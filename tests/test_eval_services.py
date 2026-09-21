from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from conftest import frozen_dataset

from overbae.services.eval import chatml, evidence, normalizer, rubric_compiler
from overbae.services.eval.evaluators import (
    base,
    deterministic,
    gen_judge,
    judge,
    statistical,
    trajectory,
)
from overbae.services.eval.evaluators.base import EvalUnit
from overbae.services.eval.ranking import bradley_terry, rollup
from overbae.services.eval.runner import ReplayToolProvider, run_capability
from tests.factories import evaluator_stub


class TestNormalizer:
    def test_plain_message_list(self):
        n = normalizer.normalize_messages(
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        )
        assert n["modality"] == "single_turn"
        assert n["final_output"] == "hello"

    def test_json_string_messages_with_embedded_tool_calls(self):
        raw = json.dumps(
            [
                {"role": "user", "content": "weather?"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": json.dumps({"city": "SF"}),
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "80F"},
                {"role": "assistant", "content": "It's 80F."},
            ]
        )
        n = normalizer.normalize_messages(raw)
        assert n["modality"] == "tool_calling"
        struct = normalizer.structure_trajectory(n)
        nodes = struct["tool_graph"]["nodes"]
        assert len(nodes) == 1
        assert nodes[0]["tool"] == "get_weather"
        assert nodes[0]["arguments"] == {"city": "SF"}
        assert nodes[0]["result"] == "80F"

    def test_qwen_xml_tool_call_in_assistant_content(self):
        n = normalizer.normalize_messages(
            [
                {"role": "user", "content": "adjudicate"},
                {
                    "role": "assistant",
                    "content": (
                        "<tool_call>\n"
                        "<function=post_decision>\n"
                        "<parameter=claim_id>\n"
                        "CLM-E-0100\n"
                        "</parameter>\n"
                        "<parameter=decision>\n"
                        "escalate\n"
                        "</parameter>\n"
                        "</function>\n"
                        "</tool_call>"
                    ),
                },
            ]
        )
        assert n["modality"] == "tool_calling"
        nodes = normalizer.structure_trajectory(n)["tool_graph"]["nodes"]
        assert nodes[0]["tool"] == "post_decision"
        assert nodes[0]["arguments"] == {"claim_id": "CLM-E-0100", "decision": "escalate"}

    def test_unclosed_qwen_xml_still_parses(self):
        calls = normalizer.parse_text_tool_calls(
            "<tool_call>\n<function=post_decision>\n<parameter=decision>\nescalate\n</parameter>\n"
        )
        assert calls == [
            {"id": "xml_0", "name": "post_decision", "arguments": {"decision": "escalate"}}
        ]

    def test_normalize_spans_llm_call(self):
        span = SimpleNamespace(
            span_id="a" * 16,
            trace_id="t" * 32,
            parent_span_id=None,
            span_type="llm_call",
            start_time_ns=1,
            status_code=0,
            name="chat",
            attributes={
                "overmind.input.data": json.dumps([{"role": "user", "content": "q"}]),
                "overmind.output.data": json.dumps([{"role": "assistant", "content": "a"}]),
                "genai.model": "gpt-5-mini",
                "genai.cost": 0.001,
            },
        )
        n = normalizer.normalize_spans([span])
        assert n["final_output"] == "a"
        assert n["metadata"]["model"] == "gpt-5-mini"

    def test_tool_span_enriches_graph(self):
        llm = SimpleNamespace(
            span_id="a" * 16,
            trace_id="t" * 32,
            parent_span_id=None,
            span_type="llm_call",
            start_time_ns=1,
            status_code=0,
            name="chat",
            attributes={
                "overmind.input.data": json.dumps([{"role": "user", "content": "q"}]),
                "overmind.output.data": json.dumps(
                    [
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "search", "arguments": "{}"},
                                }
                            ],
                        }
                    ]
                ),
            },
        )
        tool = SimpleNamespace(
            span_id="b" * 16,
            trace_id="t" * 32,
            parent_span_id="a" * 16,
            span_type="tool_call",
            start_time_ns=2,
            status_code=0,
            name="search",
            attributes={"tool.name": "search", "overmind.output.data": "RESULT"},
        )
        n = normalizer.normalize_spans([llm, tool])
        struct = normalizer.structure_trajectory(n)
        nodes = struct["tool_graph"]["nodes"]
        assert nodes[0]["result"] == "RESULT"

    def test_size_guard_truncates(self):
        big = "x" * 200_000
        n = normalizer.normalize_messages([{"role": "user", "content": big}])
        assert n["metadata"]["truncated"] is True

    def test_final_output_never_returns_tool_envelope(self):
        # final_output stays empty so the no_final_output degradation rule can trip.
        msgs = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": '{"rows": 100}'},
        ]
        n = normalizer.normalize_messages(msgs)
        assert n["final_output"] == ""

    def test_final_output_falls_back_to_terminal_tool_call_arguments(self):
        # Structured-output agents deliver through the terminal call's arguments, not text.
        msgs = [
            {"role": "user", "content": "decide"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "emit_portfolio_decision",
                            "arguments": json.dumps({"action": "buy", "ticker": "ACME"}),
                        },
                    }
                ],
            },
        ]
        n = normalizer.normalize_messages(msgs)
        assert json.loads(n["final_output"]) == {"action": "buy", "ticker": "ACME"}

    def test_final_output_returns_assistant_answer_after_tools(self):
        msgs = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": '{"rows": 100}'},
            {"role": "assistant", "content": "There are 100 rows."},
        ]
        n = normalizer.normalize_messages(msgs)
        assert n["final_output"] == "There are 100 rows."


class TestReplayToolProvider:
    def test_exact_match(self):
        p = ReplayToolProvider(
            recorded_calls=[{"name": "s", "arguments": {"q": "x"}, "result": "ok"}]
        )
        r = p.execute("s", {"q": "x"})
        assert r.matched and r.content == "ok"

    def test_subset_fuzzy_match(self):
        p = ReplayToolProvider(
            recorded_calls=[{"name": "s", "arguments": {"q": "x", "k": 1}, "result": "ok"}]
        )
        r = p.execute("s", {"q": "x"})
        assert r.matched
        assert p.fuzzy_hits == 1

    def test_miss(self):
        p = ReplayToolProvider(recorded_calls=[])
        r = p.execute("s", {"q": "x"})
        assert not r.matched
        assert p.misses == 1

    def test_idempotent_reread_reserves_for_readonly_tool(self):
        p = ReplayToolProvider(
            recorded_calls=[{"name": "read", "arguments": {"path": "/a"}, "result": "contents"}]
        )
        first = p.execute("read", {"path": "/a"})
        assert first.matched and first.content == "contents"
        for _ in range(3):
            r = p.execute("read", {"path": "/a"})
            assert r.matched and r.content == "contents"
        assert p.misses == 0
        assert p.fuzzy_hits == 0

    def test_state_changing_tool_consumes_once(self):
        # A mutation is never re-served: an extra call must surface as a divergence.
        p = ReplayToolProvider(
            recorded_calls=[{"name": "create_file", "arguments": {"path": "/a"}, "result": "ok"}]
        )
        assert p.execute("create_file", {"path": "/a"}).content == "ok"
        second = p.execute("create_file", {"path": "/a"})
        assert not second.matched
        assert p.misses == 1

    def test_idempotent_reread_preferred_over_fuzzy_other_slot(self):
        p = ReplayToolProvider(
            recorded_calls=[
                {"name": "read", "arguments": {"path": "/a"}, "result": "A"},
                {"name": "read", "arguments": {"path": "/b"}, "result": "B"},
            ]
        )
        assert p.execute("read", {"path": "/a"}).content == "A"
        repeat = p.execute("read", {"path": "/a"})
        assert repeat.content == "A"
        assert p.fuzzy_hits == 0
        assert p.execute("read", {"path": "/b"}).content == "B"


class TestResolveMaxSteps:
    def test_derives_from_recorded_tool_calls_with_headroom(self):
        from overbae.services.eval.runner import _DEFAULT_MAX_STEPS, resolve_max_steps

        assert resolve_max_steps(recorded_tool_calls=40) == 65  # ceil(40*1.5) + 5
        assert resolve_max_steps(recorded_tool_calls=40) > _DEFAULT_MAX_STEPS

    def test_floors_at_default_for_shallow_traces(self):
        from overbae.services.eval.runner import _DEFAULT_MAX_STEPS, resolve_max_steps

        assert resolve_max_steps(recorded_tool_calls=0) == _DEFAULT_MAX_STEPS
        assert resolve_max_steps(recorded_tool_calls=1) == _DEFAULT_MAX_STEPS

    def test_ceiling_caps_runaway(self):
        from overbae.services.eval.runner import _MAX_STEPS_CEILING, resolve_max_steps

        assert resolve_max_steps(recorded_tool_calls=10_000) == _MAX_STEPS_CEILING

    def test_override_wins_but_is_clamped(self):
        from overbae.services.eval.runner import _MAX_STEPS_CEILING, resolve_max_steps

        assert resolve_max_steps(recorded_tool_calls=40, override=20) == 20
        assert resolve_max_steps(recorded_tool_calls=2, override=500) == _MAX_STEPS_CEILING

    def test_invalid_or_nonpositive_override_ignored(self):
        from overbae.services.eval.runner import resolve_max_steps

        assert resolve_max_steps(recorded_tool_calls=40, override="oops") == 65
        assert resolve_max_steps(recorded_tool_calls=40, override=0) == 65
        assert resolve_max_steps(recorded_tool_calls=40, override=-3) == 65


class TestRunAgentBudgetEdge:
    def _provider(self):
        return ReplayToolProvider(recorded_calls=[], tool_defs=[{"name": "read"}])

    def test_divergent_loop_forced_to_final_answer_at_cap(self, monkeypatch):
        # On the final turn the loop drops the tools and asks for a final answer.
        def fake_call_llm(*_args, **kwargs):
            if kwargs.get("tools"):
                return (json.dumps({"tool_calls": [{"name": "read", "arguments": {}}]}), {})
            return ("FORCED FINAL ANSWER", {})

        monkeypatch.setattr("overbae.services.eval.runner.call_llm", fake_call_llm)
        result = run_capability(
            input_messages=[{"role": "user", "content": "do it"}],
            tool_provider=self._provider(),
            max_steps=3,
        )
        assert result.output_messages[-1]["role"] == "assistant"
        assert result.output_messages[-1]["content"] == "FORCED FINAL ANSWER"
        # The synthetic nudge stays in the live conversation, never the returned tail.
        assert all(m.get("role") != "user" for m in result.output_messages)

    def test_well_behaved_replay_not_forced(self, monkeypatch):
        def fake_call_llm(*_args, **_kwargs):
            return ("done", {})

        monkeypatch.setattr("overbae.services.eval.runner.call_llm", fake_call_llm)
        result = run_capability(
            input_messages=[{"role": "user", "content": "q"}],
            tool_provider=self._provider(),
            max_steps=12,
        )
        assert result.steps == 1
        assert result.output_messages == [{"role": "assistant", "content": "done"}]

    def test_qwen_xml_tool_call_continues_the_loop(self, monkeypatch):
        xml = (
            "<tool_call>\n"
            "<function=post_decision>\n"
            "<parameter=claim_id>\nCLM-E-0100\n</parameter>\n"
            "<parameter=decision>\nescalate\n</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        turns = iter([(xml, {}), ('{"decision": "escalate"}', {})])

        def fake_call_llm(*_args, **_kwargs):
            return next(turns)

        monkeypatch.setattr("overbae.services.eval.runner.call_llm", fake_call_llm)
        provider = ReplayToolProvider(
            recorded_calls=[
                {
                    "name": "post_decision",
                    "arguments": {"claim_id": "CLM-E-0100", "decision": "escalate"},
                    "result": "ok",
                }
            ],
            tool_defs=[{"name": "post_decision"}],
        )
        result = run_capability(
            input_messages=[{"role": "user", "content": "adjudicate"}],
            tool_provider=provider,
            max_steps=6,
        )
        assert result.output_messages[-1]["content"] == '{"decision": "escalate"}'
        assert any(
            m.get("role") == "tool" and m.get("name") == "post_decision"
            for m in result.output_messages
        )

    def test_from_structured(self):
        struct = {"tool_graph": {"nodes": [{"tool": "s", "arguments": {"q": "x"}, "result": "ok"}]}}
        p = ReplayToolProvider.from_structured(
            struct, [{"name": "s", "description": "", "parameters": {}}]
        )
        assert p.execute("s", {"q": "x"}).content == "ok"
        assert p.tool_definitions()[0]["function"]["name"] == "s"

    def test_illegal_tool_names_sanitized_and_replay_still_matches(self):
        # The provider rejects any name outside ^[a-zA-Z0-9_-]{1,128}$.
        struct = {
            "tool_graph": {
                "nodes": [
                    {"tool": "mcp.call_tool", "arguments": {"q": "x"}, "result": "r1"},
                    {"tool": "read file", "arguments": {"p": "/tmp"}, "result": "r2"},
                    {"tool": "a:b", "arguments": {}, "result": "r3"},
                ]
            }
        }
        defs = [
            {"name": "mcp.call_tool", "description": "", "parameters": {}},
            {"name": "read file", "description": "", "parameters": {}},
            {"name": "a:b", "description": "", "parameters": {}},
        ]
        p = ReplayToolProvider.from_structured(struct, defs)

        advertised = [t["function"]["name"] for t in p.tool_definitions()]
        assert advertised == ["mcp_call_tool", "read_file", "a_b"]
        for name in advertised:
            assert re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", name)

        assert p.execute("mcp_call_tool", {"q": "x"}).content == "r1"
        assert p.execute("read_file", {"p": "/tmp"}).content == "r2"
        assert p.execute("a_b", {}).content == "r3"

    def test_original_name_preserved_for_evaluator_visibility(self):
        struct = {
            "tool_graph": {"nodes": [{"tool": "mcp.call_tool", "arguments": {}, "result": "r"}]}
        }
        p = ReplayToolProvider.from_structured(struct, [])
        assert p.recorded_calls[0]["original_name"] == "mcp.call_tool"
        assert p.recorded_calls[0]["name"] == "mcp_call_tool"

    def test_original_name_maps_advertised_alias_back(self):
        p = ReplayToolProvider(
            tool_defs=[{"name": "run_clustering.py", "description": "", "parameters": {}}]
        )
        assert p.tool_definitions()[0]["function"]["name"] == "run_clustering_py"
        assert p.original_name("run_clustering_py") == "run_clustering.py"
        # unknown / model-invented tools pass through unchanged
        assert p.original_name("head") == "head"

    def test_restore_original_tool_names_in_produced_output(self):
        from overbae.services.eval.runner import _restore_original_tool_names

        p = ReplayToolProvider(
            tool_defs=[{"name": "run_clustering.py", "description": "", "parameters": {}}]
        )
        produced = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "run_clustering_py", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "name": "run_clustering_py", "content": "ok"},
        ]
        restored = _restore_original_tool_names(produced, p)
        assert restored[0]["tool_calls"][0]["function"]["name"] == "run_clustering.py"
        assert restored[1]["name"] == "run_clustering.py"
        # input is not mutated in place
        assert produced[0]["tool_calls"][0]["function"]["name"] == "run_clustering_py"


class TestSanitizeToolName:
    def test_replaces_illegal_chars(self):
        assert chatml.sanitize_tool_name("mcp.call_tool") == "mcp_call_tool"
        assert chatml.sanitize_tool_name("read file") == "read_file"
        assert chatml.sanitize_tool_name("a:b/c") == "a_b_c"

    def test_already_valid_unchanged(self):
        assert chatml.sanitize_tool_name("get_weather-2") == "get_weather-2"

    def test_truncates_to_128(self):
        out = chatml.sanitize_tool_name("x" * 200)
        assert len(out) == 128

    def test_empty_falls_back(self):
        assert chatml.sanitize_tool_name("") == "tool"
        assert chatml.sanitize_tool_name("   ") == "tool"
        assert chatml.sanitize_tool_name(None) == "tool"


class TestMapChoice:
    def test_configured_choices_are_authoritative(self):
        ev = evaluator_stub(
            score_type="categorical",
            choices=[{"label": "good", "value": 0.9}, {"label": "bad", "value": 0.1}],
        )
        assert base.map_choice("good", ev) == (0.9, None)
        # A configured label that collides with a canonical token still wins.
        ev2 = evaluator_stub(score_type="categorical", choices=[{"label": "pass", "value": 0.25}])
        assert base.map_choice("pass", ev2) == (0.25, None)

    def test_pass_threshold_never_produces_passed_for_categorical(self):
        # ``passed`` exists only for boolean evaluators, threshold or not.
        ev = evaluator_stub(
            score_type="categorical",
            pass_threshold=0.5,
            choices=[{"label": "good", "value": 0.9}, {"label": "bad", "value": 0.1}],
        )
        assert base.map_choice("good", ev) == (0.9, None)
        assert base.map_choice("bad", ev) == (0.1, None)

    def test_canonical_fallback_pass_family(self):
        ev = evaluator_stub(score_type="categorical", choices=[])
        for label in ("pass", "PASS", "Pass.", " yes ", "true", "Correct", "full credit"):
            assert base.map_choice(label, ev) == (1.0, None), label

    def test_canonical_fallback_fail_family(self):
        ev = evaluator_stub(score_type="categorical", choices=[])
        for label in ("fail", "FAIL", "no", "false", "Incorrect", "failed"):
            assert base.map_choice(label, ev) == (0.0, None), label

    def test_canonical_fallback_partial_is_not_a_hard_pass(self):
        ev = evaluator_stub(score_type="categorical", choices=[])
        assert base.map_choice("partial", ev) == (0.5, None)
        assert base.map_choice("partial credit", ev) == (0.5, None)

    def test_canonical_fallback_ignores_pass_threshold(self):
        ev = evaluator_stub(score_type="categorical", choices=[], pass_threshold=0.7)
        assert base.map_choice("pass", ev) == (1.0, None)
        assert base.map_choice("fail", ev) == (0.0, None)
        assert base.map_choice("partial", ev) == (0.5, None)

    def test_unmappable_label_still_abstains(self):
        ev = evaluator_stub(score_type="categorical", choices=[])
        assert base.map_choice("frobnicate", ev) == (None, None)
        assert base.map_choice("", ev) == (None, None)

    def test_fallback_used_when_configured_choice_does_not_match(self):
        ev = evaluator_stub(
            score_type="categorical", choices=[{"label": "excellent", "value": 1.0}]
        )
        assert base.map_choice("pass", ev) == (1.0, None)


class TestJsonPath:
    def test_index_and_descent(self):
        obj = {"messages": [{"content": "a"}, {"content": "b"}]}
        assert base.resolve_jsonpath(obj, "$.messages[-1].content") == ["b"]
        assert set(base.resolve_jsonpath(obj, "$..content")) == {"a", "b"}

    def test_wildcard(self):
        obj = {"items": [{"x": 1}, {"x": 2}]}
        assert base.resolve_jsonpath(obj, "$.items[*].x") == [1, 2]

    def test_resolve_variables(self):
        unit = EvalUnit(
            trajectory={
                "final_output": "Paris",
                "messages": [
                    {"role": "user", "content": "capital?"},
                    {"role": "assistant", "content": "Paris"},
                ],
            },
            expected="Paris",
        )
        mapping = [
            {"var": "input", "source": "input"},
            {"var": "output", "source": "output"},
            {"var": "reference", "source": "reference"},
        ]
        out = base.resolve_variables(unit, mapping)
        assert out == {"input": "capital?", "output": "Paris", "reference": "Paris"}

    def test_resolve_variables_parses_json_string_for_jsonpath(self):
        # Structured final_output is stored as a JSON string; a jsonpath must
        # traverse it after parsing.
        unit = EvalUnit(
            trajectory={
                "final_output": '{"status": "finished", "recommendations": ["a", "b"]}',
                "messages": [],
            }
        )
        mapping = [
            {"var": "recs", "source": "final_output", "jsonpath": "$.recommendations"},
            {"var": "status", "source": "final_output", "jsonpath": "$.status"},
        ]
        out = base.resolve_variables(unit, mapping)
        assert out["status"] == "finished"
        assert "a" in out["recs"] and "b" in out["recs"]

    def test_resolve_variables_plain_text_with_jsonpath_stays_empty(self):
        unit = EvalUnit(trajectory={"final_output": "just prose", "messages": []})
        out = base.resolve_variables(
            unit, [{"var": "x", "source": "final_output", "jsonpath": "$.foo"}]
        )
        assert out["x"] in ("", "[]")  # no match → empty, never raises


