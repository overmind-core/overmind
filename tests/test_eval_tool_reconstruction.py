from __future__ import annotations

import json
from types import SimpleNamespace

from overbae.services.eval import funnel as judging
from overbae.services.eval import normalizer
from overbae.services.eval.evaluators import gen_judge, trajectory
from overbae.services.eval.evaluators.base import (
    EvalUnit,
    insufficient_evidence_reason,
    resolve_variables,
)
from overbae.services.eval.evaluators.gen_judge import ChecklistItem, ChecklistResult
from overbae.tasks.eval import _assess_degradation


def _stub_judge_score(monkeypatch, verdicts=(True, False), reasoning="judged"):
    """Stubs the judge with one checklist verdict per entry; the score the
    evaluator lands on is the pass fraction."""

    def fake_invoke_judge(prompt, **kwargs):
        return judging.JudgeOutcome(
            parsed=ChecklistResult(
                items=[ChecklistItem(id=f"c{n}", verdict=v) for n, v in enumerate(verdicts)],
                reasoning=reasoning,
            ),
            raw="{}",
            stats={"response_cost": 0.0, "response_ms": 0},
            judge_trace_id="t",
        )

    monkeypatch.setattr(judging, "invoke_judge", fake_invoke_judge)


def _provenance_marker(draft):
    for entry in draft.sub_scores or []:
        if isinstance(entry, dict) and "_provenance" in entry:
            return entry["_provenance"]
    return None


def _span(
    *,
    span_id: str,
    span_type: str,
    parent_span_id: str | None = None,
    start_time_ns: int = 1,
    name: str = "span",
    attributes: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        span_id=span_id,
        trace_id="t" * 32,
        parent_span_id=parent_span_id,
        span_type=span_type,
        start_time_ns=start_time_ns,
        status_code=0,
        name=name,
        attributes=attributes or {},
    )


def _root_llm_span() -> SimpleNamespace:
    # Raw (non-ChatML) domain I/O — forces the root_io binary-lift path.
    return _span(
        span_id="root",
        span_type="llm_call",
        parent_span_id=None,
        start_time_ns=1,
        attributes={
            "overmind.input.data": json.dumps({"task": "analyze the dataset"}),
            "overmind.output.data": json.dumps({"status": "ok", "summary": "done"}),
        },
    )


def _mcp_tool_span(idx: int, tool: str, args: dict, result: dict) -> SimpleNamespace:
    # An MCP proxy span named ``mcp`` whose real tool lives in the arguments.
    return _span(
        span_id=f"tool_{idx}",
        span_type="tool_call",
        parent_span_id="root",
        start_time_ns=10 + idx,
        name="mcp",
        attributes={
            "tool.name": "mcp",
            "overmind.input.data": json.dumps({"toolName": tool, "args": args}),
            "overmind.output.data": json.dumps(result),
        },
    )


def test_reconstruct_spans_folds_standalone_mcp_tool_spans():
    spans = [
        _root_llm_span(),
        _mcp_tool_span(0, "query_schema", {"table": "t"}, {"rows": 3}),
        _mcp_tool_span(1, "sample_rows", {"n": 5}, {"sample": [1, 2]}),
    ]

    traj = normalizer.reconstruct_spans(spans)
    struct = normalizer.structure_trajectory(traj)

    assert traj["modality"] == "tool_calling"
    roles = [m["role"] for m in traj["messages"]]
    assert "tool" in roles
    assert any(m.get("tool_calls") for m in traj["messages"])

    call_names = {tc["name"] for m in traj["messages"] for tc in (m.get("tool_calls") or [])}
    assert call_names == {"query_schema", "sample_rows"}

    assert {d["name"] for d in traj["tool_definitions"]} == {"query_schema", "sample_rows"}
    assert struct["num_tool_calls"] == 2


def test_reconstruct_spans_uses_root_output_for_multi_lane_trace():
    # Multi-lane capability: the temporally-last LLM span is an intermediate lane; the
    # genuine final answer lives on the root span.
    root = _span(
        span_id="root",
        span_type="llm_call",
        parent_span_id=None,
        start_time_ns=1,
        name="Workshop Dataset Analysis",
        attributes={
            "overmind.input.data": json.dumps({"task": "analyze dataset"}),
            "overmind.output.data": json.dumps(
                {"status": "finished", "summary": {"rows": 10}, "recommendations": ["x"]}
            ),
        },
    )
    lane = _span(
        span_id="lane",
        span_type="llm_call",
        parent_span_id="root",
        start_time_ns=999,  # temporally last
        name="mode:tabular_clustering",
        attributes={
            "overmind.input.data": json.dumps({"files": ["a"]}),
            "overmind.output.data": json.dumps({"diversity_block": "STEP 3 — DIVERSITY ..."}),
        },
    )

    traj = normalizer.reconstruct_spans([root, lane])
    final = json.loads(traj["final_output"])

    assert final["status"] == "finished"
    assert final["recommendations"] == ["x"]
    assert "diversity_block" not in final  # the intermediate lane is not the answer
    assert (traj["metadata"]["extraction"]["path"]) == "root_io"


def test_reconstruct_spans_lifts_output_from_entry_point_span():
    # LangExtract shape: the root LLM span carries no I/O, ``openai.chat`` uses gen_ai.*
    # keys the normalizer's INPUT/OUTPUT_KEYS don't read, and the assembled document
    # lives on the ``entry_point`` span's ``outputs`` attr.
    document = {
        "extractions": [
            {"extraction_class": "character", "extraction_text": "Aragorn"},
            {"extraction_class": "character", "extraction_text": "Gandalf"},
        ],
        "text": "Aragorn ... Gandalf ...",
    }
    root = _span(
        span_id="root",
        span_type="llm_call",
        parent_span_id=None,
        start_time_ns=1,
        name="charnames_extract_09",
        attributes={"overmind.capability.id": "a", "overmind.project.id": "p"},
    )
    entry = _span(
        span_id="entry",
        span_type="entry_point",
        parent_span_id="root",
        start_time_ns=2,
        name="LangExtract Structured Extraction Capability",
        attributes={
            "inputs": json.dumps({"text_or_documents": "Aragorn ... Gandalf ..."}),
            "outputs": json.dumps(document),
        },
    )
    openai_chat = _span(
        span_id="chat",
        span_type="llm_call",
        parent_span_id="entry",
        start_time_ns=3,  # temporally last LLM span, but uses gen_ai.* keys only
        name="openai.chat",
        attributes={"gen_ai.completion.0.content": "one chunk"},
    )

    traj = normalizer.reconstruct_spans([root, entry, openai_chat])

    assert traj["final_output"], "entry_point output must be surfaced, not empty"
    final = json.loads(traj["final_output"])
    assert [e["extraction_text"] for e in final["extractions"]] == ["Aragorn", "Gandalf"]

    unit = EvalUnit(trajectory=traj, structured=normalizer.structure_trajectory(traj))
    resolved = resolve_variables(
        unit, [{"var": "extractions", "source": "output", "jsonpath": "$.extractions"}]
    )
    assert "Aragorn" in resolved["extractions"]


def test_reconstruct_spans_noop_for_plain_chat():
    chat = _span(
        span_id="root",
        span_type="llm_call",
        parent_span_id=None,
        attributes={
            "overmind.input.data": json.dumps([{"role": "user", "content": "hi"}]),
            "overmind.output.data": json.dumps([{"role": "assistant", "content": "hello"}]),
        },
    )

    traj = normalizer.reconstruct_spans([chat])
    struct = normalizer.structure_trajectory(traj)

    assert traj["modality"] != "tool_calling"
    assert traj["tool_definitions"] == []
    assert struct["num_tool_calls"] == 0
    assert [m["role"] for m in traj["messages"]] == ["user", "assistant"]


def test_tool_graph_builds_nodes_from_raw_spans_without_inlining():
    # Messages carry no tool_calls, but ``_raw_tool_spans`` survives — nodes come
    # from the raw spans, unwrapped.
    normalized = {
        "messages": [{"role": "user", "content": "go"}, {"role": "assistant", "content": "ok"}],
        "tool_definitions": [],
        "final_output": "ok",
        "metadata": {},
        "_raw_tool_spans": [
            {
                "span_id": "s0",
                "name": "mcp",
                "arguments": {"toolName": "read", "args": {}},
                "result": {"ok": True},
                "error": "",
            },
            {
                "span_id": "s1",
                "name": "list_files",
                "arguments": {"dir": "/"},
                "result": {"files": []},
                "error": "",
            },
        ],
    }

    struct = normalizer.structure_trajectory(normalized)

    assert struct["num_tool_calls"] == 2
    tools = [n["tool"] for n in struct["tool_graph"]["nodes"]]
    assert tools == ["read", "list_files"]