class TestAdaptiveResolution:
    _WORKSHOP_FIELDS = {
        "summary": "object",
        "recommendations": "list",
        "proposed_fixes": "list",
        "validation_issues": "list",
    }

    def _flat_workshop_unit(self) -> EvalUnit:
        # The real Data Workshop output is FLAT — no `analysis_compact` wrapper.
        final = json.dumps(
            {
                "status": "finished",
                "summary": {"rows": 15011},
                "recommendations": [{"body": "rebalance categories"}],
                "proposed_fixes": [["remove leakage"]],
            }
        )
        return EvalUnit(
            trajectory={"final_output": final, "messages": []},
            output_fields=self._WORKSHOP_FIELDS,
        )

    def test_flat_output_schema_extraction_without_wrapper(self):
        unit = self._flat_workshop_unit()
        det = base.resolve_variables_detailed(
            unit,
            [
                {
                    "var": "recommendations",
                    "source": "final_output",
                    "jsonpath": "$.analysis_compact.recommendations",
                },
                {
                    "var": "summary",
                    "source": "final_output",
                    "jsonpath": "$.analysis_compact.summary",
                },
            ],
        )
        assert det["recommendations"].strategy == "schema"
        assert "rebalance" in det["recommendations"].value
        assert det["summary"].strategy == "schema"
        assert "15011" in det["summary"].value

    def test_explicit_path_honored_when_it_resolves(self):
        unit = self._flat_workshop_unit()
        det = base.resolve_variables_detailed(
            unit,
            [{"var": "recommendations", "source": "final_output", "jsonpath": "$.recommendations"}],
        )
        assert det["recommendations"].strategy == "explicit_path"
        assert "rebalance" in det["recommendations"].value

    def test_tool_calls_resolve_to_tool_graph_despite_wrong_path(self):
        unit = EvalUnit(
            trajectory={"final_output": "", "messages": [{"role": "user", "content": "hi"}]},
            structured={"tool_graph": {"nodes": [{"tool": "search", "arguments": {"q": "x"}}]}},
        )
        det = base.resolve_variables_detailed(
            unit,
            [{"var": "tool_calls", "source": "trajectory", "jsonpath": "$.messages.tool_calls"}],
        )
        assert det["tool_calls"].strategy == "semantic"
        assert det["tool_calls"].shape == "tool_calls"
        assert "search" in det["tool_calls"].value

    def test_plain_text_exact_match_shape(self):
        unit = EvalUnit(trajectory={"final_output": "Paris", "messages": []})
        det = base.resolve_variables_detailed(unit, [{"var": "output", "source": "output"}])
        assert det["output"].value == "Paris"
        assert det["output"].shape == "text"

    def test_domain_dict_nested_key_search(self):
        unit = EvalUnit(
            trajectory={
                "final_output": json.dumps(
                    {"result": {"analysis": {"recommendations": ["x", "y"]}}}
                ),
                "messages": [],
            }
        )
        det = base.resolve_variables_detailed(unit, [{"var": "recommendations"}])
        assert det["recommendations"].strategy in ("semantic", "semantic_ambiguous")
        assert "x" in det["recommendations"].value

    def test_absent_evidence_stays_absent(self):
        unit = EvalUnit(
            trajectory={"final_output": json.dumps({"status": "finished"}), "messages": []},
            output_fields=self._WORKSHOP_FIELDS,
        )
        det = base.resolve_variables_detailed(
            unit, [{"var": "validation_issues", "source": "final_output", "jsonpath": "$.x"}]
        )
        assert det["validation_issues"].strategy == "absent"
        assert det["validation_issues"].value == ""

    def test_specific_missing_key_does_not_dump_whole_container(self):
        unit = EvalUnit(
            trajectory={"final_output": json.dumps({"status": "ok"}), "messages": []},
            expected={"role": "assistant", "content": "some instruction text"},
        )
        det = base.resolve_variables_detailed(
            unit, [{"var": "dataset_facts", "source": "reference", "jsonpath": "$.dataset_facts"}]
        )
        assert det["dataset_facts"].strategy == "absent"

    def test_legacy_explicit_source_without_path_unchanged(self):
        unit = EvalUnit(
            trajectory={
                "final_output": "Paris",
                "messages": [{"role": "user", "content": "capital?"}],
            },
            expected="Paris",
        )
        det = base.resolve_variables_detailed(
            unit,
            [
                {"var": "input", "source": "input"},
                {"var": "output", "source": "output"},
                {"var": "reference", "source": "reference"},
            ],
        )
        assert all(rv.strategy == "explicit_source" for rv in det.values())
        assert det["input"].value == "capital?"
        assert det["reference"].value == "Paris"

    _REF_CTX = {
        "dataset_facts": {"format": "sft_messages", "volume_and_tokens": {"total_rows": 17}},
        "known_row_ids": ["sigA", "sigB", "sigC"],
        "workshop_report": {"agenda_coverage": {"uncovered_intents": ["x"]}},
        "tool_spec": [{"name": "read_dataset_artifact"}, {"name": "run_clustering"}],
        "dataset_card": {"summary": "a corpus"},
    }

    def _ctx_unit(self) -> EvalUnit:
        # Instruction-shard expected_output (no grounding fields) + collected context.
        return EvalUnit(
            trajectory={"final_output": json.dumps({"status": "ok"}), "messages": []},
            expected="please analyze the dataset and report findings",
            reference_context=self._REF_CTX,
        )

    def test_reference_vars_resolve_from_dataset_context(self):
        unit = self._ctx_unit()
        det = base.resolve_variables_detailed(
            unit,
            [
                {"var": "dataset_facts", "source": "reference", "jsonpath": "$.dataset_facts"},
                {"var": "known_row_ids", "source": "reference", "jsonpath": "$.known_row_ids"},
                {"var": "workshop_report", "source": "reference", "jsonpath": "$.workshop_report"},
                {"var": "tool_spec", "source": "reference", "jsonpath": "$.tool_spec"},
            ],
        )
        assert all(rv.strategy == "dataset_context" for rv in det.values())
        assert "sft_messages" in det["dataset_facts"].value
        assert "sigA" in det["known_row_ids"].value
        assert "uncovered_intents" in det["workshop_report"].value
        assert "read_dataset_artifact" in det["tool_spec"].value

    def test_grounding_var_synonyms_resolve_from_context(self):
        unit = self._ctx_unit()
        det = base.resolve_variables_detailed(
            unit,
            [
                {"var": "facts", "source": "expected_output", "jsonpath": "$.facts"},
                {"var": "report", "source": "reference", "jsonpath": "$.report"},
                {"var": "row_ids", "source": "ground_truth", "jsonpath": "$.row_ids"},
            ],
        )
        assert det["facts"].strategy == "dataset_context"
        assert det["report"].strategy == "dataset_context"
        assert det["row_ids"].strategy == "dataset_context"

    def test_grounding_absent_without_context_still_abstains(self):
        unit = EvalUnit(
            trajectory={"final_output": json.dumps({"status": "ok"}), "messages": []},
            expected="instruction text",
            reference_context={},
        )
        det = base.resolve_variables_detailed(
            unit, [{"var": "dataset_facts", "source": "reference", "jsonpath": "$.dataset_facts"}]
        )
        assert det["dataset_facts"].strategy == "absent"

    def test_explicit_reference_path_wins_over_context(self):
        unit = EvalUnit(
            trajectory={"final_output": "", "messages": []},
            expected={"known_row_ids": ["real1", "real2"]},
            reference_context=self._REF_CTX,
        )
        det = base.resolve_variables_detailed(
            unit, [{"var": "known_row_ids", "source": "reference", "jsonpath": "$.known_row_ids"}]
        )
        assert det["known_row_ids"].strategy == "explicit_path"
        assert "real1" in det["known_row_ids"].value

    def test_context_does_not_shadow_capability_output_field(self):
        unit = EvalUnit(
            trajectory={
                "final_output": json.dumps({"dataset_card": {"summary": "CAPABILITY PRODUCED"}}),
                "messages": [],
            },
            output_fields={"dataset_card": {}},
            reference_context=self._REF_CTX,
        )
        det = base.resolve_variables_detailed(
            unit, [{"var": "dataset_card", "source": "output", "jsonpath": "$.dataset_card"}]
        )
        assert "CAPABILITY PRODUCED" in det["dataset_card"].value
        assert det["dataset_card"].strategy != "dataset_context"

    def test_g1_jsonpath_digs_into_grounded_value(self):
        unit = self._ctx_unit()
        det = base.resolve_variables_detailed(
            unit,
            [
                {
                    "var": "dataset_facts",
                    "source": "reference",
                    "jsonpath": "$.volume_and_tokens.total_rows",
                }
            ],
        )
        rv = det["dataset_facts"]
        assert rv.strategy == "dataset_context"
        assert "17" in rv.value
        assert "sft_messages" not in rv.value  # NOT the whole blob

    def test_g1_name_mirroring_path_falls_back_to_whole_value(self):
        unit = self._ctx_unit()
        det = base.resolve_variables_detailed(
            unit, [{"var": "dataset_facts", "source": "reference", "jsonpath": "$.dataset_facts"}]
        )
        assert det["dataset_facts"].strategy == "dataset_context"
        assert "sft_messages" in det["dataset_facts"].value

    def test_g2_grounding_resolves_for_non_reference_source(self):
        unit = self._ctx_unit()
        det = base.resolve_variables_detailed(
            unit,
            [
                {"var": "known_row_ids", "source": "input"},
                {"var": "tool_spec", "source": "structured"},
            ],
        )
        assert det["known_row_ids"].strategy == "dataset_context"
        assert "sigA" in det["known_row_ids"].value
        assert det["tool_spec"].strategy == "dataset_context"
        assert "read_dataset_artifact" in det["tool_spec"].value

    def test_g2_unknown_var_not_fabricated_from_grounding(self):
        unit = self._ctx_unit()
        det = base.resolve_variables_detailed(
            unit, [{"var": "totally_made_up_thing", "source": "input"}]
        )
        rv = det["totally_made_up_thing"]
        assert rv.strategy != "dataset_context"
        assert rv.shape == "empty"

    def test_chatml_wrapped_expected_unwrapped_before_jsonpath(self):
        # A trace-derived expected_output is a ChatML envelope whose `content` is
        # stringified JSON; the path applies to the lifted, parsed content.
        unit = EvalUnit(
            trajectory={"final_output": json.dumps({"status": "ok"}), "messages": []},
            expected={
                "role": "assistant",
                "content": json.dumps({"summary": {"rows": 15000}, "files": ["a.csv"]}),
            },
        )
        det = base.resolve_variables_detailed(
            unit,
            [{"var": "parsed_summary_rows", "source": "expected", "jsonpath": "$.summary.rows"}],
        )
        rv = det["parsed_summary_rows"]
        assert rv.strategy == "explicit_path"
        assert rv.value == "15000"

    def test_chatml_wrapped_expected_message_list_unwrapped(self):
        unit = EvalUnit(
            trajectory={"final_output": "", "messages": []},
            expected=[
                {"role": "user", "content": "analyze it"},
                {"role": "assistant", "content": json.dumps({"summary": {"rows": 42}})},
            ],
        )
        det = base.resolve_variables_detailed(
            unit, [{"var": "rows", "source": "reference", "jsonpath": "$.summary.rows"}]
        )
        assert det["rows"].value == "42"

    def test_non_chatml_expected_dict_path_unaffected(self):
        # The unwrap only triggers for {role, content}-shaped bases.
        unit = EvalUnit(
            trajectory={"final_output": "", "messages": []},
            expected={"summary": {"rows": 7}},
        )
        det = base.resolve_variables_detailed(
            unit, [{"var": "rows", "source": "reference", "jsonpath": "$.summary.rows"}]
        )
        assert det["rows"].strategy == "explicit_path"
        assert det["rows"].value == "7"


class TestCalibrationMetric:
    """A model can extract as well as another and still lie about how sure it is.
    No accuracy metric shows that, so calibration is scored over the run."""

    def _value(self, pairs):
        ev = evaluator_stub(
            kind="statistical", scope="dataset", config={"metric": "calibration", "min_n": 0}
        )
        preds = [str(c) for c, _ in pairs]
        refs = [o for _, o in pairs]
        return statistical.aggregate(preds, refs, ev)

    def test_perfect_calibration_scores_one(self):
        # Always certain, always right.
        assert self._value([(1.0, 1.0)] * 5).value == 1.0

    def test_overconfidence_is_penalised(self):
        # States 1.0 on rows it got wrong.
        assert self._value([(1.0, 0.0)] * 5).value == 0.0

    def test_reference_is_a_fraction_and_only_a_perfect_row_counts_correct(self):
        # The sibling evaluator reports a per-field fraction; a partly-correct row
        # is not what "I am confident this extraction is right" claims.
        assert self._value([(1.0, 0.99)] * 4).value == 0.0
        assert self._value([(1.0, 1.0)] * 4).value == 1.0

    def test_reports_the_direction_and_size_of_the_miscalibration(self):
        draft = self._value([(0.9, 1.0), (0.9, 1.0), (0.9, 0.0), (0.9, 0.0)])
        assert "overconfident by 0.400" in draft.reasoning
        assert "Brier=" in draft.reasoning

    def test_measured_finetune_regression_is_visible(self):
        # The real iteration-2 figures: the finetune states higher confidence and
        # is right less often, which every other evaluator scored as equal.
        base = self._value([(0.952, 1.0)] * 18 + [(0.952, 0.0)] * 6)
        finetune = self._value([(0.969, 1.0)] * 17 + [(0.969, 0.0)] * 7)
        assert finetune.value < base.value

    def test_accuracy_alone_does_not_move_the_score(self):
        # The reason this is ECE and not Brier. Both arms are equally well tuned —
        # each states exactly its own hit rate — so calibration must read the same
        # even though one is far more accurate. Brier would rank the accurate one
        # higher and call it better calibrated.
        accurate = self._value([(0.9, 1.0)] * 9 + [(0.9, 0.0)])
        inaccurate = self._value([(0.5, 1.0)] * 5 + [(0.5, 0.0)] * 5)
        assert accurate.value == pytest.approx(inaccurate.value)

    def test_bins_stop_opposite_errors_cancelling(self):
        # Wildly overconfident on one group, wildly underconfident on the other.
        # A single mean gap would net to zero and call this perfect.
        draft = self._value([(0.95, 0.0)] * 10 + [(0.05, 1.0)] * 10)
        assert draft.value == pytest.approx(0.05)

    def test_reports_a_bootstrap_interval(self):
        # A run-level metric emits one number per arm, so without an interval
        # there is no way to tell a real gap from noise.
        draft = self._value([(0.9, 1.0)] * 30 + [(0.9, 0.0)] * 10)
        interval = next(s["_interval"] for s in draft.sub_scores if "_interval" in s)
        assert interval["low"] < draft.value < interval["high"]
        assert f"[{interval['low']:.4f}, {interval['high']:.4f}]" in draft.reasoning

    def test_interval_is_wider_on_less_data(self):
        few = self._value([(0.9, 1.0)] * 9 + [(0.9, 0.0)])
        many = self._value(([(0.9, 1.0)] * 9 + [(0.9, 0.0)]) * 20)

        def width(d):
            i = next(s["_interval"] for s in d.sub_scores if "_interval" in s)
            return i["high"] - i["low"]

        assert width(few) > width(many)

    def test_numeric_references_produce_no_confusion_matrix(self):
        # Regression: aggregate_run builds a confusion matrix for every statistical
        # evaluator, and the label normalizer assumed a string reference. A metric
        # whose references are numeric took a whole run's finalisation down with it.
        assert statistical.confusion_matrix(["0.9", "0.8"], [1.0, 0.0], {}) == {}

    def test_abstains_below_min_n(self):
        ev = evaluator_stub(
            kind="statistical", scope="dataset", config={"metric": "calibration", "min_n": 10}
        )
        draft = statistical.aggregate(["0.9"] * 4, [1.0] * 4, ev)
        assert draft.value is None
        assert draft.outcome == base.OUTCOME_ABSTAINED

    def test_ignores_values_that_are_not_probabilities(self):
        # A field holding something other than a 0..1 confidence must not be
        # silently folded into the metric.
        assert self._value([(1.0, 1.0), (42.0, 1.0), ("n/a", 1.0)]).value == 1.0


class TestCanonicalFieldRouting:
    """Which fields a deterministic check may claim. A plain string is an exact
    compare unless the declaration describes free prose."""

    def _kind(self, name, decl):
        from overbae.services.eval.card_compiler import canonical_field_kind

        return canonical_field_kind(name, decl)

    def test_claims_boolean_number_and_explicitly_formatted_dates(self):
        assert self._kind("isInvoice", "boolean — true only for payable invoices") == "boolean"
        assert self._kind("amount", "number|null — total amount due") == "number"
        assert self._kind("dueDate", "string|null — payment due date as YYYY-MM-DD") == "date"

    def test_ledgerline_card_routes_identifier_strings_and_leaves_prose(self):
        from overbae.services.eval.card_compiler import canonical_output_fields

        declared = {
            "amount": "number|null — total amount due as a number without currency symbols",
            "confidence": (
                "float in [0,1] — model confidence in the full triage + extraction decision"
            ),
            "currency": "string|null — ISO currency code (USD, GBP, EUR) when known",
            "dueDate": "string|null — payment due date as YYYY-MM-DD when known",
            "invoiceNumber": "string|null — invoice or bill reference number when present",
            "isInvoice": (
                "boolean — true only for unpaid/payable invoices, bills, or statements "
                "with amount owed"
            ),
            "summary": "string|null — brief description of the invoice or email",
            "vendor": "string|null — vendor or sender name when known",
        }
        kinds = {name: self._kind(name, decl) for name, decl in declared.items()}
        assert kinds == {
            "amount": "number",
            "confidence": None,
            "currency": "string",
            "dueDate": "date",
            "invoiceNumber": "string",
            "isInvoice": "boolean",
            "summary": None,
            "vendor": "string",
        }
        assert canonical_output_fields({"output_fields": declared}) == [
            {"name": "amount", "kind": "number"},
            {"name": "currency", "kind": "string"},
            {"name": "dueDate", "kind": "date"},
            {"name": "invoiceNumber", "kind": "string"},
            {"name": "isInvoice", "kind": "boolean"},
            {"name": "vendor", "kind": "string"},
        ]

    def test_prose_ness_is_read_from_the_declaration_not_the_name(self):
        assert self._kind("summary", "string|null — vendor or sender name when known") == "string"
        assert self._kind("vendor", "string|null — brief description of the invoice") is None

    def test_markdown_report_declaration_is_prose(self):
        assert (
            self._kind("report", "string — markdown report body returned by write_report()") is None
        )
        assert self._kind("answer", "string — long-form free-form analysis") is None
        assert self._kind("invoiceNumber", "string — invoice or bill reference") == "string"

    def test_claims_an_explicit_enum(self):
        assert self._kind("verdict", "enum: pass|fail|defer — closed outcome") == "enum"
        assert self._kind("status", "string — enum: open|closed") == "enum"

    def test_json_schema_enum_array_is_a_declared_closed_set(self):
        from overbae.services.eval.card_compiler import canonical_output_fields

        card = {
            "output_fields": {"decision": "string — outcome"},
            "output_schema": {
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": ["approve", "partial", "reject", "escalate"],
                    }
                }
            },
        }
        assert canonical_output_fields(card) == [{"name": "decision", "kind": "enum"}]

    def test_never_claims_a_self_reported_confidence(self):
        # A number with no correct value to compare a reference against.
        assert self._kind("confidence", "float in [0,1] — model confidence") is None
        assert self._kind("score", "number — certainty of the extraction") is None


class TestCanonicalFieldsCheck:
    FIELDS = [
        {"name": "isInvoice", "kind": "boolean"},
        {"name": "amount", "kind": "number"},
        {"name": "dueDate", "kind": "date"},
    ]

    def _draft(self, output, expected, input_text="the invoice email"):
        unit = EvalUnit(
            trajectory={
                "final_output": json.dumps(output),
                "messages": [{"role": "user", "content": input_text}],
            },
            structured={},
            expected=json.dumps(expected) if expected is not None else None,
        )
        ev = evaluator_stub(config={"check": "canonical_fields", "fields": self.FIELDS})
        return deterministic.evaluate(unit, ev, {})[0]

    def test_all_matching_scores_one(self):
        row = {"isInvoice": True, "amount": 4500, "dueDate": "2026-08-12"}
        assert self._draft(row, row).value == 1.0

    def test_score_is_the_fraction_of_fields_that_match(self):
        draft = self._draft(
            {"isInvoice": True, "amount": 99, "dueDate": "2026-08-12"},
            {"isInvoice": True, "amount": 4500, "dueDate": "2026-08-12"},
        )
        assert draft.value == pytest.approx(2 / 3)
        violated = next(s["_violated_fields"] for s in draft.sub_scores if "_violated_fields" in s)
        assert violated == ["amount"]

    def test_numeric_formatting_is_not_an_error(self):
        # The failure mode that inverted a model ranking under strict equality.
        assert self._draft({"amount": 4500.0}, {"amount": 4500}).value == 1.0
        assert self._draft({"amount": "4500.00"}, {"amount": 4500}).value == 1.0

    def test_a_null_reference_is_an_assertion_not_an_absence_of_opinion(self):
        # Measured case: on a paid receipt the golden sets amount=null and the
        # model extracts the receipt total, which does appear in the text but is
        # the wrong answer for "amount due". Source presence must not excuse it.
        draft = self._draft(
            {"isInvoice": False, "amount": 59.99, "dueDate": None},
            {"isInvoice": False, "amount": None, "dueDate": None},
            input_text="your receipt for 59.99, already paid",
        )
        assert draft.value == pytest.approx(2 / 3)
        violated = next(s["_violated_fields"] for s in draft.sub_scores if "_violated_fields" in s)
        assert violated == ["amount"]

    def test_declining_to_invent_a_value_is_correct(self):
        row = {"isInvoice": True, "amount": 4500, "dueDate": None}
        assert self._draft(row, {"isInvoice": True, "amount": 4500}).value == 1.0

    def test_abstains_without_a_reference(self):
        draft = self._draft({"isInvoice": True}, None)
        assert draft.value is None

    def test_unparseable_output_abstains(self):
        unit = EvalUnit(trajectory={"final_output": "not json"}, structured={}, expected="{}")
        ev = evaluator_stub(config={"check": "canonical_fields", "fields": self.FIELDS})
        draft = deterministic.evaluate(unit, ev, {})[0]
        assert draft.value is None
        assert draft.outcome == "abstained"


class TestCanonicalStringField:
    def _draft(self, output, expected):
        unit = EvalUnit(
            trajectory={"final_output": json.dumps(output), "messages": []},
            structured={},
            expected=json.dumps(expected),
        )
        ev = evaluator_stub(
            config={"check": "canonical_fields", "fields": [{"name": "vendor", "kind": "string"}]}
        )
        return deterministic.evaluate(unit, ev, {})[0]

    def test_exact_match_passes(self):
        assert self._draft({"vendor": "Acme Corp"}, {"vendor": "Acme Corp"}).value == 1.0

    def test_case_and_whitespace_difference_passes(self):
        assert self._draft({"vendor": "  ACME CORP "}, {"vendor": "acme corp"}).value == 1.0

    def test_different_value_fails(self):
        assert self._draft({"vendor": "Acme Corp"}, {"vendor": "Globex"}).value == 0.0

    def test_both_null_passes(self):
        assert self._draft({"vendor": None}, {"vendor": None}).value == 1.0

    def test_one_null_fails(self):
        assert self._draft({"vendor": "Acme"}, {"vendor": None}).value == 0.0
        assert self._draft({"vendor": None}, {"vendor": "Acme"}).value == 0.0

    def test_trailing_period_is_not_a_mismatch(self):
        assert self._draft({"vendor": "Acme Inc"}, {"vendor": "Acme Inc."}).value == 1.0

    def test_interior_hyphen_stays_significant(self):
        assert self._draft({"vendor": "INV-123"}, {"vendor": "INV123"}).value == 0.0


class TestDeterministic:
    def _unit(self, **traj):
        return EvalUnit(trajectory=traj, structured={}, expected=traj.pop("_expected", None))

    def test_exact_match(self):
        u = EvalUnit(trajectory={"final_output": "yes"}, expected="yes")
        ev = evaluator_stub(config={"check": "exact_match"})
        assert deterministic.evaluate(u, ev, {})[0].value == 1.0

    def test_regex(self):
        u = EvalUnit(trajectory={"final_output": "order #1234"})
        ev = evaluator_stub(config={"check": "regex", "pattern": r"#\d+"})
        assert deterministic.evaluate(u, ev, {})[0].value == 1.0

    def test_json_schema_valid(self):
        u = EvalUnit(trajectory={"final_output": '{"a": 1}'})
        ev = evaluator_stub(config={"check": "json_schema_valid", "required_keys": ["a"]})
        assert deterministic.evaluate(u, ev, {})[0].value == 1.0

    def test_json_schema_valid_abstains_on_explicit_empty_deliverable(self):
        ev = evaluator_stub(config={"check": "json_schema_valid", "required_keys": ["a"]})
        for empty in ("[]", "{}", "null"):
            draft = deterministic.evaluate(EvalUnit(trajectory={"final_output": empty}), ev, {})[0]
            assert draft.value is None, empty
            assert draft.outcome == "abstained"
            assert "rejection terminal" in draft.reasoning
        blank = deterministic.evaluate(EvalUnit(trajectory={"final_output": ""}), ev, {})[0]
        assert blank.value == 0.0  # broken record, not a rejection decision

    def test_json_schema_valid_accepts_fenced_block(self):
        u = EvalUnit(trajectory={"final_output": '```json\n{"a": 1}\n```'})
        ev = evaluator_stub(config={"check": "json_schema_valid", "required_keys": ["a"]})
        assert deterministic.evaluate(u, ev, {})[0].value == 1.0

    def test_json_schema_valid_accepts_prose_wrapped(self):
        u = EvalUnit(
            trajectory={"final_output": 'Here is the result: {"a": 1, "b": [2, 3]}. Done.'}
        )
        ev = evaluator_stub(config={"check": "json_schema_valid"})
        assert deterministic.evaluate(u, ev, {})[0].value == 1.0

    def test_json_schema_valid_still_rejects_invalid(self):
        u = EvalUnit(trajectory={"final_output": "not json at all {oops"})
        ev = evaluator_stub(config={"check": "json_schema_valid"})
        assert deterministic.evaluate(u, ev, {})[0].value == 0.0

    def test_json_schema_valid_unparseable_output_names_all_required_keys(self):
        u = EvalUnit(trajectory={"final_output": "traceback: boom"})
        ev = evaluator_stub(config={"check": "json_schema_valid", "required_keys": ["a", "b"]})
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value == 0.0
        assert [s["_violated_fields"] for s in draft.sub_scores if "_violated_fields" in s] == [
            ["a", "b"]
        ]

    def test_json_schema_valid_emits_typed_violated_fields(self):
        # Missing keys are named structurally so the context-graph violates edges
        # never re-parse the reasoning string.
        u = EvalUnit(trajectory={"final_output": '{"a": 1}'})
        ev = evaluator_stub(config={"check": "json_schema_valid", "required_keys": ["a", "b", "c"]})
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value == 0.0
        violated = [s["_violated_fields"] for s in draft.sub_scores if "_violated_fields" in s]
        assert violated == [["b", "c"]]
        ok = EvalUnit(trajectory={"final_output": '{"a": 1, "b": 2, "c": 3}'})
        ok_draft = deterministic.evaluate(ok, ev, {})[0]
        assert not [s for s in ok_draft.sub_scores if "_violated_fields" in s]

    def test_json_schema_valid_tolerates_one_envelope_level(self):
        # The card declares item-level keys; the runtime wraps them one level down.
        wrapped = '{"items": [{"kind": "x", "text": "t"}, {"kind": "y", "text": "u"}]}'
        u = EvalUnit(trajectory={"final_output": wrapped})
        ev = evaluator_stub(
            config={"check": "json_schema_valid", "required_keys": ["kind", "text"]}
        )
        assert deterministic.evaluate(u, ev, {})[0].value == 1.0
        partial = '{"items": [{"kind": "x", "text": "t"}, {"kind": "y"}]}'
        draft = deterministic.evaluate(EvalUnit(trajectory={"final_output": partial}), ev, {})[0]
        assert draft.value == 0.0
        violated = [s["_violated_fields"] for s in draft.sub_scores if "_violated_fields" in s]
        assert violated == [["text"]]
        nested = '{"result": {"kind": "x", "text": "t"}}'
        assert (
            deterministic.evaluate(EvalUnit(trajectory={"final_output": nested}), ev, {})[0].value
            == 1.0
        )

    def test_json_schema_valid_abstains_off_surface_key_vs_golden(self):
        # `isInvoice` lives on the LLM surface but the golden (harness deliverable)
        # drops it — a key the graded surface cannot carry must abstain, not fail.
        record = '{"vendor": "Acme", "amount": 10, "emailId": "e1"}'
        u = EvalUnit(trajectory={"final_output": record}, expected=json.loads(record))
        ev = evaluator_stub(
            config={
                "check": "json_schema_valid",
                "required_keys": ["isInvoice", "vendor", "amount"],
            }
        )
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value is None
        assert draft.outcome == "abstained"
        abstained = [s["_abstained_fields"] for s in draft.sub_scores if "_abstained_fields" in s]
        assert abstained == [["isInvoice"]]

    def _conformance_ev(self, fields):
        return evaluator_stub(config={"check": "schema_field_conformance", "fields": fields})

    def test_schema_field_conformance_all_present_fields_conform(self):
        u = EvalUnit(trajectory={"final_output": '{"confidence": 0.9, "dueDate": "2026-08-01"}'})
        ev = self._conformance_ev(
            {
                "confidence": {"type": "number", "min": 0, "max": 1},
                "dueDate": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
                "vendor": {"type": "string"},  # absent → skipped, not failed
            }
        )
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value == 1.0
        verdicts = {
            s["_field_conformance"]["field"]: s["_field_conformance"]["verdict"]
            for s in draft.sub_scores
            if "_field_conformance" in s
        }
        assert verdicts == {"confidence": "pass", "dueDate": "pass", "vendor": "absent"}

    def test_schema_field_conformance_violations_score_fraction(self):
        u = EvalUnit(
            trajectory={"final_output": '{"confidence": 1.4, "source": "gmail", "amount": "ten"}'}
        )
        ev = self._conformance_ev(
            {
                "confidence": {"type": "number", "min": 0, "max": 1},
                "source": {"enum": ["gmail", "demo"]},
                "amount": {"type": "number", "nullable": True},
            }
        )
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value == pytest.approx(1 / 3)
        violated = [s["_violated_fields"] for s in draft.sub_scores if "_violated_fields" in s]
        assert violated == [["confidence", "amount"]]

    def test_schema_field_conformance_nullable_rules(self):
        ev = self._conformance_ev(
            {
                "dueDate": {"type": "string", "nullable": True},
                "vendor": {"type": "string"},
            }
        )
        u = EvalUnit(trajectory={"final_output": '{"dueDate": null, "vendor": null}'})
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value == 0.5  # dueDate null ok, vendor null not declared nullable

    def test_schema_field_conformance_unparseable_output_scores_zero(self):
        u = EvalUnit(trajectory={"final_output": "traceback: boom"})
        ev = self._conformance_ev({"confidence": {"type": "number"}})
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value == 0.0
        violated = [s["_violated_fields"] for s in draft.sub_scores if "_violated_fields" in s]
        assert violated == [["confidence"]]

    def test_schema_field_conformance_empty_output_abstains(self):
        u = EvalUnit(trajectory={"final_output": "   "})
        ev = self._conformance_ev({"confidence": {"type": "number"}})
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value is None
        assert draft.outcome == "abstained"

    def test_schema_field_conformance_no_declared_field_present_abstains(self):
        u = EvalUnit(trajectory={"final_output": '{"other": 1}'})
        ev = self._conformance_ev({"confidence": {"type": "number"}})
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value is None
        assert draft.outcome == "abstained"

    def test_schema_field_conformance_validates_list_of_records(self):
        records = '[{"confidence": 0.9}, {"confidence": 2.0}]'
        u = EvalUnit(trajectory={"final_output": records})
        ev = self._conformance_ev({"confidence": {"type": "number", "min": 0, "max": 1}})
        draft = deterministic.evaluate(u, ev, {})[0]
        # Any record violating fails the field.
        assert draft.value == 0.0

    def test_json_schema_valid_still_fails_genuine_omission_vs_golden(self):
        u = EvalUnit(
            trajectory={"final_output": '{"vendor": "Acme"}'},
            expected={"vendor": "Acme", "amount": 10},
        )
        ev = evaluator_stub(
            config={"check": "json_schema_valid", "required_keys": ["vendor", "amount"]}
        )
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value == 0.0
        violated = [s["_violated_fields"] for s in draft.sub_scores if "_violated_fields" in s]
        assert violated == [["amount"]]

    def test_json_schema_valid_live_abstains_when_output_is_other_surface(self):
        # Undeclared keys (harness markers) mark the record as another surface, so
        # a missing contract key is a mismatch rather than a failure.
        record = '{"vendor": "Acme", "emailId": "e1", "gmailUrl": "#"}'
        u = EvalUnit(trajectory={"final_output": record})
        ev = evaluator_stub(
            config={"check": "json_schema_valid", "required_keys": ["isInvoice", "vendor"]}
        )
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value is None
        assert draft.outcome == "abstained"

    def test_json_schema_valid_live_fails_when_no_undeclared_keys(self):
        u = EvalUnit(trajectory={"final_output": '{"vendor": "Acme"}'})
        ev = evaluator_stub(
            config={"check": "json_schema_valid", "required_keys": ["isInvoice", "vendor"]}
        )
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value == 0.0

    def test_tool_selection_perfect_overlap(self):
        u = EvalUnit(structured={"tool_graph": {"nodes": [{"tool": "search"}]}})
        ev = evaluator_stub(config={"check": "tool_selection", "expected_tools": ["search"]})
        assert deterministic.evaluate(u, ev, {})[0].value == 1.0

    def test_tool_selection_skipping_optional_declared_tools_is_not_a_miss(self):
        u = EvalUnit(structured={"tool_graph": {"nodes": [{"tool": "search"}]}})
        ev = evaluator_stub(
            config={"check": "tool_selection", "expected_tools": ["search", "fetch"]}
        )
        assert deterministic.evaluate(u, ev, {})[0].value == 1.0

    def test_tool_selection_undeclared_tool_lowers_precision(self):
        u = EvalUnit(structured={"tool_graph": {"nodes": [{"tool": "search"}, {"tool": "shell"}]}})
        ev = evaluator_stub(config={"check": "tool_selection", "expected_tools": ["search"]})
        assert deterministic.evaluate(u, ev, {})[0].value == 0.5

    def test_tool_selection_abstains_on_empty_trajectory(self):
        # "Made no calls" must not be conflated with "made the wrong calls".
        u = EvalUnit(structured={"tool_graph": {"nodes": []}})
        ev = evaluator_stub(config={"check": "tool_selection", "expected_tools": ["read_server"]})
        assert deterministic.evaluate(u, ev, {})[0].value is None

    def test_tool_selection_scores_zero_on_wrong_tools(self):
        u = EvalUnit(structured={"tool_graph": {"nodes": [{"tool": "read"}, {"tool": "glob"}]}})
        ev = evaluator_stub(config={"check": "tool_selection", "expected_tools": ["read_server"]})
        assert deterministic.evaluate(u, ev, {})[0].value == 0.0

    def test_exact_match_targets_output_field_on_structured_output(self):
        final = json.dumps({"status": "ok", "label": "positive"})
        u = EvalUnit(trajectory={"final_output": final}, expected="positive")
        ev = evaluator_stub(config={"check": "exact_match", "output_field": "label"})
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value == 1.0

    def test_exact_match_targeted_field_absent_abstains(self):
        u = EvalUnit(trajectory={"final_output": json.dumps({"status": "ok"})}, expected="x")
        ev = evaluator_stub(config={"check": "exact_match", "output_field": "label"})
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value is None

    def test_output_jsonpath_targeting(self):
        final = json.dumps({"wrap": {"answer": "42"}})
        u = EvalUnit(trajectory={"final_output": final}, expected="42")
        ev = evaluator_stub(config={"check": "exact_match", "output_jsonpath": "$.wrap.answer"})
        assert deterministic.evaluate(u, ev, {})[0].value == 1.0

    def test_whole_output_default_unchanged_and_records_resolution(self):
        u = EvalUnit(trajectory={"final_output": "yes"}, expected="yes")
        ev = evaluator_stub(config={"check": "exact_match"})
        draft = deterministic.evaluate(u, ev, {})[0]
        assert draft.value == 1.0
        resolution = next((s["_resolution"] for s in draft.sub_scores if "_resolution" in s), None)
        assert resolution is not None
        assert resolution["output"]["strategy"] == "explicit_source"


class TestCardConstraints:
    def _unit(self, tools=(), output="", messages=None, calls=None):
        trajectory = {"final_output": output}
        if messages is not None:
            trajectory["messages"] = messages
        nodes = calls if calls is not None else [{"tool": t} for t in tools]
        return EvalUnit(
            trajectory=trajectory,
            structured={"tool_graph": {"nodes": nodes}},
        )

    @staticmethod
    def _round_messages(*rounds: list[str]):
        messages = []
        for tools in rounds:
            messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": t}} for t in tools],
                }
            )
            for t in tools:
                messages.append({"role": "tool", "name": t, "content": "{}"})
        return messages

    def _ev(self, constraints, declared=("fetch", "parse", "emit")):
        return evaluator_stub(
            config={
                "check": "card_constraints",
                "constraints": constraints,
                "declared_tools": list(declared),
            }
        )

    @staticmethod
    def _verdicts(draft):
        return {
            s["_constraint"]["id"]: s["_constraint"]["verdict"]
            for s in draft.sub_scores
            if "_constraint" in s
        }

    def test_ordering_pass_and_fail(self):
        entry = {
            "id": "c1",
            "rule": "fetch before parse",
            "type": "ordering",
            "params": {},
            "tools": ["fetch", "parse"],
        }
        ok = deterministic.evaluate(self._unit(tools=["fetch", "parse"]), self._ev([entry]), {})[0]
        assert ok.value == 1.0
        assert self._verdicts(ok) == {"c1": "pass"}
        bad = deterministic.evaluate(self._unit(tools=["parse", "fetch"]), self._ev([entry]), {})[0]
        assert bad.value == 0.0
        assert self._verdicts(bad) == {"c1": "fail"}

    def test_ordering_abstains_when_sequence_not_exercised(self):
        entry = {
            "id": "c1",
            "rule": "fetch before parse",
            "type": "ordering",
            "params": {},
            "tools": ["fetch", "parse"],
        }
        draft = deterministic.evaluate(self._unit(tools=["fetch"]), self._ev([entry]), {})[0]
        assert draft.value is None
        assert self._verdicts(draft) == {"c1": "abstain"}

    def test_budget_checks_max_calls(self):
        entry = {
            "id": "b1",
            "rule": "at most 2 calls",
            "type": "budget",
            "params": {"max_calls": 2},
            "tools": [],
        }
        ok = deterministic.evaluate(self._unit(tools=["fetch", "parse"]), self._ev([entry]), {})[0]
        assert self._verdicts(ok) == {"b1": "pass"}
        over = deterministic.evaluate(
            self._unit(tools=["fetch", "parse", "emit"]), self._ev([entry]), {}
        )[0]
        assert self._verdicts(over) == {"b1": "fail"}
        # No numeric budget param → not mechanically checkable → abstain.
        vague = {"id": "b2", "rule": "be frugal", "type": "budget", "params": {}, "tools": []}
        draft = deterministic.evaluate(self._unit(tools=["fetch"]), self._ev([vague]), {})[0]
        assert self._verdicts(draft) == {"b2": "abstain"}

    def test_tool_discipline_flags_undeclared_tools(self):
        entry = {
            "id": "d1",
            "rule": "only declared tools",
            "type": "tool_discipline",
            "params": {},
            "tools": [],
        }
        ok = deterministic.evaluate(self._unit(tools=["fetch"]), self._ev([entry]), {})[0]
        assert self._verdicts(ok) == {"d1": "pass"}
        rogue = deterministic.evaluate(self._unit(tools=["fetch", "shell"]), self._ev([entry]), {})[
            0
        ]
        assert self._verdicts(rogue) == {"d1": "fail"}
        # Canonicalization: "Language-Model.Infer" joins declared "language_model_infer".
        canon = deterministic.evaluate(
            self._unit(tools=["Language-Model.Infer"]),
            self._ev([entry], declared=["language_model.infer"]),
            {},
        )[0]
        assert self._verdicts(canon) == {"d1": "pass"}

    def test_output_format_fence_and_json_gates(self):
        fence = {
            "id": "f1",
            "rule": "no fenced output",
            "type": "output_format",
            "params": {"fence_output": False},
            "tools": [],
        }
        bad = deterministic.evaluate(self._unit(output="```json\n{}\n```"), self._ev([fence]), {})[
            0
        ]
        assert self._verdicts(bad) == {"f1": "fail"}
        ok = deterministic.evaluate(self._unit(output='{"a": 1}'), self._ev([fence]), {})[0]
        assert self._verdicts(ok) == {"f1": "pass"}
        jsn = {
            "id": "f2",
            "rule": "emit JSON",
            "type": "output_format",
            "params": {"format": "json"},
            "tools": [],
        }
        assert self._verdicts(
            deterministic.evaluate(self._unit(output="not json {"), self._ev([jsn]), {})[0]
        ) == {"f2": "fail"}

    def test_uncheckable_types_abstain_and_all_abstain_scores_none(self):
        entries = [
            {
                "id": "e1",
                "rule": "evidence must ground output",
                "type": "evidence",
                "params": {},
                "tools": [],
            },
            {
                "id": "o1",
                "rule": "needs at least one example",
                "type": "output_format",
                "params": {"min_examples": 1},
                "tools": [],
            },
        ]
        draft = deterministic.evaluate(self._unit(tools=["fetch"]), self._ev(entries), {})[0]
        assert draft.value is None
        assert draft.outcome == "abstained"
        assert self._verdicts(draft) == {"e1": "abstain", "o1": "abstain"}

    def test_mixed_verdicts_score_fraction(self):
        entries = [
            {
                "id": "c1",
                "rule": "fetch before parse",
                "type": "ordering",
                "params": {},
                "tools": ["fetch", "parse"],
            },
            {
                "id": "d1",
                "rule": "only declared tools",
                "type": "tool_discipline",
                "params": {},
                "tools": [],
            },
            {"id": "e1", "rule": "unverifiable", "type": "evidence", "params": {}, "tools": []},
        ]
        draft = deterministic.evaluate(self._unit(tools=["parse", "fetch"]), self._ev(entries), {})[
            0
        ]
        # ordering fails, discipline passes, evidence abstains → 1/2 checked.
        assert draft.value == 0.5
        assert self._verdicts(draft) == {"c1": "fail", "d1": "pass", "e1": "abstain"}

    def test_budget_honours_max_rounds_not_call_count(self):
        entry = {
            "id": "b1",
            "rule": "at most 4 rounds",
            "type": "budget",
            "params": {"max_rounds": 4},
            "tools": [],
        }
        many_calls = ["a", "b", "c", "d", "e"]
        two_rounds = self._round_messages(["a", "b", "c"], ["d", "e"])
        ok = deterministic.evaluate(
            self._unit(tools=many_calls, messages=two_rounds), self._ev([entry]), {}
        )[0]
        assert self._verdicts(ok) == {"b1": "pass"}
        five_rounds = self._round_messages(*(["x"] for _ in range(5)))
        over = deterministic.evaluate(
            self._unit(tools=["x"] * 5, messages=five_rounds), self._ev([entry]), {}
        )[0]
        assert self._verdicts(over) == {"b1": "fail"}
        no_messages = deterministic.evaluate(self._unit(tools=many_calls), self._ev([entry]), {})[0]
        assert self._verdicts(no_messages) == {"b1": "abstain"}

    def test_single_tool_precondition_is_must_have_been_called(self):
        entry = {
            "id": "p1",
            "rule": "post_decision exactly once",
            "type": "precondition",
            "params": {},
            "tools": ["post_decision"],
        }
        ok = deterministic.evaluate(self._unit(tools=["post_decision"]), self._ev([entry]), {})[0]
        assert self._verdicts(ok) == {"p1": "pass"}
        missing = deterministic.evaluate(self._unit(tools=["fetch"]), self._ev([entry]), {})[0]
        assert self._verdicts(missing) == {"p1": "fail"}

    def test_situational_one_tool_precondition_abstains(self):
        entry = {
            "id": "p1",
            "rule": "get_fx_rate before converting claimed_amount into reporting_currency",
            "type": "precondition",
            "params": {},
            "tools": ["get_fx_rate"],
        }
        called = deterministic.evaluate(self._unit(tools=["get_fx_rate"]), self._ev([entry]), {})[0]
        skipped = deterministic.evaluate(self._unit(tools=["fetch"]), self._ev([entry]), {})[0]
        assert self._verdicts(called) == {"p1": "abstain"}
        assert self._verdicts(skipped) == {"p1": "abstain"}
        item = next(s for s in skipped.sub_scores if s.get("id") == "p1")
        assert item["verdict"] is None

    def test_situational_trigger_field_present_is_must_call(self):
        entry = {
            "id": "p1",
            "rule": "lookup before citing a clause",
            "type": "precondition",
            "params": {"when_field": "clause"},
            "tools": ["lookup"],
        }
        called = deterministic.evaluate(
            self._unit(tools=["lookup"], output='{"clause": "T-1"}'), self._ev([entry]), {}
        )[0]
        assert self._verdicts(called) == {"p1": "pass"}
        missing = deterministic.evaluate(
            self._unit(tools=["fetch"], output='{"clause": "T-1"}'), self._ev([entry]), {}
        )[0]
        assert self._verdicts(missing) == {"p1": "fail"}
        idle = deterministic.evaluate(
            self._unit(tools=["fetch"], output='{"clause": ""}'), self._ev([entry]), {}
        )[0]
        assert self._verdicts(idle) == {"p1": "abstain"}

    def test_situational_trigger_inferred_from_output_fields_when_params_empty(self):
        entry = {
            "id": "p1",
            "rule": "lookup before citing a clause",
            "type": "precondition",
            "params": {},
            "tools": ["lookup"],
        }
        unit = self._unit(tools=["fetch"], output='{"clause": "T-1"}')
        unit.output_fields = {"clause": "string — cited id"}
        draft = deterministic.evaluate(unit, self._ev([entry]), {})[0]
        assert self._verdicts(draft) == {"p1": "fail"}

    def test_two_field_rule_abstains_when_unit_has_the_compiler_field_map(self):
        entry = {
            "id": "p1",
            "rule": "get_fx_rate before converting claimed_amount into reporting_currency",
            "type": "precondition",
            "params": {},
            "tools": ["get_fx_rate"],
        }
        unit = self._unit(
            tools=["fetch"],
            output='{"reporting_currency": "USD", "claimed_amount": 10}',
        )
        unit.output_fields = {"reporting_currency": "string"}
        unit.input_schema = {"claimed_amount": "number"}
        draft = deterministic.evaluate(unit, self._ev([entry]), {})[0]
        assert self._verdicts(draft) == {"p1": "abstain"}

    def test_declared_when_fields_differ_is_must_call_only_when_values_differ(self):
        entry = {
            "id": "p1",
            "rule": "get_rate before converting",
            "type": "precondition",
            "params": {"when_fields_differ": ["claim_currency", "reporting_currency"]},
            "tools": ["get_rate"],
        }
        same = deterministic.evaluate(
            self._unit(
                tools=["fetch"],
                output='{"claim_currency": "USD", "reporting_currency": "USD"}',
            ),
            self._ev([entry]),
            {},
        )[0]
        assert self._verdicts(same) == {"p1": "abstain"}
        needed = deterministic.evaluate(
            self._unit(
                tools=["fetch"],
                output='{"claim_currency": "EUR", "reporting_currency": "USD"}',
            ),
            self._ev([entry]),
            {},
        )[0]
        assert self._verdicts(needed) == {"p1": "fail"}
        called = deterministic.evaluate(
            self._unit(
                tools=["get_rate"],
                output='{"claim_currency": "EUR", "reporting_currency": "USD"}',
            ),
            self._ev([entry]),
            {},
        )[0]
        assert self._verdicts(called) == {"p1": "pass"}

    def test_situational_trigger_enum_values(self):
        entry = {
            "id": "p1",
            "rule": "history before pass or defer",
            "type": "precondition",
            "params": {"when_field": "verdict", "when_values": ["pass", "defer"]},
            "tools": ["history"],
        }
        needed = deterministic.evaluate(
            self._unit(tools=["fetch"], output='{"verdict": "Pass"}'), self._ev([entry]), {}
        )[0]
        assert self._verdicts(needed) == {"p1": "fail"}
        other = deterministic.evaluate(
            self._unit(tools=["fetch"], output='{"verdict": "fail"}'), self._ev([entry]), {}
        )[0]
        assert self._verdicts(other) == {"p1": "abstain"}

    def test_ordering_last_checks_the_last_call_not_presence(self):
        entry = {
            "id": "o1",
            "rule": "post_decision is the last tool call",
            "type": "ordering",
            "params": {"position": "last"},
            "tools": ["post_decision"],
        }
        last = deterministic.evaluate(
            self._unit(tools=["fetch", "post_decision"]), self._ev([entry]), {}
        )[0]
        assert self._verdicts(last) == {"o1": "pass"}
        not_last = deterministic.evaluate(
            self._unit(tools=["post_decision", "fetch"]), self._ev([entry]), {}
        )[0]
        assert self._verdicts(not_last) == {"o1": "fail"}
        never = deterministic.evaluate(self._unit(tools=["fetch"]), self._ev([entry]), {})[0]
        assert self._verdicts(never) == {"o1": "fail"}

    def test_ordering_last_inferred_from_rule_when_params_empty(self):
        entry = {
            "id": "o1",
            "rule": "post_decision is the last tool call",
            "type": "ordering",
            "params": {},
            "tools": ["post_decision"],
        }
        last = deterministic.evaluate(
            self._unit(tools=["fetch", "post_decision"]), self._ev([entry]), {}
        )[0]
        assert self._verdicts(last) == {"o1": "pass"}
        not_last = deterministic.evaluate(
            self._unit(tools=["post_decision", "fetch"]), self._ev([entry]), {}
        )[0]
        assert self._verdicts(not_last) == {"o1": "fail"}

    def test_constraint_items_expose_boolean_verdict_for_item_rollup(self):
        entry = {
            "id": "c1",
            "rule": "fetch before parse",
            "type": "ordering",
            "params": {},
            "tools": ["fetch", "parse"],
        }
        draft = deterministic.evaluate(self._unit(tools=["fetch", "parse"]), self._ev([entry]), {})[
            0
        ]
        item = next(s for s in draft.sub_scores if s.get("id") == "c1")
        assert item["verdict"] is True
        assert item["_constraint"]["verdict"] == "pass"
        fail = deterministic.evaluate(self._unit(tools=["parse", "fetch"]), self._ev([entry]), {})[
            0
        ]
        item = next(s for s in fail.sub_scores if s.get("id") == "c1")
        assert item["verdict"] is False
        assert item["_constraint"]["verdict"] == "fail"

    def test_tool_discipline_counts_named_tool(self):
        entry = {
            "id": "d1",
            "rule": "call post_decision exactly once",
            "type": "tool_discipline",
            "params": {"tool": "post_decision", "max_calls": 1},
            "tools": [],
        }
        ok = deterministic.evaluate(self._unit(tools=["post_decision"]), self._ev([entry]), {})[0]
        assert self._verdicts(ok) == {"d1": "pass"}
        twice = deterministic.evaluate(
            self._unit(tools=["post_decision", "post_decision"]), self._ev([entry]), {}
        )[0]
        assert self._verdicts(twice) == {"d1": "fail"}
        missing = deterministic.evaluate(self._unit(tools=["fetch"]), self._ev([entry]), {})[0]
        assert self._verdicts(missing) == {"d1": "fail"}

    def test_tool_arguments_required_keys_and_number_type(self):
        entry = {
            "id": "a1",
            "rule": "post_decision arguments match the declared schema",
            "type": "tool_arguments",
            "params": {
                "tool": "post_decision",
                "arguments": [
                    {"name": "claim_id", "required": True},
                    {
                        "name": "approved_amount",
                        "required": True,
                        "kind": "number",
                        "nullable": True,
                    },
                ],
            },
            "tools": ["post_decision"],
        }
        ok = deterministic.evaluate(
            self._unit(
                calls=[
                    {
                        "tool": "post_decision",
                        "arguments": {"claim_id": "c1", "approved_amount": 79},
                    }
                ]
            ),
            self._ev([entry]),
            {},
        )[0]
        assert self._verdicts(ok) == {"a1": "pass"}
        as_string = deterministic.evaluate(
            self._unit(
                calls=[
                    {
                        "tool": "post_decision",
                        "arguments": {"claim_id": "c1", "approved_amount": "79"},
                    }
                ]
            ),
            self._ev([entry]),
            {},
        )[0]
        assert self._verdicts(as_string) == {"a1": "fail"}
        none_str = deterministic.evaluate(
            self._unit(
                calls=[
                    {
                        "tool": "post_decision",
                        "arguments": {"claim_id": "c1", "approved_amount": "None"},
                    }
                ]
            ),
            self._ev([entry]),
            {},
        )[0]
        assert self._verdicts(none_str) == {"a1": "fail"}
        nullable = deterministic.evaluate(
            self._unit(
                calls=[
                    {
                        "tool": "post_decision",
                        "arguments": {"claim_id": "c1", "approved_amount": None},
                    }
                ]
            ),
            self._ev([entry]),
            {},
        )[0]
        assert self._verdicts(nullable) == {"a1": "pass"}
        silent = deterministic.evaluate(self._unit(tools=["fetch"]), self._ev([entry]), {})[0]
        assert self._verdicts(silent) == {"a1": "abstain"}
        missing = deterministic.evaluate(
            self._unit(calls=[{"tool": "post_decision", "arguments": {"approved_amount": 79}}]),
            self._ev([entry]),
            {},
        )[0]
        assert self._verdicts(missing) == {"a1": "fail"}