def test_synthesize_tool_definitions_from_message_calls():
    traj = {
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "name": "search", "arguments": {"q": "x", "k": 3}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        ],
        "tool_definitions": [],
    }

    normalizer.synthesize_tool_definitions(traj)

    assert len(traj["tool_definitions"]) == 1
    schema = traj["tool_definitions"][0]
    assert schema["name"] == "search"
    assert set(schema["parameters"]["properties"]) == {"q", "k"}


def test_synthesize_tool_definitions_preserves_advertised_schema():
    advertised = [{"name": "real_tool", "description": "d", "parameters": {}}]
    traj = {
        "messages": [
            {"role": "assistant", "content": "", "tool_calls": [{"name": "other", "arguments": {}}]}
        ],
        "tool_definitions": list(advertised),
    }

    normalizer.synthesize_tool_definitions(traj)

    assert traj["tool_definitions"] == advertised


def _judge_stub(scope: str, source: str):
    return SimpleNamespace(
        name="tool_discipline",
        kind="llm_judge",
        score_type="numeric",
        scope=scope,
        variable_mapping=[{"var": "evidence", "source": source}],
        judge_model="gpt-5-mini",
        requires_reference=False,
        score_min=0.0,
        score_max=1.0,
        pass_threshold=1.0,
        checklist=[{"id": "c0", "q": "?", "weight": 1.0}, {"id": "c1", "q": "?", "weight": 1.0}],
        config={},
        rubric_md="",
        description="",
        choices=[],
    )


def test_judge_scores_low_provenance_on_empty_trajectory_evidence(monkeypatch):
    # Empty evidence still scores — execution never blocks a user-selected eval.
    _stub_judge_score(monkeypatch)
    unit = EvalUnit(trajectory={"metadata": {}, "final_output": ""}, structured={}, expected=None)
    evaluator = _judge_stub(scope="trajectory", source="tool_calls")

    drafts = gen_judge.evaluate(unit, evaluator, ctx={})

    assert len(drafts) == 1
    assert drafts[0].value == 0.5  # scored, not abstained
    marker = _provenance_marker(drafts[0])
    assert marker is not None and marker["level"] == "low"
    assert "insufficient evidence" in drafts[0].reasoning.lower()


def test_judge_reason_helper_passes_when_evidence_present():
    unit = EvalUnit(
        trajectory={"metadata": {}},
        structured={"tool_graph": {"nodes": [{"tool": "search", "arguments": {}}]}},
        expected=None,
    )
    evaluator = _judge_stub(scope="trajectory", source="tool_calls")

    assert insufficient_evidence_reason(unit, evaluator) is None


def test_judge_guard_ignores_sample_scoped_evaluators():
    # The guard is scoped to trajectory/tool/step/final_output only.
    unit = EvalUnit(trajectory={"metadata": {}, "final_output": ""}, structured={}, expected=None)
    evaluator = _judge_stub(scope="sample", source="output")

    assert insufficient_evidence_reason(unit, evaluator) is None


def _gated_judge_stub(scope: str, source: str):
    ev = _judge_stub(scope=scope, source=source)
    # Gates are boolean-failure logic; a gate flag on a graded judge is ignored.
    ev.score_type = "boolean"
    ev.checklist = [{"id": "c0", "q": "Does X hold?", "gate": True, "weight": 1.0}]
    return ev


def test_gated_judge_flags_low_provenance_when_bound_var_absent(monkeypatch):
    unit = EvalUnit(trajectory={"metadata": {}, "final_output": ""}, structured={}, expected=None)
    evaluator = _gated_judge_stub(scope="sample", source="input")

    reason = insufficient_evidence_reason(unit, evaluator)
    assert reason is not None
    assert "ungradable gate" in reason.lower()

    _stub_judge_score(monkeypatch, verdicts=(False,))
    drafts = gen_judge.evaluate(unit, evaluator, ctx={})
    assert drafts[0].value == 0.0  # scored, not abstained
    marker = _provenance_marker(drafts[0])
    assert marker is not None and marker["level"] == "low"