class TestJudgeFieldRefs:
    def _outcome(self, items):
        return SimpleNamespace(
            parsed=gen_judge.ChecklistResult(items=items, reasoning="r"),
            stats={},
            judge_trace_id="",
        )

    def test_configured_field_ref_stamped_onto_verdict_sub_scores(self):
        ev = evaluator_stub(
            kind="llm_judge",
            checklist=[
                {"id": "grounding", "q": "is it grounded?", "field": "char_interval"},
                {"id": "freeform", "q": "is it good?"},
            ],
        )
        items = [
            gen_judge.ChecklistItem(id="grounding", verdict=False, reasoning="no interval"),
            gen_judge.ChecklistItem(id="freeform", verdict=True),
        ]
        draft = gen_judge._draft_from_outcome(self._outcome(items), ev)
        by_id = {s["id"]: s for s in draft.sub_scores if "id" in s}
        # The field comes from the evaluator CONFIG, never the LLM output.
        assert by_id["grounding"]["field"] == "char_interval"
        assert "field" not in by_id["freeform"]

    def test_unconfigured_checklist_changes_nothing(self):
        ev = evaluator_stub(kind="llm_judge", checklist=[{"id": "a", "q": "?"}])
        items = [gen_judge.ChecklistItem(id="a", verdict=False)]
        draft = gen_judge._draft_from_outcome(self._outcome(items), ev)
        by_id = {s["id"]: s for s in draft.sub_scores if "id" in s}
        assert by_id["a"]["verdict"] is False
        assert all("field" not in s for s in draft.sub_scores if "id" in s)

    def test_field_bound_item_fails_when_output_is_not_json(self):
        ev = evaluator_stub(
            kind="llm_judge",
            checklist=[
                {"id": "decision", "q": "does the decision match?", "field": "decision"},
                {"id": "tone", "q": "is the tone ok?"},
            ],
        )
        items = [
            gen_judge.ChecklistItem(id="decision", verdict=True, reasoning="yes in the prose"),
            gen_judge.ChecklistItem(id="tone", verdict=True),
        ]
        draft = gen_judge._draft_from_outcome(
            self._outcome(items), ev, output="The claim should be escalated."
        )
        by_id = {s["id"]: s for s in draft.sub_scores if "id" in s}
        assert by_id["decision"]["verdict"] is False
        assert by_id["tone"]["verdict"] is True
        assert draft.value == 0.5

    def test_field_bound_item_stands_when_key_is_in_the_record(self):
        ev = evaluator_stub(
            kind="llm_judge",
            checklist=[{"id": "decision", "q": "match?", "field": "decision"}],
        )
        items = [gen_judge.ChecklistItem(id="decision", verdict=True)]
        draft = gen_judge._draft_from_outcome(
            self._outcome(items), ev, output='{"decision": "escalate"}'
        )
        by_id = {s["id"]: s for s in draft.sub_scores if "id" in s}
        assert by_id["decision"]["verdict"] is True
        assert draft.value == 1.0

    def test_field_bound_item_fails_when_key_is_absent(self):
        ev = evaluator_stub(
            kind="llm_judge",
            checklist=[{"id": "decision", "q": "match?", "field": "decision"}],
        )
        items = [gen_judge.ChecklistItem(id="decision", verdict=True)]
        draft = gen_judge._draft_from_outcome(self._outcome(items), ev, output='{"other": 1}')
        by_id = {s["id"]: s for s in draft.sub_scores if "id" in s}
        assert by_id["decision"]["verdict"] is False
        assert (
            by_id["decision"]["reasoning"]
            == "Required output field 'decision' is missing from the JSON object."
        )
        assert draft.reasoning == "Missing required output fields: decision."
        assert draft.value == 0.0

    def test_field_bound_null_key_is_present(self):
        ev = evaluator_stub(
            kind="llm_judge",
            checklist=[{"id": "rate", "q": "match?", "field": "fx_rate_used"}],
        )
        items = [gen_judge.ChecklistItem(id="rate", verdict=True)]
        draft = gen_judge._draft_from_outcome(
            self._outcome(items), ev, output='{"fx_rate_used": null}'
        )
        by_id = {s["id"]: s for s in draft.sub_scores if "id" in s}
        assert by_id["rate"]["verdict"] is True


class TestChecklistAggregation:
    def _draft(self, ev, items):
        outcome = SimpleNamespace(
            parsed=gen_judge.ChecklistResult(items=items, reasoning="r"),
            stats={},
            judge_trace_id="",
        )
        return gen_judge._draft_from_outcome(outcome, ev)

    def _ev(self, checklist, **kwargs):
        return evaluator_stub(kind="llm_judge", checklist=checklist, **kwargs)

    def test_score_is_the_weighted_pass_fraction(self):
        ev = self._ev(
            [
                {"id": "a", "q": "?", "weight": 3.0},
                {"id": "b", "q": "?", "weight": 1.0},
            ]
        )
        draft = self._draft(
            ev,
            [
                gen_judge.ChecklistItem(id="a", verdict=True),
                gen_judge.ChecklistItem(id="b", verdict=False),
            ],
        )
        assert draft.value == 0.75

    def test_hyphen_underscore_id_still_scores_the_configured_item(self):
        ev = self._ev(
            [
                {"id": "citations_from_retrieved_sources", "q": "?", "weight": 1.0},
                {"id": "the-output-agrees-with-the-reference-answer", "q": "?", "weight": 3.0},
            ]
        )
        draft = self._draft(
            ev,
            [
                gen_judge.ChecklistItem(id="citations_from_retrieved_sources", verdict=True),
                gen_judge.ChecklistItem(
                    id="the-output-agrees-with-the-reference_answer", verdict=False
                ),
            ],
        )
        assert draft.value == 0.25
        assert draft.outcome == base.OUTCOME_SCORED
        by_id = {s["id"]: s for s in draft.sub_scores if "id" in s}
        assert "the-output-agrees-with-the-reference-answer" in by_id
        assert by_id["the-output-agrees-with-the-reference-answer"]["verdict"] is False
        assert "the-output-agrees-with-the-reference_answer" not in by_id

    def test_ambiguous_folded_ids_stay_exact_match(self):
        ev = self._ev(
            [
                {"id": "foo-bar", "q": "?"},
                {"id": "foo_bar", "q": "?"},
            ]
        )
        draft = self._draft(ev, [gen_judge.ChecklistItem(id="foobar", verdict=True)])
        assert draft.value is None
        assert draft.outcome == base.OUTCOME_ERROR

    def test_configured_weights_beat_judge_invented_items(self):
        ev = self._ev([{"id": "a", "q": "?"}])
        draft = self._draft(
            ev,
            [
                gen_judge.ChecklistItem(id="a", verdict=False),
                gen_judge.ChecklistItem(id="hallucinated", verdict=True),
            ],
        )
        assert draft.value == 0.0

    def test_unanswered_item_is_false_and_is_reported(self):
        ev = self._ev([{"id": "a", "q": "?"}, {"id": "skipped", "q": "?"}])
        draft = self._draft(ev, [gen_judge.ChecklistItem(id="a", verdict=True)])
        assert draft.value == 0.5
        coverage = next(s["_coverage"] for s in draft.sub_scores if "_coverage" in s)
        assert coverage["unanswered"] == ["skipped"]

    def test_score_is_rescaled_into_the_evaluator_range(self):
        ev = self._ev([{"id": "a", "q": "?"}, {"id": "b", "q": "?"}], score_min=1.0, score_max=5.0)
        draft = self._draft(
            ev,
            [
                gen_judge.ChecklistItem(id="a", verdict=True),
                gen_judge.ChecklistItem(id="b", verdict=False),
            ],
        )
        assert draft.value == 3.0

    def test_no_answered_item_errors_rather_than_scoring_zero(self):
        ev = self._ev([{"id": "a", "q": "?"}])
        draft = self._draft(ev, [gen_judge.ChecklistItem(id="a", verdict=None)])
        assert draft.value is None
        assert draft.outcome == base.OUTCOME_ERROR

    def test_not_applicable_item_leaves_the_denominator(self):
        ev = self._ev([{"id": "a", "q": "?"}, {"id": "empty", "q": "?"}])
        draft = self._draft(
            ev,
            [
                gen_judge.ChecklistItem(id="a", verdict=True),
                gen_judge.ChecklistItem(id="empty", not_applicable=True, verdict=False),
            ],
        )
        assert draft.value == 1.0
        by_id = {s["id"]: s for s in draft.sub_scores if "id" in s}
        assert by_id["empty"]["outcome"] == "not_applicable"
        assert by_id["empty"]["verdict"] is None
        coverage = next(s["_coverage"] for s in draft.sub_scores if "_coverage" in s)
        assert coverage["not_applicable"] == ["empty"]

    def test_every_item_not_applicable_is_not_an_error(self):
        ev = self._ev([{"id": "a", "q": "?"}, {"id": "b", "q": "?"}])
        draft = self._draft(
            ev,
            [
                gen_judge.ChecklistItem(id="a", not_applicable=True, verdict=True),
                gen_judge.ChecklistItem(id="b", not_applicable=True, verdict=False),
            ],
        )
        assert draft.value is None
        assert draft.outcome == base.OUTCOME_NOT_APPLICABLE

    def test_categorical_all_not_applicable_does_not_mint_a_label(self):
        ev = self._ev(
            [{"id": "a", "q": "?"}, {"id": "b", "q": "?"}],
            score_type="categorical",
            choices=[{"label": "pass", "value": 1.0}],
        )
        draft = gen_judge._draft_from_outcome(
            SimpleNamespace(
                parsed=gen_judge.ChecklistResult(
                    items=[
                        gen_judge.ChecklistItem(id="a", not_applicable=True),
                        gen_judge.ChecklistItem(id="b", not_applicable=True),
                    ],
                    reasoning="r",
                    label="pass",
                ),
                stats={},
                judge_trace_id="",
            ),
            ev,
        )
        assert draft.value is None
        assert draft.outcome == base.OUTCOME_NOT_APPLICABLE

    def test_checklist_prompt_instructs_not_applicable(self):
        from overbae.services.eval.rubric_compiler import build_checklist_prompt

        ev = self._ev([{"id": "a", "q": "when context is empty, did it abstain?"}])
        prompt = build_checklist_prompt(ev, {"output": "a long report"})
        assert "not_applicable" in prompt
        assert "answer every checklist item" not in prompt.lower()

    def test_checklist_prompt_puts_bound_gold_in_the_question(self):
        from overbae.services.eval.rubric_compiler import build_checklist_prompt

        ev = self._ev(
            [{"id": "gold", "q": "Does the output agree with {reference}?"}],
        )
        prompt = build_checklist_prompt(
            ev,
            {
                "input": "Sources:\nWikipedia says 15th place.\n" * 40,
                "output": "The team finished 15th.",
                "reference": "January 8, 2019",
            },
        )
        gold_line = next(line for line in prompt.splitlines() if line.startswith("- (gold)"))
        assert "January 8, 2019" in gold_line
        assert "{reference}" not in gold_line
        assert "only answer key" in prompt
        assert "not a substitute gold" in prompt
        assert "always applies" in prompt
        assert "not not_applicable" in prompt
        assert prompt.index("reference:\nJanuary 8, 2019") < prompt.index("Inputs:")
        assert prompt.index("Inputs:") < prompt.index("Sources:")

    def test_checklist_prompt_does_not_interpolate_input_into_questions(self):
        from overbae.services.eval.rubric_compiler import build_checklist_prompt

        ev = self._ev([{"id": "a", "q": "Did it use {input}?"}])
        prompt = build_checklist_prompt(
            ev,
            {"input": "STUFFED DOCUMENT", "output": "ok", "reference": "gold"},
        )
        gold_line = next(line for line in prompt.splitlines() if line.startswith("- (a)"))
        assert "STUFFED DOCUMENT" not in gold_line
        assert "{input}" in gold_line

    def test_failed_gate_floors_a_boolean_score(self):
        ev = self._ev(
            [
                {"id": "safety", "q": "?", "gate": True},
                {"id": "b", "q": "?"},
                {"id": "c", "q": "?"},
            ],
            score_type="boolean",
        )
        draft = self._draft(
            ev,
            [
                gen_judge.ChecklistItem(id="safety", verdict=False),
                gen_judge.ChecklistItem(id="b", verdict=True),
                gen_judge.ChecklistItem(id="c", verdict=True),
            ],
        )
        assert draft.value == 0.0

    def test_bound_reference_na_keeps_the_verdict(self):
        ev = self._ev(
            [
                {"id": "citations_from_retrieved_sources", "q": "cited?", "weight": 0.25},
                {
                    "id": "the-output-agrees-with-the-reference-answer",
                    "q": "Does the output agree with the reference answer?",
                    "weight": 1.0,
                },
            ],
            requires_reference=True,
        )
        draft = gen_judge._draft_from_outcome(
            SimpleNamespace(
                parsed=gen_judge.ChecklistResult(
                    items=[
                        gen_judge.ChecklistItem(
                            id="citations_from_retrieved_sources", verdict=True
                        ),
                        gen_judge.ChecklistItem(
                            id="the-output-agrees-with-the-reference-answer",
                            not_applicable=True,
                            verdict=True,
                        ),
                    ],
                    reasoning="r",
                ),
                stats={},
                judge_trace_id="",
            ),
            ev,
            reference="Edward Teller",
        )
        assert draft.value == 1.0
        by_id = {s["id"]: s for s in draft.sub_scores if "id" in s}
        assert by_id["the-output-agrees-with-the-reference-answer"]["verdict"] is True
        assert "outcome" not in by_id["the-output-agrees-with-the-reference-answer"]

    def test_bound_reference_na_without_verdict_is_false(self):
        ev = self._ev(
            [
                {"id": "citations_from_retrieved_sources", "q": "cited?"},
                {
                    "id": "the-output-agrees-with-the-reference-answer",
                    "q": "Does the output agree with the reference answer?",
                },
            ]
        )
        draft = gen_judge._draft_from_outcome(
            SimpleNamespace(
                parsed=gen_judge.ChecklistResult(
                    items=[
                        gen_judge.ChecklistItem(
                            id="citations_from_retrieved_sources", verdict=True
                        ),
                        gen_judge.ChecklistItem(
                            id="the-output-agrees-with-the-reference-answer",
                            not_applicable=True,
                            verdict=None,
                        ),
                    ],
                    reasoning="r",
                ),
                stats={},
                judge_trace_id="",
            ),
            ev,
            reference="Edward Teller",
        )
        assert draft.value == 0.5
        by_id = {s["id"]: s for s in draft.sub_scores if "id" in s}
        assert by_id["the-output-agrees-with-the-reference-answer"]["verdict"] is False
        assert "outcome" not in by_id["the-output-agrees-with-the-reference-answer"]
        assert not any("_coverage" in s for s in draft.sub_scores)

    def test_empty_reference_keeps_na(self):
        ev = self._ev(
            [
                {
                    "id": "the-output-agrees-with-the-reference-answer",
                    "q": "agree with the reference?",
                }
            ]
        )
        draft = gen_judge._draft_from_outcome(
            SimpleNamespace(
                parsed=gen_judge.ChecklistResult(
                    items=[
                        gen_judge.ChecklistItem(
                            id="the-output-agrees-with-the-reference-answer",
                            not_applicable=True,
                            verdict=True,
                        )
                    ],
                    reasoning="r",
                ),
                stats={},
                judge_trace_id="",
            ),
            ev,
            reference="",
        )
        assert draft.outcome == base.OUTCOME_NOT_APPLICABLE

    def test_citation_na_is_kept_when_reference_is_bound(self):
        ev = self._ev(
            [
                {"id": "citations_from_retrieved_sources", "q": "cited?"},
                {
                    "id": "the-output-agrees-with-the-reference-answer",
                    "q": "Does the output agree with the reference answer?",
                },
            ]
        )
        draft = gen_judge._draft_from_outcome(
            SimpleNamespace(
                parsed=gen_judge.ChecklistResult(
                    items=[
                        gen_judge.ChecklistItem(
                            id="citations_from_retrieved_sources",
                            not_applicable=True,
                            verdict=True,
                        ),
                        gen_judge.ChecklistItem(
                            id="the-output-agrees-with-the-reference-answer", verdict=True
                        ),
                    ],
                    reasoning="r",
                ),
                stats={},
                judge_trace_id="",
            ),
            ev,
            reference="Edward Teller",
        )
        assert draft.value == 1.0
        by_id = {s["id"]: s for s in draft.sub_scores if "id" in s}
        assert by_id["citations_from_retrieved_sources"]["outcome"] == "not_applicable"

    def test_unreferenced_is_not_a_reference_item(self):
        ev = self._ev([{"id": "unreferenced_sources", "q": "are unused sources omitted?"}])
        draft = gen_judge._draft_from_outcome(
            SimpleNamespace(
                parsed=gen_judge.ChecklistResult(
                    items=[
                        gen_judge.ChecklistItem(
                            id="unreferenced_sources", not_applicable=True, verdict=True
                        )
                    ],
                    reasoning="r",
                ),
                stats={},
                judge_trace_id="",
            ),
            ev,
            reference="Edward Teller",
        )
        assert draft.outcome == base.OUTCOME_NOT_APPLICABLE


class TestGenerateMechanicalApplicability:
    def _unit(self, **kwargs):
        from tests.test_eval_generate_applicability import BEHAVIOUR_CARD

        return EvalUnit(
            trajectory={"final_output": "A long sourced report.", "messages": []},
            codebase_card=BEHAVIOUR_CARD,
            **kwargs,
        )

    def _evaluate(self, monkeypatch, ev, unit=None):
        from overbae.services.eval import cascade
        from overbae.services.eval import funnel as judging

        calls: list[dict] = []

        def _fake(*args, **kwargs):
            calls.append(kwargs)
            raise AssertionError("judge must not run when every item is not applicable")

        monkeypatch.setattr(cascade, "maybe_route", lambda *a, **k: None)
        monkeypatch.setattr(judging, "invoke_judge", _fake)
        drafts = gen_judge.evaluate(unit or self._unit(), ev, {})
        return drafts, calls

    def test_applies_when_excludes_the_item_without_the_judge(self, monkeypatch):
        ev = evaluator_stub(
            kind="llm_judge",
            judge_model="judge-x",
            checklist=[
                {
                    "id": "empty",
                    "q": "Did it abstain?",
                    "weight": 1.0,
                    "applies_when": {"context_present": "gathered_context_empty"},
                }
            ],
            variable_mapping=[{"var": "output", "source": "output"}],
        )
        drafts, calls = self._evaluate(monkeypatch, ev)
        assert calls == []
        assert drafts[0].outcome == base.OUTCOME_NOT_APPLICABLE
        assert drafts[0].value is None
        by_id = {s["id"]: s for s in drafts[0].sub_scores if "id" in s}
        assert by_id["empty"]["outcome"] == "not_applicable"

    def test_paraphrased_failure_mode_item_is_not_applicable(self, monkeypatch):
        ev = evaluator_stub(
            kind="llm_judge",
            judge_model="judge-x",
            checklist=[
                {
                    "id": "abstention_clarity",
                    "q": "Abstention message is explicit and unambiguous",
                    "weight": 1.0,
                }
            ],
            variable_mapping=[{"var": "output", "source": "output"}],
            config={"provenance": {"source": "codebase_card.failure_modes[0]"}},
        )
        drafts, calls = self._evaluate(monkeypatch, ev)
        assert calls == []
        assert drafts[0].outcome == base.OUTCOME_NOT_APPLICABLE
        by_id = {s["id"]: s for s in drafts[0].sub_scores if "id" in s}
        assert by_id["abstention_clarity"]["outcome"] == "not_applicable"

    def test_mixed_cluster_still_asks_the_observable_item(self, monkeypatch):
        from overbae.services.eval import cascade
        from overbae.services.eval import funnel as judging

        calls: list[dict] = []

        def _fake(*args, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                parsed=gen_judge.ChecklistResult(
                    items=[gen_judge.ChecklistItem(id="cites", verdict=True)],
                    reasoning="r",
                ),
                stats={},
                judge_trace_id="t",
            )

        monkeypatch.setattr(cascade, "maybe_route", lambda *a, **k: None)
        monkeypatch.setattr(judging, "invoke_judge", _fake)
        ev = evaluator_stub(
            kind="llm_judge",
            judge_model="judge-x",
            checklist=[
                {
                    "id": "cites",
                    "q": "Do citations in the report come from retrieved sources?",
                    "weight": 1.0,
                },
                {
                    "id": "deep",
                    "q": "Do deep and detailed research modes produce broader coverage?",
                    "weight": 1.0,
                },
            ],
            variable_mapping=[{"var": "output", "source": "output"}],
            config={"provenance": {"source": "codebase_card.success_criteria"}},
        )
        drafts = gen_judge.evaluate(self._unit(), ev, {})
        assert len(calls) == 1
        assert drafts[0].outcome == base.OUTCOME_SCORED
        assert drafts[0].value == 1.0
        by_id = {s["id"]: s for s in drafts[0].sub_scores if "id" in s}
        assert by_id["cites"]["verdict"] is True
        assert by_id["deep"]["outcome"] == "not_applicable"


class TestEmptyChecklistRetry:
    def _unit(self):
        return EvalUnit(trajectory={"final_output": "A report.", "messages": []})

    def _ev(self, **kwargs):
        return evaluator_stub(
            kind="llm_judge",
            judge_model="judge-x",
            checklist=[{"id": "q1", "q": "Is it correct?", "weight": 1.0}],
            variable_mapping=[{"var": "output", "source": "output"}],
            **kwargs,
        )

    def _outcome(self, items, parsed=True):
        return SimpleNamespace(
            parsed=(None if not parsed else gen_judge.ChecklistResult(items=items, reasoning="r")),
            stats={},
            judge_trace_id="t",
        )

    def _evaluate(self, monkeypatch, replies):
        from overbae.services.eval import cascade
        from overbae.services.eval import funnel as judging

        calls: list[dict] = []

        def _fake(*args, **kwargs):
            calls.append(kwargs)
            return replies[min(len(calls) - 1, len(replies) - 1)]

        monkeypatch.setattr(cascade, "maybe_route", lambda *a, **k: None)
        monkeypatch.setattr(judging, "invoke_judge", _fake)
        drafts = gen_judge.evaluate(self._unit(), self._ev(), {})
        return drafts, calls

    def test_empty_items_retries_once_then_scores(self, monkeypatch):
        filled = [gen_judge.ChecklistItem(id="q1", verdict=True)]
        drafts, calls = self._evaluate(
            monkeypatch,
            [self._outcome([]), self._outcome(filled)],
        )
        assert len(calls) == 2
        assert calls[1].get("use_cache") is False
        assert drafts[0].outcome == base.OUTCOME_SCORED
        assert drafts[0].value == 1.0

    def test_parse_failure_retries_once_then_scores(self, monkeypatch):
        filled = [gen_judge.ChecklistItem(id="q1", verdict=True)]
        drafts, calls = self._evaluate(
            monkeypatch,
            [self._outcome([], parsed=False), self._outcome(filled)],
        )
        assert len(calls) == 2
        assert calls[1].get("use_cache") is False
        assert drafts[0].value == 1.0

    def test_empty_items_twice_stays_an_error(self, monkeypatch):
        drafts, calls = self._evaluate(monkeypatch, [self._outcome([]), self._outcome([])])
        assert len(calls) == 2
        assert drafts[0].value is None
        assert drafts[0].outcome == base.OUTCOME_ERROR
        assert "none of the configured checklist items" in drafts[0].reasoning

    def test_unmatched_items_do_not_retry(self, monkeypatch):
        drafts, calls = self._evaluate(
            monkeypatch,
            [self._outcome([gen_judge.ChecklistItem(id="invented", verdict=True)])],
        )
        assert len(calls) == 1
        assert drafts[0].outcome == base.OUTCOME_ERROR

    def test_proportional_empty_claims_do_not_retry(self, monkeypatch):
        from overbae.services.eval import cascade
        from overbae.services.eval import funnel as judging

        calls: list[dict] = []

        def _fake(*args, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                parsed=gen_judge.ClaimsResult(claims=[], reasoning="r"),
                stats={},
                judge_trace_id="t",
            )

        monkeypatch.setattr(cascade, "maybe_route", lambda *a, **k: None)
        monkeypatch.setattr(judging, "invoke_judge", _fake)
        ev = self._ev(config={gen_judge.SCORING_MODE_KEY: gen_judge.SCORING_PROPORTIONAL})
        drafts = gen_judge.evaluate(self._unit(), ev, {})
        assert len(calls) == 1
        assert drafts[0].outcome == base.OUTCOME_ABSTAINED


class TestBoundReferenceNaRetry:
    def _unit(self, expected="Edward Teller"):
        return EvalUnit(
            trajectory={"final_output": "Teller won the Ig Nobel.", "messages": []},
            expected=expected,
        )

    def _ev(self):
        return evaluator_stub(
            kind="llm_judge",
            judge_model="judge-x",
            requires_reference=True,
            checklist=[
                {"id": "citations_from_retrieved_sources", "q": "cited?", "weight": 0.25},
                {
                    "id": "the-output-agrees-with-the-reference-answer",
                    "q": "Does the output agree with the reference answer?",
                    "weight": 1.0,
                },
            ],
            variable_mapping=[
                {"var": "output", "source": "output"},
                {"var": "reference", "source": "reference"},
            ],
        )

    def _outcome(self, *, gold_na=False, gold=None, cite=True):
        gold_item = gen_judge.ChecklistItem(
            id="the-output-agrees-with-the-reference-answer",
            not_applicable=gold_na,
            verdict=None if gold_na else gold,
        )
        return SimpleNamespace(
            parsed=gen_judge.ChecklistResult(
                items=[
                    gen_judge.ChecklistItem(id="citations_from_retrieved_sources", verdict=cite),
                    gold_item,
                ],
                reasoning="r",
            ),
            stats={},
            judge_trace_id="t",
        )

    def _evaluate(self, monkeypatch, replies, *, expected="Edward Teller"):
        from overbae.services.eval import cascade
        from overbae.services.eval import funnel as judging

        calls: list[dict] = []

        def _fake(*args, **kwargs):
            calls.append(kwargs)
            return replies[min(len(calls) - 1, len(replies) - 1)]

        monkeypatch.setattr(cascade, "maybe_route", lambda *a, **k: None)
        monkeypatch.setattr(judging, "invoke_judge", _fake)
        drafts = gen_judge.evaluate(self._unit(expected), self._ev(), {})
        return drafts, calls

    def test_na_gold_retries_once_then_scores(self, monkeypatch):
        drafts, calls = self._evaluate(
            monkeypatch,
            [self._outcome(gold_na=True), self._outcome(gold=True)],
        )
        assert len(calls) == 2
        assert calls[1].get("use_cache") is False
        assert drafts[0].value == 1.0
        by_id = {s["id"]: s for s in drafts[0].sub_scores if "id" in s}
        assert by_id["the-output-agrees-with-the-reference-answer"]["verdict"] is True

    def test_na_gold_twice_is_false(self, monkeypatch):
        drafts, calls = self._evaluate(
            monkeypatch,
            [self._outcome(gold_na=True), self._outcome(gold_na=True)],
        )
        assert len(calls) == 2
        assert drafts[0].value == 0.2
        by_id = {s["id"]: s for s in drafts[0].sub_scores if "id" in s}
        assert by_id["the-output-agrees-with-the-reference-answer"]["verdict"] is False
        assert not any("_coverage" in s for s in drafts[0].sub_scores)

    def test_empty_reference_does_not_retry_na(self, monkeypatch):
        drafts, calls = self._evaluate(
            monkeypatch,
            [self._outcome(gold_na=True)],
            expected="",
        )
        assert len(calls) == 1
        assert drafts[0].value == 1.0
        coverage = next(s["_coverage"] for s in drafts[0].sub_scores if "_coverage" in s)
        assert coverage["not_applicable"] == ["the-output-agrees-with-the-reference-answer"]

    def test_citation_reference_verb_stays_na_when_gold_is_bound(self, monkeypatch):
        ev = evaluator_stub(
            kind="llm_judge",
            judge_model="judge-x",
            requires_reference=True,
            checklist=[
                {
                    "id": "citations_from_retrieved_sources",
                    "q": "Do citations reference URLs or documents actually retrieved?",
                    "weight": 0.25,
                },
                {
                    "id": "the-output-agrees-with-the-reference-answer",
                    "q": "Does the output agree with {reference}?",
                    "weight": 1.0,
                },
            ],
            variable_mapping=[
                {"var": "output", "source": "output"},
                {"var": "reference", "source": "reference"},
            ],
        )
        outcome = SimpleNamespace(
            parsed=gen_judge.ChecklistResult(
                items=[
                    gen_judge.ChecklistItem(
                        id="citations_from_retrieved_sources",
                        not_applicable=True,
                        verdict=None,
                    ),
                    gen_judge.ChecklistItem(
                        id="the-output-agrees-with-the-reference-answer",
                        verdict=True,
                    ),
                ],
                reasoning="r",
            ),
            stats={},
            judge_trace_id="t",
        )
        from overbae.services.eval import cascade
        from overbae.services.eval import funnel as judging

        monkeypatch.setattr(cascade, "maybe_route", lambda *a, **k: None)
        monkeypatch.setattr(judging, "invoke_judge", lambda *a, **k: outcome)
        drafts = gen_judge.evaluate(self._unit(), ev, {})
        assert drafts[0].value == 1.0
        by_id = {s["id"]: s for s in drafts[0].sub_scores if "id" in s}
        assert by_id["citations_from_retrieved_sources"]["outcome"] == "not_applicable"
        assert by_id["citations_from_retrieved_sources"]["verdict"] is None


class TestChecklistIsRequired:
    """A generative score is the weighted fraction of configured items, so a
    judge without a checklist cannot produce one. Enforced at authoring and
    again at attach, and never papered over at grade time."""

    def _ev(self, **kwargs):
        return evaluator_stub(kind="llm_judge", checklist=[], **kwargs)

    def test_invented_items_do_not_score(self):
        draft = gen_judge._draft_from_outcome(
            SimpleNamespace(
                parsed=gen_judge.ChecklistResult(
                    items=[gen_judge.ChecklistItem(id="made_up", verdict=True)], reasoning="r"
                ),
                stats={},
                judge_trace_id="",
            ),
            self._ev(),
        )
        assert draft.value is None
        assert draft.outcome == base.OUTCOME_ERROR
        # The message must name the config as the culprit, not the judge.
        assert "no compiled checklist" in draft.reasoning

    def test_judge_skipping_configured_items_reads_differently(self):
        ev = evaluator_stub(kind="llm_judge", checklist=[{"id": "a", "q": "?"}])
        draft = gen_judge._draft_from_outcome(
            SimpleNamespace(
                parsed=gen_judge.ChecklistResult(items=[], reasoning="r"),
                stats={},
                judge_trace_id="",
            ),
            ev,
        )
        assert draft.outcome == base.OUTCOME_ERROR
        assert "answered none" in draft.reasoning

    @pytest.mark.django_db
    def test_attach_refuses_a_checklistless_judge(self):
        from overbae.models import Evaluator, Project
        from overbae.services.eval import snapshots

        project = Project.objects.create(name="cl", slug="cl")
        ev = Evaluator.objects.create(
            project=project,
            name="No Checklist",
            kind=Evaluator.Kind.LLM_JUDGE,
            scope=Evaluator.Scope.FINAL_OUTPUT,
            rubric_md="Grade it.",
        )
        with pytest.raises(snapshots.UngradableEvaluatorError):
            snapshots.build_snapshot(ev)

    @pytest.mark.django_db
    def test_deterministic_and_sentinel_rows_are_exempt(self):
        from overbae.models import Evaluator, Project
        from overbae.services.eval import snapshots

        project = Project.objects.create(name="cl2", slug="cl2")
        exact = Evaluator.objects.create(
            project=project,
            name="Exact",
            kind=Evaluator.Kind.DETERMINISTIC,
            config={"check": "exact_match"},
        )
        sentinel = Evaluator.objects.create(
            project=project,
            name="__agent_spec__",
            kind=Evaluator.Kind.AGENTIC,
            config={"capability_spec_role": "spec"},
        )
        assert snapshots.build_snapshot(exact)["name"] == "Exact"
        assert snapshots.build_snapshot(sentinel)["checklist"] == []


class TestChecklistVariablesMustBeBound:
    """The prompt prints each question verbatim and lists the bound variables
    under it, so an item referencing something unbound asks the judge about
    evidence it was never given — which it answers anyway, from nothing."""

    def _ev(self, question, mapping):
        from overbae.models import Evaluator

        return Evaluator(
            name="J",
            kind=Evaluator.Kind.LLM_JUDGE,
            scope=Evaluator.Scope.FINAL_OUTPUT,
            checklist=[{"id": "a", "q": question, "weight": 1.0}],
            variable_mapping=mapping,
        )

    def test_unbound_variable_is_reported(self):
        ev = self._ev(
            "Is the output consistent with {{established_knowledge}}?",
            [{"var": "input", "source": "input"}, {"var": "output", "source": "output"}],
        )
        assert ev.unbound_checklist_variables() == ["established_knowledge"]

    def test_bound_variable_is_accepted(self):
        ev = self._ev(
            "Is the output consistent with {{context}}?",
            [{"var": "context", "source": "input"}, {"var": "output", "source": "output"}],
        )
        assert ev.unbound_checklist_variables() == []

    def test_empty_mapping_falls_back_to_the_defaults(self):
        # No mapping means input/output/reference are resolved, so an item
        # referencing those is bound even though nothing is declared.
        assert (
            self._ev("Does {{output}} match {{reference}}?", []).unbound_checklist_variables() == []
        )
        assert self._ev("Is {{corpus}} covered?", []).unbound_checklist_variables() == ["corpus"]

    @pytest.mark.django_db
    def test_attach_refuses_an_unbound_reference(self):
        from overbae.models import Evaluator, Project
        from overbae.services.eval import snapshots

        project = Project.objects.create(name="ub", slug="ub")
        ev = Evaluator.objects.create(
            project=project,
            name="Hallucination",
            kind=Evaluator.Kind.LLM_JUDGE,
            scope=Evaluator.Scope.FINAL_OUTPUT,
            checklist=[
                {"id": "a", "q": "Consistent with {{established_knowledge}}?", "weight": 1.0}
            ],
            variable_mapping=[{"var": "output", "source": "output"}],
        )
        with pytest.raises(snapshots.UngradableEvaluatorError, match="established_knowledge"):
            snapshots.build_snapshot(ev)


class TestGateMetricsExcludedFromHeadline:
    """A conformance check passes on nearly every row, so averaging it into the
    headline creates a floor: a task the model got wrong still banks credit for
    formatting its answer correctly."""

    def _summary(self, gate_metrics):
        return {
            "metrics": ["Correctness", "output-contract"],
            "gate_metrics": gate_metrics,
            "variants": {
                "v1": {
                    "metrics": {
                        "Correctness": {"mean": 0.5, "n": 10},
                        "output-contract": {"mean": 1.0, "n": 10},
                    }
                }
            },
        }

    def test_gate_metric_does_not_lift_the_headline(self):
        from overbae.services.eval import comparison

        assert comparison.overall_aggregate(self._summary(["output-contract"])).mean == 0.5

    def test_without_the_marker_it_still_averages_in(self):
        from overbae.services.eval import comparison

        assert comparison.overall_aggregate(self._summary([])).mean == 0.75

    def test_gate_still_appears_as_its_own_row(self):
        from overbae.services.eval import comparison

        summary = self._summary(["output-contract"])
        names = {r.name for r in comparison.build_comparison_rows(summary, summary)}
        assert "output-contract" in names


class TestShippedTemplatesAreGradable:
    """Every managed template is cloned by users, so one shipped without a
    checklist reproduces the invented-item defect on every fresh install."""

    def _rows(self):
        from overbae.models import Evaluator
        from overbae.services.eval.managed import MANAGED_EVALUATORS

        return [
            Evaluator(
                name=t["name"],
                kind=t["kind"],
                scope=t.get("scope", "final_output"),
                checklist=t.get("checklist", []),
                variable_mapping=t.get("variable_mapping", []),
                config=t.get("config", {}),
                requires_reference=t.get("requires_reference", False),
            )
            for t in MANAGED_EVALUATORS
        ]

    def test_every_judge_template_ships_a_checklist_or_opts_out(self):
        missing = [ev.name for ev in self._rows() if ev.requires_checklist() and not ev.checklist]
        assert missing == []

    def test_no_template_references_an_unbound_variable(self):
        unbound = {
            ev.name: ev.unbound_checklist_variables()
            for ev in self._rows()
            if ev.unbound_checklist_variables()
        }
        assert unbound == {}

    def test_checklist_items_are_uniquely_identified(self):
        for ev in self._rows():
            ids = [i["id"] for i in ev.checklist]
            assert len(ids) == len(set(ids)), ev.name