def test_gated_judge_runs_when_bound_var_present():
    unit = EvalUnit(
        trajectory={
            "final_output": "",
            "messages": [{"role": "user", "content": "real user request"}],
        },
        structured={},
        expected=None,
    )
    evaluator = _gated_judge_stub(scope="sample", source="input")

    assert insufficient_evidence_reason(unit, evaluator) is None


def test_ungated_judge_not_blocked_by_absent_var():
    # No gate items → only the scope-based evidence guard remains, and sample scope runs.
    unit = EvalUnit(trajectory={"metadata": {}, "final_output": ""}, structured={}, expected=None)
    evaluator = _judge_stub(scope="sample", source="input")

    assert insufficient_evidence_reason(unit, evaluator) is None


def test_trajectory_matcher_abstains_without_reference():
    # No reference trajectory → abstain (value=None), never a self-referential 1.0.
    unit = EvalUnit(
        trajectory={},
        structured={"tool_graph": {"nodes": [{"tool": "search", "arguments": {"q": "x"}}]}},
        expected=None,
    )
    evaluator = SimpleNamespace(name="trajectory_accuracy", scope="trajectory", config={})

    drafts = trajectory.evaluate(unit, evaluator, ctx={})

    assert len(drafts) == 1
    assert drafts[0].value is None


def test_trajectory_matcher_scores_against_real_reference():
    unit = EvalUnit(
        trajectory={},
        structured={"tool_graph": {"nodes": [{"tool": "search", "arguments": {"q": "x"}}]}},
        expected=[{"name": "search", "arguments": {"q": "x"}}],
    )
    evaluator = SimpleNamespace(name="trajectory_accuracy", scope="trajectory", config={})

    drafts = trajectory.evaluate(unit, evaluator, ctx={})

    assert drafts[0].value == 1.0
    assert drafts[0].passed is True


def test_assess_degradation_trusts_root_io_with_real_final_output():
    # root_io alone must not mark a trace degraded, or reconstructed samples drop
    # out of every aggregate.
    normalized = {
        "metadata": {"extraction": {"path": "root_io"}},
        "final_output": '{"status": "finished", "recommendations": ["x"]}',
    }
    structured = {"num_tool_calls": 5}
    degraded, reason = _assess_degradation(normalized, structured, raw_tool_spans=[{"name": "x"}])
    assert degraded is False
    assert reason == ""


def test_assess_degradation_flags_no_final_output():
    normalized = {"metadata": {"extraction": {"path": "root_io"}}, "final_output": ""}
    structured = {"num_tool_calls": 5}
    degraded, reason = _assess_degradation(normalized, structured, raw_tool_spans=[{"name": "x"}])
    assert degraded is True
    assert reason.startswith("no_final_output")


def test_assess_degradation_flags_tools_lost():
    normalized = {"metadata": {"extraction": {"path": "chatml_messages"}}, "final_output": "done"}
    structured = {"num_tool_calls": 0}
    raw = [{"name": "search"}, {"name": "read"}]
    degraded, reason = _assess_degradation(normalized, structured, raw_tool_spans=raw)
    assert degraded is True
    assert reason.startswith("tools_lost")


def test_assess_degradation_flags_replay_misses():
    normalized = {
        "metadata": {"extraction": {"path": "chatml_messages"}, "replay_misses": 4},
        "final_output": "answer",
    }
    structured = {"num_tool_calls": 6}
    degraded, reason = _assess_degradation(normalized, structured, raw_tool_spans=[])
    assert degraded is True
    assert reason.startswith("replay_misses")


def test_assess_degradation_trusts_clean_chat():
    normalized = {"metadata": {"extraction": {"path": "chatml_messages"}}, "final_output": "hi"}
    structured = {"num_tool_calls": 0}
    degraded, reason = _assess_degradation(normalized, structured, raw_tool_spans=[])
    assert degraded is False
    assert reason == ""