class TestProportionalScoring:
    """Faithfulness-shaped metrics are per-claim: the denominator comes from the
    output, so the score says how much was unsupported, not merely whether
    anything was."""

    def _ev(self, **kwargs):
        kwargs.setdefault("config", {gen_judge.SCORING_MODE_KEY: gen_judge.SCORING_PROPORTIONAL})
        return evaluator_stub(kind="llm_judge", checklist=[], **kwargs)

    def _draft(self, ev, claims):
        outcome = SimpleNamespace(
            parsed=gen_judge.ClaimsResult(
                claims=[gen_judge.Claim(claim=c, supported=s) for c, s in claims],
                reasoning="r",
            ),
            stats={},
            judge_trace_id="t",
        )
        return gen_judge._draft_from_claims(outcome, ev)

    def test_score_is_the_supported_fraction(self):
        draft = self._draft(self._ev(), [("a", True), ("b", True), ("c", True), ("d", False)])
        assert draft.value == 0.75
        proportion = next(s["_proportion"] for s in draft.sub_scores if "_proportion" in s)
        assert (proportion["supported"], proportion["judged"]) == (3, 4)

    def test_denominator_comes_from_the_output_not_the_config(self):
        # The same one bad claim scores differently against 2 claims and 10 —
        # the granularity a fixed checklist cannot express.
        few = self._draft(self._ev(), [("a", True), ("b", False)])
        many = self._draft(self._ev(), [(f"c{i}", i > 0) for i in range(10)])
        assert few.value == 0.5
        assert many.value == 0.9

    def test_reasoning_states_the_denominator(self):
        # Two models that differ in verbosity are scored over different claim
        # counts, so the count has to travel with the fraction.
        draft = self._draft(self._ev(), [("a", True), ("b", True), ("c", False)])
        assert "2/3 claims supported" in draft.reasoning

    def test_claims_always_come_from_the_output(self):
        # Enumerating the reference instead counts how much of the golden is
        # present, so a terse-but-correct output scores as though it were wrong.
        # Only the evidence binding varies now, never the enumeration side.
        prompt = rubric_compiler.build_claims_prompt(
            self._ev(rubric_md="r"), {"output": "o", "reference": "g"}
        )
        assert "claim the OUTPUT makes" in prompt
        assert "REFERENCE states" not in prompt

    def test_a_self_reported_confidence_is_never_a_claim(self):
        # It has no correct answer to check against, so grading it against a
        # golden measures agreement with whatever produced that golden.
        prompt = rubric_compiler.build_claims_prompt(self._ev(rubric_md="r"), {"output": "o"})
        assert "self-reported confidence" in prompt

    @pytest.mark.django_db
    def test_no_evidence_abstains_instead_of_grading_against_nothing(self):
        # Every claim would come back unsupported for a reason that has nothing
        # to do with the model, and the run would read it as near-zero grounding.
        from overbae.services.eval.evaluators.base import EvalUnit

        ev = self._ev(
            variable_mapping=[
                {"var": "output", "source": "output"},
                {"var": "context", "source": "input"},
            ]
        )
        unit = EvalUnit(trajectory={"final_output": "The payout takes two days.", "messages": []})
        drafts = gen_judge.evaluate(unit, ev, {"eval_surface": base.SURFACE_GENERATIVE})
        assert len(drafts) == 1
        assert drafts[0].value is None
        assert drafts[0].outcome == base.OUTCOME_ABSTAINED
        assert "context" in drafts[0].reasoning

    @pytest.mark.django_db
    def test_a_checklist_judge_still_grades_the_output_alone(self, monkeypatch):
        # Conciseness and toxicity need no evidence, so abstaining on an empty
        # input would silence evaluators that are working correctly.
        from overbae.services.eval import funnel as judging
        from overbae.services.eval.evaluators.base import EvalUnit

        monkeypatch.setattr(
            judging,
            "invoke_judge",
            lambda *a, **k: SimpleNamespace(
                parsed=gen_judge.ChecklistResult(
                    items=[gen_judge.ChecklistItem(id="q1", verdict=True)], reasoning="r"
                ),
                stats={},
                judge_trace_id="t",
            ),
        )
        ev = evaluator_stub(
            kind="llm_judge",
            checklist=[{"id": "q1", "q": "Is it concise?", "weight": 1.0}],
            variable_mapping=[
                {"var": "output", "source": "output"},
                {"var": "input", "source": "input"},
            ],
        )
        unit = EvalUnit(trajectory={"final_output": "Short.", "messages": []})
        drafts = gen_judge.evaluate(unit, ev, {"eval_surface": base.SURFACE_GENERATIVE})
        assert drafts[0].outcome != base.OUTCOME_ABSTAINED
        assert drafts[0].value == 1.0

    def test_no_claims_abstains_rather_than_scoring_one(self):
        # An output with nothing checkable is unmeasurable, not perfect.
        draft = self._draft(self._ev(), [])
        assert draft.value is None
        assert draft.outcome == base.OUTCOME_ABSTAINED

    def test_unjudged_claims_leave_the_denominator_and_are_reported(self):
        outcome = SimpleNamespace(
            parsed=gen_judge.ClaimsResult(
                claims=[
                    gen_judge.Claim(claim="a", supported=True),
                    gen_judge.Claim(claim="b", supported=None),
                ],
                reasoning="r",
            ),
            stats={},
            judge_trace_id="t",
        )
        draft = gen_judge._draft_from_claims(outcome, self._ev())
        assert draft.value == 1.0
        coverage = next(s["_coverage"] for s in draft.sub_scores if "_coverage" in s)
        assert coverage["unjudged_claims"] == 1

    def test_score_is_rescaled_into_the_evaluator_range(self):
        draft = self._draft(self._ev(score_min=1.0, score_max=5.0), [("a", True), ("b", False)])
        assert draft.value == 3.0

    @pytest.mark.django_db
    def test_proportional_judge_needs_no_checklist(self):
        from overbae.models import Evaluator, Project
        from overbae.services.eval import snapshots

        project = Project.objects.create(name="prop", slug="prop")
        ev = Evaluator.objects.create(
            project=project,
            name="Faithfulness",
            kind=Evaluator.Kind.LLM_JUDGE,
            scope=Evaluator.Scope.FINAL_OUTPUT,
            rubric_md="Claims must be supported by the context.",
            config={gen_judge.SCORING_MODE_KEY: gen_judge.SCORING_PROPORTIONAL},
        )
        assert ev.requires_checklist() is False
        assert snapshots.build_snapshot(ev)["name"] == "Faithfulness"


class TestJudgeSurfaceIsolation:
    """The two systems must never share a judge: a generative run scores from
    checklist verdicts, trace scoring asks the model for the score."""

    def _ev(self):
        return evaluator_stub(kind="llm_judge", checklist=[{"id": "a", "q": "?"}])

    def _routes_to(self, monkeypatch, ctx):
        called = []
        monkeypatch.setattr(judge, "evaluate", lambda *a, **k: called.append("judge") or [])
        monkeypatch.setattr(gen_judge, "evaluate", lambda *a, **k: called.append("gen_judge") or [])
        base.evaluate(EvalUnit(), self._ev(), ctx)
        return called

    def test_generative_surface_routes_to_gen_judge(self, monkeypatch):
        assert self._routes_to(monkeypatch, {"eval_surface": base.SURFACE_GENERATIVE}) == [
            "gen_judge"
        ]

    def test_trace_scoring_surface_routes_to_judge(self, monkeypatch):
        assert self._routes_to(monkeypatch, {"eval_surface": base.SURFACE_TRACE_SCORING}) == [
            "judge"
        ]

    def test_unset_surface_stays_on_trace_scoring(self, monkeypatch):
        # Trace scoring is the default so an unconverted caller keeps its
        # existing behaviour rather than silently switching scoring models.
        assert self._routes_to(monkeypatch, {}) == ["judge"]


class TestTraceJudgeFieldRefs:
    def _outcome(self, items):
        return SimpleNamespace(
            parsed=base.JudgeResult(items=items, score=0.0, reasoning="r"),
            stats={},
            judge_trace_id="",
        )

    def test_unconfigured_checklist_changes_nothing(self):
        ev = evaluator_stub(kind="llm_judge", checklist=[{"id": "a", "q": "?"}])
        items = [base.JudgeItem(id="a", verdict=False)]
        draft = judge._draft_from_outcome(self._outcome(items), ev)
        assert all("field" not in s for s in draft.sub_scores if "id" in s)

    def test_delivery_fields_parse_but_are_not_stamped(self):
        # Delivery is a ledger transition now; the judge parses the fields for
        # cached-result compatibility but never stamps them onto sub_scores.
        ev = evaluator_stub(kind="llm_judge", score_type="numeric")
        outcome = SimpleNamespace(
            parsed=base.JudgeResult(
                items=[],
                score=1.0,
                reasoning="r",
                delivery="delivered_wrong",
                clears_outstanding=False,
            ),
            stats={},
            judge_trace_id="",
        )
        draft = judge._draft_from_outcome(outcome, ev)
        assert not any(s.get("_delivery") for s in draft.sub_scores)

    def test_numeric_step_gate_sets_passed_without_capping_score(self):
        ev = evaluator_stub(
            kind="llm_judge",
            score_type="numeric",
            checklist=[
                {"id": "step-x-warranted", "q": "should this happen?", "gate": True},
                {"id": "step-x-quality", "q": "was it done well?"},
            ],
        )
        items = [
            base.JudgeItem(id="step-x-warranted", verdict=False, score=0.0),
            base.JudgeItem(id="step-x-quality", verdict=True, score=0.8),
        ]
        outcome = SimpleNamespace(
            parsed=base.JudgeResult(items=items, score=0.5, reasoning="wrong tool"),
            stats={},
            judge_trace_id="",
        )
        draft = judge._draft_from_outcome(outcome, ev)
        assert draft.value == 0.5
        assert draft.passed is False

    def test_numeric_step_gate_pass_sets_passed_true(self):
        ev = evaluator_stub(
            kind="llm_judge",
            score_type="numeric",
            checklist=[
                {"id": "step-x-warranted", "q": "should this happen?", "gate": True},
                {"id": "step-x-quality", "q": "was it done well?"},
            ],
        )
        items = [
            base.JudgeItem(id="step-x-warranted", verdict=True, score=1.0),
            base.JudgeItem(id="step-x-quality", verdict=True, score=0.6),
        ]
        outcome = SimpleNamespace(
            parsed=base.JudgeResult(items=items, score=0.8, reasoning="ok"),
            stats={},
            judge_trace_id="",
        )
        draft = judge._draft_from_outcome(outcome, ev)
        assert draft.value == 0.8
        assert draft.passed is True

    def test_numeric_outcome_gate_fail_caps_score_and_passed(self):
        ev = evaluator_stub(
            kind="llm_judge",
            score_type="numeric",
            score_min=0.0,
            score_max=1.0,
            config={"behaviour": {"behaviour_key": "happy", "role": "outcome"}},
            checklist=[{"id": "serves-running-intent", "q": "serve intent?", "gate": True}],
        )
        items = [base.JudgeItem(id="serves-running-intent", verdict=False, score=0.0)]
        outcome = SimpleNamespace(
            parsed=base.JudgeResult(items=items, score=1.0, reasoning="called the wrong tool"),
            stats={},
            judge_trace_id="",
        )
        draft = judge._draft_from_outcome(outcome, ev)
        assert draft.value == 0.0
        assert draft.passed is False
        assert not any(
            isinstance(s, dict) and (s.get("_threshold") or {}).get("gated_fail")
            for s in draft.sub_scores
        )

    def test_root_cause_stamped_without_changing_score(self):
        ev = evaluator_stub(kind="llm_judge", score_type="numeric")
        outcome = SimpleNamespace(
            parsed=base.JudgeResult(
                items=[],
                score=0.91,
                reasoning="analysis landed",
                root_cause="lookup_span",
                root_cause_reason="empty lookup payload",
            ),
            stats={},
            judge_trace_id="",
        )
        draft = judge._draft_from_outcome(outcome, ev)
        assert draft.value == 0.91
        assert draft.failure_role == "root_cause"
        stamped = next(s for s in draft.sub_scores if s.get("failure_role") == "root_cause")
        assert stamped["id"] == "lookup_span"
        assert stamped["tool"] == "lookup_span"
        assert stamped["reasoning"] == "empty lookup payload"

    def test_empty_root_cause_does_not_stamp(self):
        ev = evaluator_stub(kind="llm_judge", score_type="numeric")
        outcome = SimpleNamespace(
            parsed=base.JudgeResult(items=[], score=0.91, reasoning="pass"),
            stats={},
            judge_trace_id="",
        )
        draft = judge._draft_from_outcome(outcome, ev)
        assert draft.value == 0.91
        assert draft.failure_role == "none"
        assert not any(s.get("failure_role") == "root_cause" for s in draft.sub_scores)


class TestChecklistNotApplicable:
    def _outcome(self, items, score=1.0):
        return SimpleNamespace(
            parsed=base.JudgeResult(items=items, score=score, reasoning="r"),
            stats={},
            judge_trace_id="",
        )

    def test_not_applicable_item_never_carries_a_pass(self):
        ev = evaluator_stub(
            kind="llm_judge",
            score_type="numeric",
            checklist=[{"id": "retry", "q": "were retries handled?"}, {"id": "real", "q": "?"}],
        )
        items = [
            # A leaked verdict/score on an NA item is normalized away.
            base.JudgeItem(id="retry", verdict=True, score=1.0, not_applicable=True),
            base.JudgeItem(id="real", verdict=True, score=0.8),
        ]
        draft = judge._draft_from_outcome(self._outcome(items, score=0.8), ev)
        by_id = {s["id"]: s for s in draft.sub_scores if "id" in s}
        assert by_id["retry"]["outcome"] == "not_applicable"
        assert by_id["retry"]["verdict"] is None
        assert by_id["retry"]["score"] is None
        assert "outcome" not in by_id["real"]
        assert draft.value == 0.8

    def test_every_item_not_applicable_yields_not_applicable_never_perfect(self):
        ev = evaluator_stub(
            kind="llm_judge",
            score_type="numeric",
            checklist=[{"id": "a", "q": "?"}, {"id": "b", "q": "?"}],
        )
        items = [
            base.JudgeItem(id="a", verdict=True, score=1.0, not_applicable=True),
            base.JudgeItem(id="b", verdict=True, score=1.0, not_applicable=True),
        ]
        draft = judge._draft_from_outcome(self._outcome(items, score=1.0), ev)
        assert draft.outcome == base.OUTCOME_NOT_APPLICABLE
        assert draft.value is None
        assert "nothing applies" in draft.reasoning

    def test_judge_prompt_instructs_not_applicable_not_vacuous_pass(self):
        from overbae.services.eval.rubric_compiler import build_judge_prompt

        ev = evaluator_stub(
            kind="llm_judge",
            checklist=[{"id": "a", "q": "was the retry correct?"}],
            rubric_md="Grade it.",
        )
        prompt = build_judge_prompt(ev, {"output": "x"}, span_tree="run\n  llm_call")
        assert "not_applicable" in prompt
        assert "vacuous" not in prompt.lower()

    def test_numeric_judge_prompt_carries_anchored_scale(self):
        from overbae.services.eval.rubric_compiler import build_judge_prompt

        ev = evaluator_stub(kind="llm_judge", score_type="numeric", rubric_md="Grade it.")
        prompt = build_judge_prompt(ev, {"output": "x"})
        assert "0.6 — completes it with material gaps" in prompt
        assert "Reserve 1.0" in prompt

    def test_anchor_scale_skipped_off_the_unit_interval_and_on_boolean(self):
        from overbae.services.eval.rubric_compiler import build_judge_prompt

        boolean = evaluator_stub(kind="llm_judge", score_type="boolean", rubric_md="Grade it.")
        wide = evaluator_stub(
            kind="llm_judge", score_type="numeric", score_max=100.0, rubric_md="Grade it."
        )
        for ev in (boolean, wide):
            assert "Anchored scale" not in build_judge_prompt(ev, {"output": "x"})


class TestMissingPayloadPolicy:
    """A step judge abstains when the unit's own spans record no output payload."""

    def _stepevaluator_stub(self):
        return evaluator_stub(
            kind="llm_judge",
            score_type="numeric",
            rubric_md="Grade the step.",
            config={"behaviour": {"behaviour_key": "init", "role": "step"}},
        )

    def _payload_free_unit(self):
        return EvalUnit(
            trajectory={
                "final_output": "",
                "messages": [{"role": "user", "content": "go"}],
                "span_tree": [
                    {
                        "name": "initialize-run",
                        "type": "entry_point",
                        "outputs": None,
                        "children": [{"name": "setup", "type": "chain", "outputs": ""}],
                    }
                ],
            },
            structured={},
        )

    def test_step_judge_abstains_without_calling_the_judge(self, monkeypatch):
        from overbae.services.eval import funnel as judging

        def _boom(*args, **kwargs):
            raise AssertionError("judge must not be invoked on payload-free evidence")

        monkeypatch.setattr(judging, "invoke_judge", _boom)
        drafts = judge.evaluate(self._payload_free_unit(), self._stepevaluator_stub(), {})
        assert len(drafts) == 1
        assert drafts[0].outcome == base.OUTCOME_ABSTAINED
        assert drafts[0].value is None
        assert "Looked for" in drafts[0].reasoning
        assert "tool-call arguments" in drafts[0].reasoning

    def test_explicit_empty_output_is_a_rejection_decision_not_missing(self):
        unit = self._payload_free_unit()
        unit.trajectory["final_output"] = "[]"
        assert judge._explicit_rejection(unit)
        assert not judge._unit_output_payloads_empty(unit)

    def test_any_recorded_payload_defeats_the_policy(self):
        with_span_output = self._payload_free_unit()
        with_span_output.trajectory["span_tree"][0]["outputs"] = {"ok": True}
        assert not judge._unit_output_payloads_empty(with_span_output)

        with_tool_args = self._payload_free_unit()
        with_tool_args.trajectory["messages"].append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "name": "emit", "arguments": {"a": 1}}],
            }
        )
        assert not judge._unit_output_payloads_empty(with_tool_args)

    def test_outcome_role_judge_is_not_auto_abstained(self, monkeypatch):
        from overbae.services.eval import cascade
        from overbae.services.eval import funnel as judging

        ev = self._stepevaluator_stub()
        ev.config = {"behaviour": {"behaviour_key": "init", "role": "outcome"}}
        ev.judge_model = "judge-model-x"
        called = {}

        def _fake(*args, **kwargs):
            called["yes"] = True
            raise RuntimeError("stop before real judging")

        monkeypatch.setattr(cascade, "maybe_route", lambda *a, **k: None)
        monkeypatch.setattr(judging, "invoke_judge", _fake)
        with pytest.raises(RuntimeError):
            judge.evaluate(self._payload_free_unit(), ev, {})
        assert called


class TestTrajectoryMatch:
    def _unit(self, tools):
        return EvalUnit(
            structured={"tool_graph": {"nodes": [{"tool": t, "arguments": {}} for t in tools]}}
        )

    def test_superset_pass(self):
        u = self._unit(["a", "b", "c"])
        ev = evaluator_stub(
            kind="trajectory",
            config={
                "mode": "match",
                "trajectory_match_mode": "superset",
                "reference_trajectory": [{"tool": "a", "arguments": {}}],
            },
        )
        assert trajectory.evaluate(u, ev, {})[0].passed is True

    def test_strict_order_fail(self):
        u = self._unit(["b", "a"])
        ev = evaluator_stub(
            kind="trajectory",
            config={
                "mode": "match",
                "trajectory_match_mode": "strict",
                "reference_trajectory": [
                    {"tool": "a", "arguments": {}},
                    {"tool": "b", "arguments": {}},
                ],
            },
        )
        assert trajectory.evaluate(u, ev, {})[0].passed is False

    def test_unordered_pass(self):
        u = self._unit(["b", "a"])
        ev = evaluator_stub(
            kind="trajectory",
            config={
                "mode": "match",
                "trajectory_match_mode": "unordered",
                "reference_trajectory": [
                    {"tool": "a", "arguments": {}},
                    {"tool": "b", "arguments": {}},
                ],
            },
        )
        assert trajectory.evaluate(u, ev, {})[0].passed is True

    def test_empty_trajectory_with_reference_abstains(self):
        u = self._unit([])
        ev = evaluator_stub(
            kind="trajectory",
            config={
                "mode": "match",
                "trajectory_match_mode": "superset",
                "reference_trajectory": [{"tool": "a", "arguments": {}}],
            },
        )
        draft = trajectory.evaluate(u, ev, {})[0]
        assert draft.value is None

    def test_wrong_calls_against_reference_still_score_zero(self):
        u = self._unit(["x"])
        ev = evaluator_stub(
            kind="trajectory",
            config={
                "mode": "match",
                "trajectory_match_mode": "superset",
                "reference_trajectory": [{"tool": "a", "arguments": {}}],
            },
        )
        draft = trajectory.evaluate(u, ev, {})[0]
        assert draft.value == 0.0
        assert draft.passed is False


class TestStatistical:
    def test_accuracy(self):
        ev = evaluator_stub(kind="statistical", config={"metric": "accuracy"})
        d = statistical.aggregate(["a", "b", "a"], ["a", "b", "b"], ev)
        assert abs(d.value - 2 / 3) < 1e-9

    def test_f1(self):
        ev = evaluator_stub(kind="statistical", config={"metric": "f1", "average": "macro"})
        d = statistical.aggregate(["a", "b"], ["a", "a"], ev)
        assert d.value is not None

    def test_bleu_identical_is_one(self):
        ev = evaluator_stub(kind="statistical", config={"metric": "bleu"})
        d = statistical.aggregate(["the cat sat on the mat"], ["the cat sat on the mat"], ev)
        assert d.value > 0.9

    def test_confusion_matrix(self):
        cm = statistical.confusion_matrix(["a", "b"], ["a", "a"])
        assert "labels" in cm and "matrix" in cm

    def test_per_class_metrics_known_fixture(self):
        # Hand-computed: pred a twice (1 right), b twice (both right), c once.
        preds = ["a", "a", "b", "b", "c"]
        refs = ["a", "b", "b", "b", "c"]
        out = statistical.per_class_metrics(preds, refs)
        by_label = {c["label"]: c for c in out["classes"]}
        assert by_label["a"] == {
            "label": "a",
            "precision": 0.5,
            "recall": 1.0,
            "f1": 0.666667,
            "support": 1,
        }
        assert by_label["b"]["precision"] == 1.0
        assert abs(by_label["b"]["recall"] - 2 / 3) < 1e-5
        assert by_label["b"]["f1"] == 0.8
        assert by_label["b"]["support"] == 3
        assert by_label["c"] == {
            "label": "c",
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "support": 1,
        }
        agg = out["aggregates"]
        assert agg["accuracy"] == 0.8
        assert agg["n"] == 5
        assert agg["micro"]["f1"] == 0.8  # micro == accuracy for single-label
        assert abs(agg["macro"]["precision"] - (0.5 + 1 + 1) / 3) < 1e-5
        assert agg["weighted"]["precision"] == 0.9  # (0.5·1 + 1·3 + 1·1)/5
        cm = out["confusion_matrix"]
        assert cm["labels"] == ["a", "b", "c"]
        assert cm["matrix"] == [[1, 0, 0], [1, 2, 0], [0, 0, 1]]

    def test_per_class_metrics_empty_pairs_is_empty(self):
        assert statistical.per_class_metrics([], []) == {}
        assert statistical.per_class_metrics(["a"], [None]) == {}

    def test_emit_prediction_default_uses_final_output(self):
        u = EvalUnit(trajectory={"final_output": "positive"}, expected="positive")
        ev = evaluator_stub(kind="statistical", config={"metric": "accuracy"})
        draft = judge.emit_prediction(u, ev)[0]
        assert draft.sub_scores[0]["prediction"] == "positive"
        assert draft.sub_scores[0]["reference"] == "positive"

    def test_emit_prediction_targets_classification_field(self):
        final = json.dumps({"reasoning": "...", "label": "negative"})
        u = EvalUnit(trajectory={"final_output": final}, expected="negative")
        ev = evaluator_stub(
            kind="statistical", config={"metric": "accuracy", "prediction_field": "label"}
        )
        draft = judge.emit_prediction(u, ev)[0]
        assert draft.sub_scores[0]["prediction"] == "negative"
        # The prediction must stay at sub_scores[0] for aggregation.
        assert any("_resolution" in s for s in draft.sub_scores)

    def test_duplicate_rate_counts_repeated_outputs(self):
        ev = evaluator_stub(kind="statistical", config={"metric": "duplicate_rate"})
        # "a" appears twice → 2 of 4 samples share an output.
        d = statistical.aggregate(["a", "a", "b", "c"], [None, None, None, None], ev)
        assert abs(d.value - 0.5) < 1e-9
        assert d.scope == "dataset"
        assert d.sub_scores == [{"metric": "duplicate_rate", "n": 4}]

    def test_duplicate_rate_all_unique_is_zero(self):
        ev = evaluator_stub(kind="statistical", config={"metric": "duplicate_rate"})
        d = statistical.aggregate(["a", "b", "c"], [], ev)
        assert d.value == 0.0

    def test_duplicate_rate_ignores_references(self):
        # Must not require references, unlike accuracy/f1/etc.
        ev = evaluator_stub(kind="statistical", config={"metric": "duplicate_rate"})
        d = statistical.aggregate(["x", "x"], [], ev)
        assert d.value == 1.0

    def test_duplicate_rate_applies_output_normalize(self):
        ev = evaluator_stub(
            kind="statistical",
            config={"metric": "duplicate_rate", "output_normalize": "first_word"},
        )
        d = statistical.aggregate(["yes indeed", "Yes certainly", "no"], [], ev)
        assert abs(d.value - 2 / 3) < 1e-9


class TestRanking:
    def test_rollup_delta_and_regression(self):
        scores = [
            {
                "variant_id": "A",
                "variant_label": "base",
                "name": "acc",
                "value": 0.8,
                "passed": True,
            },
            {
                "variant_id": "B",
                "variant_label": "ft",
                "name": "acc",
                "value": 0.6,
                "passed": False,
            },
        ]
        out = rollup(scores, baseline_variant_id="A")
        cell = out["variants"]["B"]["metrics"]["acc"]
        assert cell["regression"] is True
        assert abs(cell["delta"] + 0.2) < 1e-9

    def test_bradley_terry_orders_winner_first(self):
        matches = [{"a": "A", "b": "B", "winner": "B"}] * 5 + [{"a": "A", "b": "B", "winner": "A"}]
        bt = bradley_terry(matches, rounds=20)
        assert bt["ranking"][0] == "B"
        assert bt["ratings"]["B"]["rating"] > bt["ratings"]["A"]["rating"]


class TestEvidenceInference:
    def test_output_field_judge_is_model_output(self):
        vm = [{"var": "recommendations", "source": "output", "jsonpath": "$.recommendations"}]
        req = evidence.infer_evidence_requirement(
            kind="llm_judge", scope="final_output", variable_mapping=vm
        )
        assert req == evidence.MODEL_OUTPUT

    def test_output_field_var_name_is_model_output(self):
        vm = [{"var": "summary", "source": "output"}]
        req = evidence.infer_evidence_requirement(
            kind="llm_judge", scope="final_output", variable_mapping=vm
        )
        assert req == evidence.MODEL_OUTPUT

    def test_structured_source_is_harness_artifact(self):
        # Replay cannot reproduce the harness-assembled structured object.
        vm = [{"var": "trace", "source": "structured"}]
        req = evidence.infer_evidence_requirement(
            kind="llm_judge", scope="final_output", variable_mapping=vm
        )
        assert req == evidence.HARNESS_ARTIFACT

    def test_required_keys_schema_gate_is_model_output(self):
        # A candidate model can produce the contract keys itself, so the gate runs
        # in generate mode.
        req = evidence.infer_evidence_requirement(
            kind="deterministic",
            scope="final_output",
            config={"check": "json_schema_valid", "required_keys": ["summary"]},
        )
        assert req == evidence.MODEL_OUTPUT

    def test_bare_json_validity_is_model_output(self):
        req = evidence.infer_evidence_requirement(
            kind="deterministic", scope="final_output", config={"check": "json_schema_valid"}
        )
        assert req == evidence.MODEL_OUTPUT

    def test_trajectory_scope_is_trajectory(self):
        req = evidence.infer_evidence_requirement(kind="llm_judge", scope="trajectory")
        assert req == evidence.TRAJECTORY

    def test_compose_judge_evaluator_kwargs_infers_model_output_by_default(self):
        from overbae.services.eval.rubric_compiler import compose_judge_evaluator_kwargs

        kwargs = compose_judge_evaluator_kwargs(
            {
                "project": None,
                "capability": None,
                "name": "output-quality",
                "evaluation_prompt": "Grade the final answer.",
                "score_type": "numeric",
                "applicable_roles": ["trace_scoring"],
            }
        )
        assert kwargs["scope"] == "final_output"
        assert kwargs["evidence_requirement"] == evidence.MODEL_OUTPUT

    def test_compose_judge_evaluator_kwargs_infers_trajectory_for_behaviour_judge(self):
        from types import SimpleNamespace

        from overbae.services.eval.rubric_compiler import compose_judge_evaluator_kwargs

        behaviour = SimpleNamespace(key="agent-run-loop")
        kwargs = compose_judge_evaluator_kwargs(
            {
                "project": None,
                "capability": None,
                "name": "judge-self-report-alignment",
                "evaluation_prompt": "Compare done.success to _judge_trace verdict.",
                "score_type": "boolean",
                "applicable_roles": ["trace_scoring"],
                "behaviour": behaviour,
            }
        )
        assert kwargs["scope"] == "trajectory"
        assert kwargs["evidence_requirement"] == evidence.TRAJECTORY

    def test_tool_calls_source_is_trajectory(self):
        vm = [{"var": "tool_calls", "source": "trajectory", "jsonpath": "$.messages.tool_calls"}]
        req = evidence.infer_evidence_requirement(
            kind="llm_judge", scope="trajectory", variable_mapping=vm
        )
        assert req == evidence.TRAJECTORY

    def test_tool_selection_check_is_trajectory(self):
        req = evidence.infer_evidence_requirement(
            kind="deterministic", scope="final_output", config={"check": "tool_selection"}
        )
        assert req == evidence.TRAJECTORY

    def test_requires_reference_is_reference(self):
        req = evidence.infer_evidence_requirement(
            kind="deterministic", scope="final_output", requires_reference=True
        )
        assert req == evidence.REFERENCE

    def test_statistical_similarity_is_reference(self):
        req = evidence.infer_evidence_requirement(
            kind="statistical",
            scope="dataset",
            requires_reference=True,
            config={"metric": "rouge_l"},
        )
        assert req == evidence.REFERENCE

    def test_plain_relevance_judge_is_model_output(self):
        vm = [{"var": "input", "source": "input"}, {"var": "output", "source": "output"}]
        req = evidence.infer_evidence_requirement(
            kind="llm_judge", scope="final_output", variable_mapping=vm
        )
        assert req == evidence.MODEL_OUTPUT


class TestApplicability:
    def test_harness_artifact_not_applicable_in_generate(self):
        assert evidence.is_applicable(evidence.HARNESS_ARTIFACT, "generate") is False

    def test_harness_artifact_applicable_in_existing(self):
        assert evidence.is_applicable(evidence.HARNESS_ARTIFACT, "existing") is True

    def test_trajectory_and_model_output_applicable_in_both_modes(self):
        for req in (evidence.TRAJECTORY, evidence.MODEL_OUTPUT, evidence.REFERENCE):
            assert evidence.is_applicable(req, "generate") is True
            assert evidence.is_applicable(req, "existing") is True

    def test_warnings_flag_harness_artifact_on_generate(self):
        evaluators = [{"name": "parse-and-validate", "evidence_requirement": "harness_artifact"}]
        variants = [
            {"label": "gpt-5 gen", "mode": "generate"},
            {"label": "prod", "mode": "existing"},
        ]
        out = evidence.compatibility_warnings(evaluators, variants)
        incompatible = [w for w in out if w["severity"] == "incompatible"]
        assert len(incompatible) == 1
        assert incompatible[0]["variant"] == "gpt-5 gen"

    def test_warnings_flag_reference_need(self):
        evaluators = [{"name": "Exact Match", "evidence_requirement": "reference"}]
        out = evidence.compatibility_warnings(evaluators, [{"label": "v", "mode": "existing"}])
        assert any(w["severity"] == "needs_reference" for w in out)

    def test_needs_reference_suppressed_when_context_supplies_it(self):
        evaluators = [{"name": "Exact Match", "evidence_requirement": "reference"}]
        out = evidence.compatibility_warnings(
            evaluators, [{"label": "v", "mode": "existing"}], reference_available=True
        )
        assert not any(w["severity"] == "needs_reference" for w in out)

    def test_warnings_empty_for_model_output(self):
        evaluators = [{"name": "Toxicity", "evidence_requirement": "model_output"}]
        out = evidence.compatibility_warnings(evaluators, [{"label": "v", "mode": "generate"}])
        assert out == []

    def test_output_quality_judge_spec_is_generate_compatible(self):
        from overbae.services.eval.specs import EvaluatorSpec, SpecProvenance

        spec = EvaluatorSpec(
            name="output-contract-semantics",
            kind="llm_judge",
            scope="final_output",
            rubric_md="Do the fields in {{summary}} mean what the contract says?",
            variable_mapping=[{"var": "summary", "source": "output", "jsonpath": "$.summary"}],
            provenance=SpecProvenance(
                source="codebase_card.success_criteria[0]",
                generator="tier1_llm@v1",
                surface_area="output_contract",
            ),
        )
        kwargs = spec.to_evaluator_kwargs()
        assert kwargs["evidence_requirement"] == evidence.MODEL_OUTPUT
        assert evidence.is_applicable(kwargs["evidence_requirement"], evidence.GENERATE)
        out = evidence.compatibility_warnings(
            [{"name": spec.name, "evidence_requirement": kwargs["evidence_requirement"]}],
            [{"label": "openai/gpt-3.5-turbo", "mode": "generate"}],
        )
        assert out == []


class TestGradedScoresHaveNoFailureGates:
    """``pass_threshold`` exists only for boolean evaluators. Numeric
    behaviour outcome/step judges may keep a ``gate`` (sets ``passed``)."""

    @staticmethod
    def _spec(**overrides):
        from overbae.services.eval.specs import EvaluatorSpec, SpecProvenance

        payload = {
            "name": "graded-judge",
            "kind": "llm_judge",
            "scope": "final_output",
            "rubric_md": "Grade the quality of {{output}}.",
            "checklist": [
                {"id": "c1", "q": "Is it good?", "weight": 1.0, "gate": True},
                {"id": "c2", "q": "Is it complete?", "weight": 1.0, "gate": False},
            ],
            "pass_threshold": 0.7,
            "provenance": SpecProvenance(
                source="codebase_card.success_criteria[0]",
                generator="tier1_llm@v1",
                surface_area="output_contract",
            ),
            **overrides,
        }
        return EvaluatorSpec.model_validate(payload)

    def test_numeric_spec_strips_gates_and_threshold(self):
        spec = self._spec(score_type="numeric")
        assert spec.pass_threshold is None
        assert all(item.gate is False for item in spec.checklist)
        kwargs = spec.to_evaluator_kwargs()
        assert kwargs["pass_threshold"] is None
        assert all(item["gate"] is False for item in kwargs["checklist"])

    def test_numeric_step_spec_keeps_warranted_gate(self):
        spec = self._spec(
            score_type="numeric",
            config={"behaviour": {"behaviour_key": "happy", "role": "step", "anchor_segment": []}},
        )
        assert spec.pass_threshold is None
        assert spec.checklist[0].gate is True
        assert spec.checklist[1].gate is False

    def test_numeric_outcome_spec_keeps_intent_gate(self):
        spec = self._spec(
            score_type="numeric",
            config={
                "behaviour": {"behaviour_key": "happy", "role": "outcome", "anchor_segment": []}
            },
        )
        assert spec.pass_threshold is None
        assert spec.checklist[0].gate is True

    def test_categorical_spec_strips_gates_and_threshold(self):
        spec = self._spec(score_type="categorical")
        assert spec.pass_threshold is None
        assert all(item.gate is False for item in spec.checklist)

    def test_boolean_spec_keeps_gates_and_threshold(self):
        spec = self._spec(score_type="boolean", pass_threshold=1.0)
        assert spec.pass_threshold == 1.0
        assert spec.checklist[0].gate is True

    def test_normalize_numeric_never_derives_passed_for_numeric(self):
        from overbae.services.eval.evaluators import base

        ev = evaluator_stub(score_type="numeric", pass_threshold=0.7)
        assert base.normalize_numeric(0.9, ev) == (0.9, None)
        assert base.normalize_numeric(0.1, ev) == (0.1, None)

    def test_normalize_numeric_derives_passed_for_boolean(self):
        from overbae.services.eval.evaluators import base

        ev = evaluator_stub(score_type="boolean", pass_threshold=None)
        assert base.normalize_numeric(1.0, ev) == (1.0, True)
        assert base.normalize_numeric(0.0, ev) == (0.0, False)
        ev2 = evaluator_stub(score_type="boolean", pass_threshold=0.5)
        assert base.normalize_numeric(0.6, ev2) == (0.6, True)


class TestSanitation:
    def test_strips_internal_symbol_token(self):
        from overbae.services.eval.sanitation import sanitize_authored_text

        cleaned, removed = sanitize_authored_text(
            "Is the output valid JSON with the (_LLM_OUTPUT_KEYS) present?"
        )
        assert "_LLM_OUTPUT_KEYS" not in cleaned
        assert removed == ["_LLM_OUTPUT_KEYS"]

    def test_strips_unsubstituted_fstring_remnant(self):
        from overbae.services.eval.sanitation import sanitize_authored_text

        cleaned, removed = sanitize_authored_text("rows must equal {total_rows} exactly")
        assert "{total_rows}" not in cleaned
        assert removed == ["{total_rows}"]

    def test_preserves_rubric_double_brace_variables(self):
        from overbae.services.eval.sanitation import sanitize_authored_text

        text = "Compare {{output}} against {{reference}}"
        cleaned, removed = sanitize_authored_text(text)
        assert cleaned == text
        assert removed == []

    def test_preserves_legitimate_uppercase_terms(self):
        from overbae.services.eval.sanitation import sanitize_authored_text

        text = "Output must be valid JSON and contain no PII or HTTP links"
        cleaned, removed = sanitize_authored_text(text)
        assert cleaned == text
        assert removed == []

    def test_contains_leaked_token(self):
        from overbae.services.eval.sanitation import contains_leaked_token

        assert contains_leaked_token("see _SECRET_KEYS") is True
        assert contains_leaked_token("plain question") is False

    def test_whole_blob_anchors_survive(self):
        from overbae.services.eval.sanitation import contains_leaked_token, sanitize_authored_text

        for token in ("{input}", "{output}", "{final_output}", "{reference}"):
            text = f"Grade {token} against the email"
            cleaned, removed = sanitize_authored_text(text)
            assert cleaned == text, token
            assert removed == []
            assert contains_leaked_token(text) is False

    def test_dangling_field_name_and_internal_symbol_are_stripped(self):
        from overbae.services.eval.sanitation import contains_leaked_token, sanitize_authored_text

        text = "Does {amount} equal _ALL_CAPS in the output?"
        cleaned, removed = sanitize_authored_text(text)
        assert "{amount}" not in cleaned
        assert "_ALL_CAPS" not in cleaned
        assert set(removed) == {"{amount}", "_ALL_CAPS"}
        assert contains_leaked_token(text) is True
        assert contains_leaked_token(cleaned) is False

    def test_contains_leaked_token_agrees_with_sanitize(self):
        from overbae.services.eval.sanitation import contains_leaked_token, sanitize_authored_text

        cases = [
            "Grade {input} against {output}",
            "See {final_output} and {reference}",
            "rows must equal {amount} exactly",
            "Valid JSON with the _SECRET_KEYS present",
            "Compare {{output}} against {{reference}}",
            "plain question",
            "See {output.amount} vs {input}",
        ]
        for text in cases:
            _cleaned, removed = sanitize_authored_text(text)
            assert contains_leaked_token(text) is bool(removed), text

    def test_verbatim_lift_with_whole_blob_anchors_is_not_mangled(self):
        from overbae.services.eval.sanitation import contains_leaked_token, sanitize_authored_text

        text = (
            "Are vendor, invoiceNumber, amount, or dueDate only present in {output} "
            "when grounded in explicit or unambiguous text in {input} "
            "(i.e., not clearly invented by the agent)?"
        )
        cleaned, removed = sanitize_authored_text(text)
        assert cleaned == text
        assert removed == []
        assert contains_leaked_token(text) is False

    def test_dotted_field_anchor_survives_beside_whole_blob(self):
        from overbae.services.eval.sanitation import contains_leaked_token, sanitize_authored_text

        text = (
            "summary addresses the actual email content and references invoice "
            "signals when present (See {output.summary} vs {input})"
        )
        cleaned, removed = sanitize_authored_text(text)
        assert cleaned == text
        assert removed == []
        assert contains_leaked_token(text) is False

    def test_empty_code_span_from_stripped_field_fills_output_then_input(self):
        from overbae.services.eval.sanitation import sanitize_authored_text

        cleaned, removed = sanitize_authored_text(
            "Does `{trader_investment_plan}` contain a proposal for the plan in `{market_report}`?"
        )
        assert cleaned == ("Does {output} contain a proposal for the plan in {input}?")
        assert "{trader_investment_plan}" not in cleaned
        assert "``" not in cleaned
        assert "{trader_investment_plan}" in removed
        assert "{market_report}" in removed
        assert removed.count("``") == 2

    def test_already_empty_code_spans_fill_without_a_field_token(self):
        from overbae.services.eval.sanitation import contains_leaked_token, sanitize_authored_text

        cleaned, removed = sanitize_authored_text(
            "Are the claims in `` faithful to `` and `{reference}`?"
        )
        assert cleaned == "Are the claims in {output} faithful to {input} and `{reference}`?"
        assert removed == ["``", "``"]
        assert contains_leaked_token("Are the claims in `` faithful to `` and `{reference}`?")

    def test_already_mangled_question_round_trips_unchanged(self):
        from overbae.services.eval.sanitation import sanitize_authored_text

        text = (
            "Are vendor, invoiceNumber, amount, or dueDate only present in when "
            "grounded in explicit or unambiguous text in (i.e., not clearly invented "
            "by the agent)?"
        )
        cleaned, removed = sanitize_authored_text(text)
        assert cleaned == text
        assert removed == []
        mangled = (
            "summary addresses the actual email content and references invoice "
            "signals when present (See {output.summary} vs )"
        )
        cleaned, removed = sanitize_authored_text(mangled)
        assert cleaned == mangled
        assert removed == []

    def test_confidence_calibration_items_are_detected(self):
        from overbae.services.eval.sanitation import grades_stated_confidence

        dropped = [
            "Confidence score calibration: higher when explicit invoice cues exist; lower when evidence is weak",
            "4) Confidence score in [0,1] and calibrated to evidence — does its magnitude reflect the evidence strength",
            "4) confidence spread reflects evidence strength — Does {output.confidence} vary appropriately (not always 0.9+)",
        ]
        for q in dropped:
            assert grades_stated_confidence({"id": "c", "q": q}) is True, q
        assert (
            grades_stated_confidence(
                {"id": "range", "q": "{output.confidence} is a number in [0,1]"}
            )
            is False
        )

    def test_mechanical_reference_compare_is_detected(self):
        from overbae.services.eval.sanitation import is_mechanical_field_compare

        covered = {"amount"}
        assert (
            is_mechanical_field_compare(
                "Compare {output.amount} to {reference.amount} within abs tol 0.01",
                covered,
            )
            is True
        )
        assert (
            is_mechanical_field_compare(
                "amount matches an explicit 'amount due' line in the email rather than a subtotal",
                covered,
            )
            is False
        )
        assert (
            is_mechanical_field_compare(
                "Compare {output.amount} to {reference.amount} within abs tol 0.01",
                (),
            )
            is False
        )


def _unit(final_output="", messages=None, expected=None, reference_context=None):
    traj = {
        "final_output": final_output,
        "messages": messages or [{"role": "user", "content": "question"}],
        "metadata": {},
    }
    return EvalUnit(
        trajectory=traj,
        structured={},
        expected=expected,
        reference_context=reference_context or {},
    )


def _spec(name, variable_mapping):
    return SimpleNamespace(name=name, variable_mapping=variable_mapping)


class TestBindingDryRun:
    def test_healthy_output_binding_is_green(self):
        from overbae.services.eval import binding_check

        units = [_unit(final_output='{"recommendations": ["a", "b"]}') for _ in range(3)]
        spec = _spec("ok", [{"var": "output", "source": "output", "jsonpath": ""}])
        health = binding_check.dry_run_spec(spec, units)
        assert health.status == binding_check.GREEN
        assert health.failing == []

    def test_string_source_with_jsonpath_is_red(self):
        # source="input" is a string; the jsonpath can never traverse it.
        from overbae.services.eval import binding_check

        units = [_unit(final_output='{"summary": {"rows": 22}}') for _ in range(3)]
        spec = _spec(
            "rosetta",
            [
                {"var": "output", "source": "output", "jsonpath": ""},
                {
                    "var": "dataset_total",
                    "source": "input",
                    "jsonpath": "$.dataset_facts.total_rows_exact",
                },
            ],
        )
        health = binding_check.dry_run_spec(spec, units)
        assert health.status == binding_check.RED
        assert [v.var for v in health.failing] == ["dataset_total"]
        assert health.failing[0].fix_hint

    def test_grounding_var_mis_sourced_proposes_reference(self):
        from overbae.services.eval import binding_check

        units = [_unit(final_output='{"x": 1}') for _ in range(3)]
        spec = _spec(
            "g",
            [{"var": "dataset_facts", "source": "input", "jsonpath": "$.volume_and_tokens"}],
        )
        health = binding_check.dry_run_spec(spec, units)
        assert health.status == binding_check.RED
        repair = health.failing[0].proposed_repair
        assert repair is not None
        assert repair["set"] == {"source": "reference"}

    def test_wrapper_key_strip_proposed(self):
        from overbae.services.eval import binding_check

        units = [_unit(final_output='{"recommendations": ["a"]}') for _ in range(3)]
        spec = _spec(
            "w",
            [{"var": "recs", "source": "output", "jsonpath": "$.analysis.recommendations"}],
        )
        health = binding_check.dry_run_spec(spec, units)
        failing_or_weak = health.failing + health.weak
        assert failing_or_weak
        repair = failing_or_weak[0].proposed_repair
        assert repair is not None and repair["action"] == "strip_wrapper"
        assert repair["set"]["jsonpath"] == "$.recommendations"

    def test_partial_resolution_is_amber(self):
        from overbae.services.eval import binding_check

        units = [
            _unit(final_output='{"recommendations": ["a"]}'),
            _unit(final_output=""),
        ]
        spec = _spec("p", [{"var": "output", "source": "output", "jsonpath": ""}])
        health = binding_check.dry_run_spec(spec, units)
        assert health.status == binding_check.AMBER

    def test_no_units_is_unknown(self):
        from overbae.services.eval import binding_check

        spec = _spec("u", [{"var": "output", "source": "output", "jsonpath": ""}])
        health = binding_check.dry_run_spec(spec, [])
        assert health.status == binding_check.UNKNOWN

    def test_empty_mapping_is_green(self):
        from overbae.services.eval import binding_check

        spec = _spec("d", [])
        health = binding_check.dry_run_spec(spec, [_unit(final_output="x")])
        assert health.status == binding_check.GREEN

    def test_dry_run_bindings_keyed_by_name(self):
        from overbae.services.eval import binding_check

        units = [_unit(final_output='{"a": 1}') for _ in range(3)]
        specs = [
            _spec("a", [{"var": "output", "source": "output", "jsonpath": ""}]),
            _spec("b", [{"var": "x", "source": "input", "jsonpath": "$.y"}]),
        ]
        out = binding_check.dry_run_bindings(specs, units)
        assert set(out) == {"a", "b"}
        assert out["a"].status == binding_check.GREEN
        assert out["b"].status == binding_check.RED


class TestModeAwareBindingDryRun:
    def test_generate_mode_skips_empty_output_binding(self):
        # In generate mode the model still has to produce the output at grade time,
        # so an empty creation-time output is unknown rather than failing.
        from overbae.services.eval import binding_check

        units = [_unit(final_output="") for _ in range(3)]
        spec = _spec("gen", [{"var": "output", "source": "output", "jsonpath": ""}])
        health = binding_check.dry_run_spec(spec, units, mode=binding_check.GENERATE)
        assert health.status == binding_check.SKIPPED
        assert health.variables[0].status == binding_check.SKIPPED
        assert health.failing == []
        assert [v.var for v in health.skipped] == ["output"]

    def test_existing_mode_flags_empty_output_binding(self):
        from overbae.services.eval import binding_check

        units = [_unit(final_output="") for _ in range(3)]
        spec = _spec("ex", [{"var": "output", "source": "output", "jsonpath": ""}])
        health = binding_check.dry_run_spec(spec, units, mode=evidence.EXISTING)
        assert health.status == binding_check.RED
        # An unset mode keeps the strict behavior.
        assert binding_check.dry_run_spec(spec, units).status == binding_check.RED

    def test_generate_mode_still_red_for_text_source_jsonpath(self):
        from overbae.services.eval import binding_check

        units = [_unit(final_output='{"summary": {"rows": 22}}') for _ in range(3)]
        spec = _spec("t", [{"var": "n", "source": "input", "jsonpath": "$.dataset_facts.total"}])
        health = binding_check.dry_run_spec(spec, units, mode=binding_check.GENERATE)
        assert health.status == binding_check.RED
        assert [v.var for v in health.failing] == ["n"]

    def test_generate_mode_red_when_output_present_but_key_missing(self):
        from overbae.services.eval import binding_check

        units = [_unit(final_output='{"present": 1}') for _ in range(3)]
        spec = _spec("m", [{"var": "missing", "source": "output", "jsonpath": "$.absent_key"}])
        health = binding_check.dry_run_spec(spec, units, mode=binding_check.GENERATE)
        assert health.status == binding_check.RED


class TestGroundingResourceRepair:
    def test_proposes_grounding_resource_when_value_in_context(self):
        from overbae.services.eval import binding_check

        ctx = {"dataset_facts": {"volume_and_tokens": {"total_rows": 22}}}
        units = [_unit(final_output='{"x": 1}', reference_context=ctx) for _ in range(3)]
        spec = _spec(
            "rows",
            [
                {
                    "var": "dataset_facts_total_rows",
                    "source": "input",
                    "jsonpath": "$.dataset_facts.total_rows_exact",
                }
            ],
        )
        health = binding_check.dry_run_spec(spec, units)
        var = health.variables[0]
        assert var.status == binding_check.RED
        repair = var.proposed_repair
        assert repair is not None
        assert repair["action"] == "resource_grounding"
        assert repair["set"]["source"] == "reference"
        assert repair["set"]["jsonpath"] == "$.dataset_facts.volume_and_tokens.total_rows"
        assert var.repair_kind == binding_check.REPAIR_AUTO

    def test_abstains_when_value_absent_from_grounding(self):
        from overbae.services.eval import binding_check

        ctx = {"dataset_facts": {"volume_and_tokens": {"other_metric": 5}}}
        units = [_unit(final_output='{"x": 1}', reference_context=ctx) for _ in range(3)]
        spec = _spec(
            "rows",
            [
                {
                    "var": "dataset_facts_total_rows",
                    "source": "input",
                    "jsonpath": "$.dataset_facts.total_rows_exact",
                }
            ],
        )
        health = binding_check.dry_run_spec(spec, units)
        var = health.variables[0]
        assert var.status == binding_check.RED
        assert (var.proposed_repair or {}).get("action") != "resource_grounding"
        assert var.repair_kind == binding_check.REPAIR_REGENERATE

    def test_refuses_semantic_conflation(self):
        # A per-row `rows` must never be auto-repaired to the dataset-level
        # `total_rows` — different concept, no safe deterministic fix.
        from overbae.services.eval import binding_check

        ctx = {"dataset_facts": {"volume_and_tokens": {"total_rows": 22}}}
        units = [_unit(final_output='{"x": 1}', reference_context=ctx) for _ in range(3)]
        spec = _spec("c", [{"var": "rows", "source": "reference", "jsonpath": "$.summary.rows"}])
        health = binding_check.dry_run_spec(spec, units)
        var = health.variables[0]
        assert var.status == binding_check.RED
        assert (var.proposed_repair or {}).get("action") != "resource_grounding"


class TestChatmlWrappedExpectedResolves:
    def test_chatml_expected_summary_rows_now_resolves_green(self):
        from overbae.services.eval import binding_check

        expected = {"role": "assistant", "content": json.dumps({"summary": {"rows": 17}})}
        ctx = {"dataset_facts": {"volume_and_tokens": {"total_rows": 22}}}
        units = [
            _unit(final_output='{"x": 1}', expected=expected, reference_context=ctx)
            for _ in range(3)
        ]
        spec = _spec(
            "expected_output_json_and_summary_rows_match",
            [{"var": "parsed_summary_rows", "source": "expected", "jsonpath": "$.summary.rows"}],
        )
        for mode in (None, binding_check.GENERATE, evidence.EXISTING):
            health = binding_check.dry_run_spec(spec, units, mode=mode)
            var = health.variables[0]
            assert var.status == binding_check.GREEN, mode
            assert var.repair_kind != binding_check.REPAIR_REGENERATE, mode
            assert health.needs_regeneration == [], mode

    def test_chatml_expected_resolves_to_the_per_row_value(self):
        unit = _unit(
            final_output='{"x": 1}',
            expected={
                "role": "assistant",
                "content": json.dumps({"summary": {"rows": 15000}}),
            },
        )
        det = base.resolve_variables_detailed(
            unit,
            [{"var": "parsed_summary_rows", "source": "expected", "jsonpath": "$.summary.rows"}],
        )
        assert det["parsed_summary_rows"].value == "15000"
        assert det["parsed_summary_rows"].strategy == "explicit_path"


@pytest.mark.django_db
def test_eval_set_expansion_matches_legacy_evaluator_ids_path():
    from overbae.models import (
        Capability,
        EvalRun,
        EvalSet,
        EvalSetMember,
        Evaluator,
        Project,
        RunEvaluator,
    )
    from overbae.services.eval import snapshots
    from overbae.services.eval.eval_set import (
        expand_to_run_evaluators,
        runnable_capability_evaluators,
    )

    project = Project.objects.create(name="evalset", slug="evalset")
    capability = Capability.objects.create(project=project, name="a", slug="a")
    judge = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Output Quality",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        rubric_md="Grade the output.",
        checklist=[{"id": "q1", "q": "?", "weight": 1.0}],
    )
    exact = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Exact Match",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        config={"check": "exact_match"},
    )

    eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
    EvalSetMember.objects.create(eval_set=eval_set, evaluator=judge, role="generative", order=0)
    EvalSetMember.objects.create(eval_set=eval_set, evaluator=exact, role="generative", order=1)

    # Legacy flat path — mirrors EvalRunSerializer.create()'s evaluator_ids branch.
    legacy_run = EvalRun.objects.create(project=project, name="legacy")
    for order, evaluator in enumerate([judge, exact]):
        RunEvaluator.objects.create(
            run=legacy_run,
            evaluator=evaluator,
            snapshot=snapshots.build_snapshot(evaluator),
            order=order,
        )

    set_run = EvalRun.objects.create(project=project, name="from-set", eval_set=eval_set)
    expand_to_run_evaluators(set_run, eval_set)

    legacy = list(legacy_run.run_evaluators.order_by("order").values_list("snapshot", flat=True))
    expanded = list(set_run.run_evaluators.order_by("order").values_list("snapshot", flat=True))
    assert expanded == legacy

    capability.active_eval_set = eval_set
    capability.save(update_fields=["active_eval_set"])
    assert {ev.id for ev in runnable_capability_evaluators(capability)} == {judge.id, exact.id}

    EvalSetMember.objects.filter(eval_set=eval_set, evaluator=exact).update(enabled=False)
    assert {ev.id for ev in runnable_capability_evaluators(capability)} == {judge.id}


@pytest.mark.django_db
def test_eval_set_expansion_selects_role_matching_run_intent():
    from overbae.models import Capability, EvalRun, EvalSet, EvalSetMember, Evaluator, Project
    from overbae.services.eval.eval_set import expand_to_run_evaluators

    project = Project.objects.create(name="roles", slug="roles")
    capability = Capability.objects.create(project=project, name="a", slug="a")
    gen_only = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Gen Only",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        config={"check": "exact_match"},
    )
    both = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Both",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        rubric_md="Grade the output.",
        checklist=[{"id": "q1", "q": "?", "weight": 1.0}],
    )
    eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
    EvalSetMember.objects.create(eval_set=eval_set, evaluator=gen_only, role="generative", order=0)
    EvalSetMember.objects.create(eval_set=eval_set, evaluator=both, role="generative", order=1)
    EvalSetMember.objects.create(eval_set=eval_set, evaluator=both, role="trace_scoring", order=2)

    gen_run = EvalRun.objects.create(project=project, name="gen", eval_set=eval_set)
    expand_to_run_evaluators(gen_run, eval_set, role=EvalSetMember.Role.GENERATIVE)
    assert {r.evaluator_id for r in gen_run.run_evaluators.all()} == {gen_only.id, both.id}

    trace_run = EvalRun.objects.create(project=project, name="trace", eval_set=eval_set)
    expand_to_run_evaluators(trace_run, eval_set, role=EvalSetMember.Role.TRACE_SCORING)
    assert {r.evaluator_id for r in trace_run.run_evaluators.all()} == {both.id}


@pytest.mark.django_db
def test_eval_set_prompt_selection_persists_and_scopes_expansion():
    from overbae.models import (
        Capability,
        EvalRun,
        EvalSet,
        EvalSetMember,
        Evaluator,
        Project,
        Prompt,
    )
    from overbae.services.eval.eval_set import expand_to_run_evaluators

    project = Project.objects.create(name="p", slug="p")
    capability = Capability.objects.create(project=project, name="a", slug="a")
    prompt_a = Prompt.objects.create(
        capability=capability, label="System prompt", system_prompt="A"
    )
    prompt_b = Prompt.objects.create(capability=capability, label="fix lane", system_prompt="B")
    member_prompt = Prompt.objects.create(capability=capability, label="pinned", system_prompt="C")

    checklist = [{"id": "q1", "q": "?", "weight": 1.0}]
    scoped = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Scoped",
        kind=Evaluator.Kind.LLM_JUDGE,
        checklist=checklist,
    )
    defaulted = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Defaulted",
        kind=Evaluator.Kind.LLM_JUDGE,
        checklist=checklist,
    )

    eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
    eval_set.prompts.set([prompt_a, prompt_b])
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=scoped, role="generative", prompt=member_prompt, order=0
    )
    EvalSetMember.objects.create(eval_set=eval_set, evaluator=defaulted, role="generative", order=1)

    assert set(EvalSet.objects.get(id=eval_set.id).prompts.values_list("id", flat=True)) == {
        prompt_a.id,
        prompt_b.id,
    }

    run = EvalRun.objects.create(project=project, name="from-set", eval_set=eval_set)
    expand_to_run_evaluators(run, eval_set)

    rows = list(run.run_evaluators.select_related("evaluator"))
    scoped_prompts = [r.prompt_id for r in rows if r.evaluator.name == "Scoped"]
    assert scoped_prompts == [member_prompt.id]
    defaulted_prompts = {r.prompt_id for r in rows if r.evaluator.name == "Defaulted"}
    assert defaulted_prompts == {prompt_a.id, prompt_b.id}

    bare = EvalSet.objects.create(project=project, capability=capability, name="Bare")
    EvalSetMember.objects.create(eval_set=bare, evaluator=defaulted, role="generative", order=0)
    bare_run = EvalRun.objects.create(project=project, name="bare", eval_set=bare)
    expand_to_run_evaluators(bare_run, bare)
    assert [r.prompt_id for r in bare_run.run_evaluators.all()] == [None]


# The card's ``expected_output.example`` comes from the library TYPE and carries
# ``char_interval``/``document_id``, which the capability's real output never emits.
_LANGEXTRACT_CARD = {
    "output_fields": {
        "extractions": "list[Extraction] | None",
        "text": "str | None",
        "char_interval": "CharInterval | None",
        "document_id": "str",
    },
    "output_schema": {
        "required_keys": ["extractions"],
        "properties": {
            "char_interval.start_pos": "int | None after alignment",
            "char_interval.end_pos": "int | None after alignment",
            "extractions": "list[dict]",
        },
    },
    "expected_output": {
        "description": "AnnotatedDocument with a non-empty extractions list.",
        "example": {
            "text": "Lady Juliet gazed longingly",
            "extractions": [
                {
                    "extraction_class": "character",
                    "extraction_text": "Lady Juliet",
                    "char_interval": {"start_pos": 0, "end_pos": 11},
                    "attributes": {"emotional_state": "longing"},
                }
            ],
        },
    },
}


@pytest.mark.django_db
def test_var_catalog_grounded_in_real_datapoint_shape():
    from overbae.models import Capability, Project
    from overbae.services.eval.grounding import resolve_grounding_for_capability
    from overbae.services.eval.rubric_compiler import _build_var_context

    project = Project.objects.create(name="lx", slug="lx")
    capability = Capability.objects.create(
        project=project,
        name="LX",
        slug="lx",
        improvement_metadata={"capability_card": _LANGEXTRACT_CARD},
    )
    frozen_dataset(
        capability.project,
        [
            {
                "input": {},
                "expected_output": {
                    "text": "Romeo loves Juliet",
                    "extractions": [
                        {
                            "extraction_class": "character",
                            "extraction_text": "Romeo",
                            "attributes": {"emotional_state": "love"},
                        }
                    ],
                },
            }
        ],
        capability=capability,
    )

    ctx = _build_var_context(resolve_grounding_for_capability(capability))

    assert "char_interval" not in ctx.bindable
    assert "extractions_char_interval_start_pos" not in ctx.bindable
    assert "extractions_char_interval_end_pos" not in ctx.bindable
    assert "extractions_char_interval_start_pos" not in ctx.nested_jsonpaths
    assert "char_interval" not in ctx.catalog_text
    assert "document_id" not in ctx.bindable

    assert "extractions_extraction_text" in ctx.bindable
    assert "extractions_attributes_emotional_state" in ctx.bindable
    assert ctx.nested_jsonpaths["extractions_extraction_text"] == "$.extractions[*].extraction_text"
    assert "extraction_text" in ctx.catalog_text


@pytest.mark.django_db
def test_var_catalog_falls_back_to_code_schema_without_datapoints():
    from overbae.models import Capability, Project
    from overbae.services.eval.grounding import resolve_grounding_for_capability
    from overbae.services.eval.rubric_compiler import _build_var_context

    project = Project.objects.create(name="lx2", slug="lx2")
    capability = Capability.objects.create(
        project=project,
        name="LX2",
        slug="lx2",
        improvement_metadata={"capability_card": _LANGEXTRACT_CARD},
    )

    ctx = _build_var_context(resolve_grounding_for_capability(capability))

    assert "extractions_char_interval_start_pos" in ctx.bindable
    assert "extractions_extraction_text" in ctx.bindable


def test_collect_reference_leaves_prefers_nested_over_flat():
    from overbae.services.eval.rubric_compiler import _collect_reference_leaves

    leaves = _collect_reference_leaves(
        [
            {"amount": 10.0, "vendor": "Acme"},
            {"amount": {"value": 10.0, "currency": "USD"}, "vendor": "Acme"},
        ]
    )
    paths = {path for path, _ in leaves}
    assert "$.amount.value" in paths
    assert "$.amount.currency" in paths
    assert "$.amount" not in paths  # parent dropped when nested children exist
    assert "$.vendor" in paths


def _llm_span(output, *, start=1):
    return SimpleNamespace(
        span_id="a" * 16,
        trace_id="t" * 32,
        parent_span_id=None,
        span_type="llm_call",
        start_time_ns=start,
        status_code=0,
        name="chat",
        attributes={
            "overmind.input.data": json.dumps([{"role": "user", "content": "email"}]),
            "overmind.output.data": json.dumps([{"role": "assistant", "content": output}]),
        },
    )


def test_sample_reference_prefers_model_golden_only_for_generation():
    from overbae.tasks.eval import _sample_reference

    harness = {"id": "demo-1", "isInvoice_dropped": True, "amount": {"value": 1.0}}
    model = {"isInvoice": True, "amount": 1.0}
    item = {"expected": harness, "model_expected": model}

    assert _sample_reference(item, is_generate=True) == model
    assert _sample_reference(item, is_generate=False) == harness
    # No captured model surface → fall back to harness even for generation.
    assert _sample_reference({"expected": harness}, is_generate=True) == harness


def test_grades_model_surface_reads_pinned_columns():
    from overbae.tasks.eval import _grades_model_surface

    def _sample(columns):
        version = SimpleNamespace(columns=[{"name": c} for c in columns])
        return SimpleNamespace(run=SimpleNamespace(cell=version, cell_id=1))

    assert _grades_model_surface(_sample(["messages", "tools"])) is True
    assert _grades_model_surface(_sample(["messages", "input", "output"])) is False
    assert _grades_model_surface(_sample(["input", "expected_output"])) is False
    # No pinned version (trace_filter run) → harness surface.
    assert (
        _grades_model_surface(SimpleNamespace(run=SimpleNamespace(cell=None, cell_id=None)))
        is False
    )


def _entry_point_span(outputs, *, span_id="e" * 16, start=1):
    return SimpleNamespace(
        span_id=span_id,
        trace_id="t" * 32,
        parent_span_id=None,
        span_type="entry_point",
        start_time_ns=start,
        status_code=0,
        name="analyzeEmail",
        attributes={
            "inputs": json.dumps({"email": {"subject": "Invoice #INV-1042"}}),
            "outputs": json.dumps(outputs),
        },
    )


def _llm_child_span(input_messages, output, *, parent="e" * 16, start=2):
    return SimpleNamespace(
        span_id="b" * 16,
        trace_id="t" * 32,
        parent_span_id=parent,
        span_type="llm_call",
        start_time_ns=start,
        status_code=0,
        name="analyzeEmailWithLlm",
        attributes={
            "overmind.input.data": json.dumps(input_messages),
            "overmind.output.data": json.dumps(
                [{"role": "assistant", "content": json.dumps(output)}]
            ),
        },
    )


def test_normalize_spans_two_layer_promotes_harness_final_output():
    from overbae.services.eval import normalizer

    # The harness (entry_point) output is the delivered record; the llm_call
    # output is the raw extraction the harness reshapes.
    harness = {
        "id": "demo-1",
        "vendor": "Northwind",
        "amount": {"value": 1240.5, "currency": "GBP"},
    }
    model = {"isInvoice": True, "vendor": "Northwind", "amount": 1240.5, "currency": "GBP"}
    seed = [
        {"role": "system", "content": "You are Ledgerline."},
        {"role": "user", "content": "email body"},
    ]
    n = normalizer.normalize_spans([_entry_point_span(harness), _llm_child_span(seed, model)])

    assert n["modality"] == "single_turn"
    assert json.loads(n["final_output"]) == harness
    assert json.loads(n["messages"][-1]["content"]) == harness
    assert json.loads(n["metadata"]["model_output"]) == model
    # The seed (system+user) is untouched, so replay stays faithful.
    assert n["messages"][0]["role"] == "system"
    assert n["messages"][1]["role"] == "user"


def test_normalize_spans_single_layer_keeps_model_output():
    from overbae.services.eval import normalizer

    model = {"answer": "hello"}
    llm = _llm_child_span([{"role": "user", "content": "hi"}], model, parent=None)
    n = normalizer.normalize_spans([llm])

    assert json.loads(n["final_output"]) == model
    assert "model_output" not in (n.get("metadata") or {})


def test_normalize_spans_multi_turn_promotes_harness_final_output():
    from overbae.services.eval import normalizer

    # Two-layer detection is modality-independent.
    harness = {"id": "demo-1"}
    model = {"turn": "final"}
    seed = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "second"},
    ]
    n = normalizer.normalize_spans([_entry_point_span(harness), _llm_child_span(seed, model)])

    assert n["modality"] == "multi_turn"
    assert json.loads(n["final_output"]) == harness
    assert n["metadata"]["two_layer"] is True
    assert json.loads(n["metadata"]["model_output"]) == model


def test_normalize_spans_no_promote_keeps_model_surface():
    from overbae.services.eval import normalizer

    # The dataset-build path opts out of promotion, but the split is still recorded
    # on metadata so the caller can lift the capability surface separately.
    harness = {"id": "demo-1", "vendor": "Northwind"}
    model = {"isInvoice": True, "vendor": "Northwind"}
    seed = [{"role": "system", "content": "sys"}, {"role": "user", "content": "email"}]
    spans = [_entry_point_span(harness), _llm_child_span(seed, model)]

    n = normalizer.normalize_spans(spans, promote_harness=False)

    assert json.loads(n["final_output"]) == model
    assert json.loads(n["messages"][-1]["content"]) == model
    assert n["metadata"]["two_layer"] is True
    assert json.loads(n["metadata"]["capability_output"]) == harness
