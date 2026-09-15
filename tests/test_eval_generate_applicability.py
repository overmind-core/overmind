from __future__ import annotations

import uuid

import pytest

from overbae.services.eval import card_compiler
from overbae.services.eval.evaluators import base as eval_base
from overbae.services.eval.grounding import EvalGroundingContext
from overbae.services.eval.roles import TRACE_SCORING, roles_for_spec
from overbae.services.eval.specs import AUTHORING_CONTRACT, TIER1_GENERATOR, EvaluatorSpec

REPORT_CARD = {
    "task": "Gather evidence and produce a markdown report with citations.",
    "domain": "open-domain factual research",
    "output_fields": {
        "report": "string — markdown report body returned by write_report()",
        "context": "list[str|dict] — accumulated research context",
        "visited_urls": "set[str] — URLs scraped or cited",
        "research_costs": "float — accumulated LLM/API spend",
        "available_images": "list[dict] optional — pre-generated inline images",
    },
    "output_schema": {
        "properties": {
            "report": "string — primary return value of write_report()",
        },
        "required_keys": [],
    },
    "input_schema": {
        "query": "string — the research question or topic",
        "report_type": "enum: research_report|detailed_report|deep",
    },
    "expected_output": {
        "example": "# Why Nvidia Stock Is Rising\n\n## Market Context\n...",
        "description": "A long-form markdown report.",
    },
    "tool_spec": [{"name": "web_search_retriever", "purpose": ""}],
    "constraints": [
        {"rule": "MCP tool selection selects at most 3 tools per query", "type": "budget"},
    ],
}


def test_scalar_report_with_harness_sidecars_is_open():
    construct = card_compiler._card_construct(REPORT_CARD)
    assert construct["family"] == "open"
    assert card_compiler._output_contract_kind(REPORT_CARD) == "scalar_string"
    grounding = EvalGroundingContext(codebase_card=REPORT_CARD)
    names = {s.name for s in card_compiler.compile_card_evaluators(grounding)}
    managed = {s.name for s in card_compiler.compile_managed_card_evaluators(grounding)}
    assert "output-field-accuracy" not in names
    assert "output-field-accuracy" not in managed
    assert "output-contract-required-keys" not in names
    assert "output-contract-required-keys" not in managed


def test_tool_only_constraints_block_generate_role():
    grounding = EvalGroundingContext(codebase_card=REPORT_CARD)
    specs = {s.name: s for s in card_compiler.compile_card_evaluators(grounding)}
    constraints = specs["card-constraints"]
    assert constraints.provenance.blocked_on
    assert "generative" not in roles_for_spec(constraints)
    vocab = specs["tool-vocabulary-selection"]
    assert vocab.provenance.blocked_on
    assert "generative" not in roles_for_spec(vocab)


def test_roles_for_spec_drops_generate_when_blocked():
    spec = EvaluatorSpec.model_validate(
        {
            "name": "tool-vocabulary-selection",
            "kind": "deterministic",
            "scope": "trajectory",
            "score_type": "numeric",
            "config": {"check": "tool_selection", "expected_tools": ["search"]},
            "provenance": {
                "source": "codebase_card.tool_spec",
                "generator": "card_compiler@v1",
                "surface_area": "tool_surface",
                "blocked_on": [
                    "generate-mode variants (stored rows contain no structured tool calls)"
                ],
            },
        }
    )
    assert roles_for_spec(spec) == (TRACE_SCORING,)


def test_unresolved_jsonpath_on_populated_source_is_not_applicable():
    ev = type(
        "Ev",
        (),
        {
            "name": "Task Success",
            "kind": "llm_judge",
            "score_type": "numeric",
            "scope": "final_output",
            "variable_mapping": [
                {"var": "report", "source": "reference", "jsonpath": "$.report"},
            ],
            "config": {},
            "checklist": [{"id": "q1", "q": "Did it answer?", "weight": 1.0}],
        },
    )()
    unit = eval_base.EvalUnit(
        trajectory={"final_output": "# report"},
        expected="A markdown gold report, not an object.",
    )
    drafts = eval_base.evaluate(unit, ev, {"eval_surface": eval_base.SURFACE_GENERATIVE})
    assert len(drafts) == 1
    assert drafts[0].outcome == eval_base.OUTCOME_NOT_APPLICABLE
    assert drafts[0].value is None
    assert "report" in drafts[0].reasoning


def test_generate_drops_unobservable_metadata_and_harness_bindings():
    from overbae.services.eval.surface_binding import enforce_surface_bindings

    checklist = [
        {"id": "cites", "q": "Are the citations present in {visited_urls}?"},
        {"id": "deep", "q": "When {metadata.report_type} is deep, is coverage broader?"},
        {"id": "mcp", "q": "Did {metadata.mcp_strategy} fall back to generic tools?"},
        {"id": "answer", "q": "Does {output} answer the research question?"},
    ]
    mapping = [
        {"var": "visited_urls", "source": "metadata", "jsonpath": "$.visited_urls"},
        {"var": "report_type", "source": "metadata", "jsonpath": "$.report_type"},
        {"var": "mcp_strategy", "source": "metadata", "jsonpath": "$.mcp_strategy"},
        {"var": "output", "source": "output", "jsonpath": ""},
        {"var": "metadata", "source": "metadata", "jsonpath": "$"},
        {"var": "cost", "source": "metadata", "jsonpath": "$.cost"},
    ]
    kept, kept_map, notes, drop = enforce_surface_bindings(
        checklist=checklist,
        variable_mapping=mapping,
        rubric_md="",
        card=REPORT_CARD,
        grades_live_surface=False,
        generate_observes_tools=False,
    )
    assert [i["id"] for i in kept] == ["answer"]
    assert [e["var"] for e in kept_map] == ["output"]
    assert not drop
    assert any("visited_urls" in n for n in notes)
    assert any("report_type" in n for n in notes)


def test_generate_drops_items_that_name_unobservable_fields_in_prose():
    from overbae.services.eval.surface_binding import enforce_surface_bindings

    checklist = [
        {"id": "telemetry", "q": "Cost and visited_urls are tracked on the instance."},
        {
            "id": "cites",
            "q": "Every claim cites a URL present in visited_urls or the reference list.",
        },
        {"id": "answer", "q": "Does the report answer the research question?"},
    ]
    mapping = [
        {"var": "visited_urls", "source": "metadata", "jsonpath": "$.visited_urls"},
        {"var": "output", "source": "output", "jsonpath": ""},
    ]
    kept, _, notes, drop = enforce_surface_bindings(
        checklist=checklist,
        variable_mapping=mapping,
        rubric_md="",
        card=REPORT_CARD,
        grades_live_surface=False,
        generate_observes_tools=False,
    )
    assert [i["id"] for i in kept] == ["answer"]
    assert not drop
    assert any("visited_urls" in n and "unobservable" in n for n in notes)


def test_generate_drops_tool_loop_items_when_replay_has_no_calls():
    from overbae.services.eval.surface_binding import enforce_surface_bindings

    checklist = [
        {"id": "tools", "q": "Did {tool_calls} include web_search_retriever?"},
        {"id": "answer", "q": "Does {output} answer the question?"},
    ]
    mapping = [
        {"var": "tool_calls", "source": "tool_calls", "jsonpath": ""},
        {"var": "output", "source": "output", "jsonpath": ""},
    ]
    kept, kept_map, notes, drop = enforce_surface_bindings(
        checklist=checklist,
        variable_mapping=mapping,
        rubric_md="",
        card=REPORT_CARD,
        grades_live_surface=False,
        generate_observes_tools=False,
    )
    assert [i["id"] for i in kept] == ["answer"]
    assert [e["var"] for e in kept_map] == ["output"]
    assert not drop
    assert any("tool_calls" in n for n in notes)


def test_trace_keeps_harness_metadata_bindings():
    from overbae.services.eval.surface_binding import enforce_surface_bindings

    checklist = [{"id": "cites", "q": "Are citations in {metadata.visited_urls}?"}]
    mapping = [{"var": "visited_urls", "source": "metadata", "jsonpath": "$.visited_urls"}]
    kept, kept_map, notes, drop = enforce_surface_bindings(
        checklist=checklist,
        variable_mapping=mapping,
        rubric_md="",
        card=REPORT_CARD,
        grades_live_surface=True,
    )
    assert [i["id"] for i in kept] == ["cites"]
    assert [e["var"] for e in kept_map] == ["visited_urls"]
    assert notes == [] and not drop


def test_generate_drops_unused_report_even_when_old_rubric_names_it():
    from overbae.services.eval.surface_binding import enforce_surface_bindings

    checklist = [
        {"id": "gold", "q": "Does {output} agree with {reference}?", "weight": 1.0},
    ]
    mapping = [
        {"var": "output", "source": "output", "jsonpath": ""},
        {"var": "reference", "source": "reference", "jsonpath": ""},
        {"var": "report", "source": "output", "jsonpath": "$.report"},
    ]
    kept, kept_map, notes, drop = enforce_surface_bindings(
        checklist=checklist,
        variable_mapping=mapping,
        rubric_md="Grade {report} against the gold.",
        card=REPORT_CARD,
        grades_live_surface=False,
        generate_observes_tools=False,
    )
    assert [i["id"] for i in kept] == ["gold"]
    assert [e["var"] for e in kept_map] == ["output", "reference"]
    assert not drop
    assert any("unused" in n and "report" in n for n in notes)


def test_generate_drops_unreferenced_output_field_binding():
    from overbae.services.eval.surface_binding import enforce_surface_bindings

    checklist = [
        {"id": "gold", "q": "Does {output} agree with {reference}?", "weight": 1.0},
    ]
    mapping = [
        {"var": "output", "source": "output", "jsonpath": ""},
        {"var": "reference", "source": "reference", "jsonpath": ""},
        {"var": "report", "source": "output", "jsonpath": "$.report"},
    ]
    kept, kept_map, notes, drop = enforce_surface_bindings(
        checklist=checklist,
        variable_mapping=mapping,
        rubric_md="Grade the report against the gold.",
        card=REPORT_CARD,
        grades_live_surface=False,
        generate_observes_tools=False,
    )
    assert [i["id"] for i in kept] == ["gold"]
    assert [e["var"] for e in kept_map] == ["output", "reference"]
    assert not drop
    assert any("unused" in n and "report" in n for n in notes)


def test_generate_judge_scores_checklist_on_long_last_chat(monkeypatch):
    from types import SimpleNamespace

    from overbae.services.eval import cascade
    from overbae.services.eval import funnel as judging
    from overbae.services.eval.evaluators import gen_judge
    from tests.factories import evaluator_stub

    routed = []

    def _route(*args, **kwargs):
        routed.append(True)
        raise AssertionError("generate judge must not cascade")

    def _fake(*args, **kwargs):
        return SimpleNamespace(
            parsed=gen_judge.ChecklistResult(
                items=[gen_judge.ChecklistItem(id="gold", verdict=True)],
                reasoning="r",
            ),
            stats={},
            judge_trace_id="t",
        )

    monkeypatch.setattr(cascade, "maybe_route", _route)
    monkeypatch.setattr(judging, "invoke_judge", _fake)
    ev = evaluator_stub(
        kind="llm_judge",
        judge_model="judge-x",
        checklist=[{"id": "gold", "q": "Does {output} agree with {reference}?", "weight": 1.0}],
        variable_mapping=[
            {"var": "output", "source": "output"},
            {"var": "reference", "source": "reference"},
        ],
    )
    unit = eval_base.EvalUnit(
        trajectory={
            "final_output": "Paris",
            "messages": [{"role": "assistant", "content": "Paris"}],
        },
        expected="Paris",
        structured={
            "tool_graph": {
                "nodes": [
                    {
                        "id": "step_0",
                        "tool": "search",
                        "arguments": {},
                        "result": "ok",
                        "error": "",
                        "depends_on": [],
                    }
                ]
            },
            "approx_tokens": 80_000,
            "num_tool_calls": 1,
        },
    )
    drafts = gen_judge.evaluate(unit, ev, {})
    assert routed == []
    assert drafts[0].outcome == eval_base.OUTCOME_SCORED
    by_id = {s["id"]: s for s in drafts[0].sub_scores if "id" in s}
    assert by_id["gold"]["verdict"] is True
    assert not any(str(s.get("id") or "").startswith("step_") for s in drafts[0].sub_scores)


def test_generate_judge_keeps_tool_item_when_unit_has_tool_graph(monkeypatch):
    from types import SimpleNamespace

    from overbae.services.eval import cascade
    from overbae.services.eval import funnel as judging
    from overbae.services.eval.evaluators import gen_judge
    from overbae.services.eval.surface_binding import GOLD_AGREEMENT_QUESTION
    from tests.factories import evaluator_stub

    def _fake(*args, **kwargs):
        return SimpleNamespace(
            parsed=gen_judge.ChecklistResult(
                items=[
                    gen_judge.ChecklistItem(id="tools", verdict=True),
                    gen_judge.ChecklistItem(id="gold", verdict=True),
                ],
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
            {"id": "tools", "q": "Did the research call web_search_retriever?", "weight": 1.0},
            {"id": "rtype", "q": "Does the output match report_type?", "weight": 1.0},
            {"id": "gold", "q": GOLD_AGREEMENT_QUESTION, "weight": 1.0},
        ],
        variable_mapping=[
            {"var": "output", "source": "output"},
            {"var": "reference", "source": "reference"},
        ],
        config={"provenance": {"source": "codebase_card.success_criteria"}},
    )
    unit = eval_base.EvalUnit(
        trajectory={"final_output": "Paris", "messages": []},
        expected="Paris",
        structured={
            "tool_graph": {"nodes": [{"id": "n0", "tool": "web_search_retriever"}]},
            "num_tool_calls": 1,
        },
        codebase_card=REPORT_CARD,
    )
    drafts = gen_judge.evaluate(unit, ev, {})
    by_id = {s["id"]: s for s in drafts[0].sub_scores if "id" in s}
    assert by_id["tools"]["verdict"] is True
    assert by_id["gold"]["verdict"] is True
    assert by_id["rtype"]["outcome"] == "not_applicable"
    assert drafts[0].value == 1.0


def test_allocation_withholds_tool_cells_from_generate_without_replay():
    card = {
        **REPORT_CARD,
        "trajectory_map": [
            {
                "id": "standard-research",
                "tools": ["web_search_retriever"],
                "routing": "default",
                "sequence": ["search", "write"],
                "terminal": {"kind": "emits_record"},
            }
        ],
    }
    grounding = EvalGroundingContext(codebase_card=card)
    cells = {c.key: c for c in card_compiler.signal_cells(grounding, {"family": "open"})}
    assert cells["codebase_card.tool_spec"].suites == ("trace_scoring",)
    assert cells["codebase_card.trajectory_map[standard-research]"].suites == ("trace_scoring",)

    live = EvalGroundingContext(
        codebase_card=card,
        report={"conversation_anatomy": {"tool_calls": {"total": 4, "unique_tools": ["search"]}}},
    )
    live_cells = {c.key: c for c in card_compiler.signal_cells(live, {"family": "open"})}
    assert live_cells["codebase_card.tool_spec"].suites == ("generative", "trace_scoring")


def test_jsonpath_miss_next_to_whole_blob_stays_gradable():
    ev = type(
        "Ev",
        (),
        {
            "variable_mapping": [
                {"var": "report", "source": "output", "jsonpath": "$.report"},
                {"var": "output", "source": "output", "jsonpath": ""},
                {"var": "visited_urls", "source": "metadata", "jsonpath": "$.visited_urls"},
            ],
        },
    )()
    unit = eval_base.EvalUnit(
        trajectory={"final_output": "# report", "metadata": {"cost": 0.0}},
        expected="gold",
    )
    assert eval_base._unresolved_binding_reason(unit, ev) is None


@pytest.mark.django_db
def test_binding_sweep_does_not_archive(monkeypatch):
    from overbae.models import Capability, Dataset, Evaluator, Project
    from overbae.services.eval import binding_check
    from overbae.services.eval.binding_check import RED, BindingHealth, sweep_dataset_bindings
    from overbae.services.eval.evaluators.base import EvalUnit

    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Task Success",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope="final_output",
        rubric_md="Grade the report.",
        checklist=[{"id": "q1", "q": "Did it answer?", "weight": 1.0}],
        variable_mapping=[{"var": "report", "source": "reference", "jsonpath": "$.report"}],
        config={
            "provenance": {
                "generator": TIER1_GENERATOR,
                "source": "x",
                "surface_area": "output_contract",
            }
        },
    )
    dataset = Dataset.objects.create(
        capability=capability, intent="eval", project=project, name="d"
    )
    monkeypatch.setattr(
        binding_check, "sample_units_for_dataset", lambda *a, **k: [EvalUnit(expected="gold")]
    )
    monkeypatch.setattr(binding_check, "dry_run_spec", lambda *a, **k: BindingHealth(RED, 1, []))

    result = sweep_dataset_bindings(dataset)
    evaluator.refresh_from_db()
    assert result["quarantined"] == 0
    assert evaluator.is_archived is False
    assert "binding_health" in (evaluator.config or {})


@pytest.mark.django_db
def test_restore_sweep_quarantine_unarchives():
    from overbae.models import Capability, Evaluator, Project
    from overbae.services.eval.binding_check import restore_sweep_quarantined

    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    ev = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Task Success",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope="final_output",
        rubric_md="r",
        checklist=[{"id": "q1", "q": "?", "weight": 1.0}],
        is_archived=True,
        config={
            "quarantined_reason": "first-data binding sweep: bindings resolved to nothing on every real row"
        },
    )
    assert restore_sweep_quarantined(capability=capability) == 1
    ev.refresh_from_db()
    assert ev.is_archived is False
    assert "quarantined_reason" not in (ev.config or {})


BEHAVIOUR_CARD = {
    **REPORT_CARD,
    "success_criteria": [
        "Citations in the report come from retrieved sources.",
        "Deep and detailed research modes produce broader coverage than simple mode.",
    ],
    "failure_modes": [
        "When context is empty, the agent invents a report instead of abstaining.",
    ],
    "expected_output": {
        **REPORT_CARD["expected_output"],
        "quality_signals": [
            "Abstain rather than guessing when context is empty.",
            "Detailed mode covers more subtopics.",
        ],
    },
    "constraints": [
        *REPORT_CARD["constraints"],
        {"rule": "DEEP_RESEARCH_CONCURRENCY must not exceed 3", "type": "budget"},
        {"rule": "Do not emit a report when context is empty", "type": "protocol"},
        {"rule": "MAX_SUBTOPICS caps the planned subtopic list", "type": "budget"},
    ],
    "trajectory_map": [
        {
            "id": "deep-research",
            "tools": ["web_search_retriever"],
            "routing": "deep and detailed research modes produce broader coverage",
            "sequence": ["search", "write"],
            "terminal": {"kind": "emits_record"},
        }
    ],
}


def test_generate_drops_unobservable_behaviour_lifts():
    from overbae.services.eval.surface_binding import enforce_surface_bindings

    checklist = [
        {"id": "cites", "q": "Do citations in the report come from retrieved sources?"},
        {
            "id": "deep",
            "q": "Do deep and detailed research modes produce broader coverage than simple mode?",
        },
        {
            "id": "empty",
            "q": "When context is empty, does the agent abstain rather than invent a report?",
        },
        {"id": "conc", "q": "Is DEEP_RESEARCH_CONCURRENCY respected?"},
        {"id": "cap", "q": "Does the run honour MAX_SUBTOPICS?"},
        {"id": "subtopics", "q": "In detailed mode, does the report cover more subtopics?"},
        {"id": "faithful", "q": "Does the report stay faithful to the user question?"},
        {"id": "detail", "q": "Does the report provide detailed coverage of the question?"},
    ]
    mapping = [{"var": "output", "source": "output", "jsonpath": ""}]
    kept, _, notes, drop = enforce_surface_bindings(
        checklist=checklist,
        variable_mapping=mapping,
        rubric_md="",
        card=BEHAVIOUR_CARD,
        grades_live_surface=False,
        generate_observes_tools=False,
    )
    assert [i["id"] for i in kept] == ["cites", "faithful", "detail"]
    assert not drop
    assert any("unobservable" in n or "control-plane" in n for n in notes)


def test_generate_drops_paraphrased_item_when_sourced_cluster_is_unobservable():
    from overbae.services.eval.surface_binding import (
        card_claims_for_source,
        enforce_surface_bindings,
    )

    source = "codebase_card.failure_modes[0]"
    sourced = card_claims_for_source(source, BEHAVIOUR_CARD)
    checklist = [{"id": "abstain", "q": "Abstention message is explicit and unambiguous"}]
    kept, _, notes, drop = enforce_surface_bindings(
        checklist=checklist,
        variable_mapping=[{"var": "output", "source": "output", "jsonpath": ""}],
        rubric_md="",
        card=BEHAVIOUR_CARD,
        grades_live_surface=False,
        generate_observes_tools=False,
        sourced_claims=sourced,
    )
    assert [i["id"] for i in kept] == []
    assert drop
    assert any("sourced card claim" in n for n in notes)


def test_mixed_success_cluster_does_not_inherit_unobservable():
    from overbae.services.eval.surface_binding import (
        card_claims_for_source,
        enforce_surface_bindings,
    )

    sourced = card_claims_for_source("codebase_card.success_criteria", BEHAVIOUR_CARD)
    checklist = [
        {"id": "cites", "q": "Do citations in the report come from retrieved sources?"},
        {
            "id": "deep",
            "q": "Do deep and detailed research modes produce broader coverage than simple mode?",
        },
    ]
    kept, _, notes, drop = enforce_surface_bindings(
        checklist=checklist,
        variable_mapping=[{"var": "output", "source": "output", "jsonpath": ""}],
        rubric_md="",
        card=BEHAVIOUR_CARD,
        grades_live_surface=False,
        generate_observes_tools=False,
        sourced_claims=sourced,
    )
    assert [i["id"] for i in kept] == ["cites"]
    assert not drop
    assert any("unobservable" in n or "control-plane" in n for n in notes)


def test_gold_agreement_remainder_survives_indexed_unobservable_source():
    from types import SimpleNamespace

    from overbae.services.eval.surface_binding import (
        GOLD_AGREEMENT_QUESTION,
        partition_generate_unobservable,
    )

    card = {
        **REPORT_CARD,
        "success_criteria": [
            "Report answers the input query with structure appropriate to report_type",
            "Citations reference URLs or documents actually retrieved during research",
        ],
    }
    evaluator = SimpleNamespace(
        config={"provenance": {"source": "codebase_card.success_criteria[0]"}}
    )
    applicable, excluded = partition_generate_unobservable(
        [
            {
                "id": "cites",
                "q": "Citations reference URLs or documents actually retrieved during research",
            },
            {"id": "gold", "q": GOLD_AGREEMENT_QUESTION},
        ],
        evaluator,
        card,
    )
    assert [i["id"] for i in applicable] == ["gold"]
    assert [e["item"]["id"] for e in excluded] == ["cites"]


def test_partition_keeps_tool_item_when_generate_observes_tools():
    from types import SimpleNamespace

    from overbae.services.eval.surface_binding import (
        GOLD_AGREEMENT_QUESTION,
        partition_generate_unobservable,
    )

    evaluator = SimpleNamespace(config={"provenance": {"source": "codebase_card.success_criteria"}})
    items = [
        {"id": "tools", "q": "Did the research call web_search_retriever?"},
        {"id": "rtype", "q": "Does the output match report_type?"},
        {"id": "gold", "q": GOLD_AGREEMENT_QUESTION},
    ]
    applicable, excluded = partition_generate_unobservable(
        items, evaluator, REPORT_CARD, generate_observes_tools=True
    )
    assert [i["id"] for i in applicable] == ["tools", "gold"]
    assert [e["item"]["id"] for e in excluded] == ["rtype"]


def test_enforce_keeps_gold_agreement_when_indexed_source_is_unobservable():
    from overbae.services.eval.surface_binding import (
        GOLD_AGREEMENT_QUESTION,
        card_claims_for_source,
        enforce_surface_bindings,
    )

    card = {
        **REPORT_CARD,
        "success_criteria": [
            "Report answers the input query with structure appropriate to report_type",
            "Citations reference URLs or documents actually retrieved during research",
        ],
    }
    sourced = card_claims_for_source("codebase_card.success_criteria[0]", card)
    kept, kept_map, notes, drop = enforce_surface_bindings(
        checklist=[
            {
                "id": "cites",
                "q": "Do citations in the report come from retrieved sources?",
                "weight": 0.25,
            },
            {"id": "gold", "q": GOLD_AGREEMENT_QUESTION, "weight": 1.0},
        ],
        variable_mapping=[
            {"var": "output", "source": "output", "jsonpath": ""},
            {"var": "reference", "source": "reference", "jsonpath": ""},
            {"var": "report", "source": "output", "jsonpath": "$.report"},
        ],
        rubric_md="Does the output agree with {reference}?",
        card=card,
        grades_live_surface=False,
        generate_observes_tools=False,
        sourced_claims=sourced,
    )
    assert [i["id"] for i in kept] == ["gold"]
    assert [e["var"] for e in kept_map] == ["output", "reference"]
    assert not drop
    assert any("citations_from_retrieved_sources" in n or "cites" in n for n in notes)
    assert any("unused" in n and "report" in n for n in notes)


def test_trace_keeps_harness_behaviour_lifts():
    from overbae.services.eval.surface_binding import enforce_surface_bindings

    checklist = [
        {"id": "deep", "q": "Do deep and detailed research modes produce broader coverage?"},
        {"id": "conc", "q": "Is DEEP_RESEARCH_CONCURRENCY respected?"},
    ]
    kept, _, notes, drop = enforce_surface_bindings(
        checklist=checklist,
        variable_mapping=[{"var": "output", "source": "output", "jsonpath": ""}],
        rubric_md="",
        card=BEHAVIOUR_CARD,
        grades_live_surface=True,
        generate_observes_tools=True,
    )
    assert [i["id"] for i in kept] == ["deep", "conc"]
    assert notes == [] and not drop


def test_allocation_withholds_harness_behaviour_cells_from_generate():
    grounding = EvalGroundingContext(codebase_card=BEHAVIOUR_CARD)
    cells = {c.key: c for c in card_compiler.signal_cells(grounding, {"family": "open"})}
    assert cells["codebase_card.constraints"].suites == ("trace_scoring",)
    assert cells["codebase_card.failure_modes[0]"].suites == ("trace_scoring",)
    assert cells["codebase_card.success_criteria"].suites == ("generative", "trace_scoring")
    assert "retrieved" in cells["codebase_card.success_criteria"].label.lower()
    assert "deep" not in cells["codebase_card.success_criteria"].label.lower()
    assert cells["codebase_card.expected_output.quality_signals"].suites == ("trace_scoring",)

    live = EvalGroundingContext(
        codebase_card=BEHAVIOUR_CARD,
        report={"conversation_anatomy": {"tool_calls": {"total": 4}}},
    )
    live_cells = {c.key: c for c in card_compiler.signal_cells(live, {"family": "open"})}
    assert live_cells["codebase_card.constraints"].suites == ("generative", "trace_scoring")
    assert live_cells["codebase_card.failure_modes[0]"].suites == ("generative", "trace_scoring")
    assert live_cells["codebase_card.expected_output.quality_signals"].suites == (
        "generative",
        "trace_scoring",
    )


def test_fill_skips_harness_symbol_quality_signal():
    from overbae.services.eval.surface_binding import fill_generate_observable_remainder

    card = {
        **BEHAVIOUR_CARD,
        "expected_output": {
            **BEHAVIOUR_CARD["expected_output"],
            "quality_signals": [
                "Report length meets configured TOTAL_WORDS minimum for the report type.",
                "The report stays faithful to the user question.",
            ],
        },
    }
    kept, notes = fill_generate_observable_remainder(
        [],
        card,
        "codebase_card.expected_output.quality_signals",
        generate_observes_tools=False,
    )
    questions = [str(i.get("q") or "") for i in kept]
    assert any("faithful" in q.lower() for q in questions)
    assert not any("TOTAL_WORDS" in q for q in questions)
    assert not any("the report type" in q.lower() for q in questions)
    assert notes


def test_fill_adds_unauthored_generate_observable_success_criterion():
    from overbae.services.eval.surface_binding import fill_generate_observable_remainder

    card = {
        **BEHAVIOUR_CARD,
        "success_criteria": [
            "The report answers the user question with specific facts.",
            *BEHAVIOUR_CARD["success_criteria"],
        ],
    }
    kept, notes = fill_generate_observable_remainder(
        [
            {
                "id": "cites",
                "q": "Do citations in the report come from retrieved sources?",
                "weight": 1.0,
            }
        ],
        card,
        "codebase_card.success_criteria",
        generate_observes_tools=False,
    )
    questions = [str(i.get("q") or "") for i in kept]
    assert any("retrieved sources" in q for q in questions)
    assert any("specific facts" in q for q in questions)
    assert notes


def test_mixed_success_criterion_keeps_deliverable_clause():
    from overbae.services.eval.surface_binding import (
        claim_needs_harness_runtime,
        generate_observable_cluster_claims,
        split_generate_observability,
    )

    mixed = "Report answers the input query with structure appropriate to report_type"
    card = {
        **REPORT_CARD,
        "success_criteria": [
            mixed,
            "Citations reference URLs or documents actually retrieved during research",
            "Cost and visited_urls are tracked on the instance",
        ],
    }
    assert claim_needs_harness_runtime(mixed, card)
    obs, har = split_generate_observability(
        card, card["success_criteria"], generate_observes_tools=False
    )
    assert any("answers the input query" in t.lower() for t in obs)
    assert not any("report_type" in t for t in obs)
    assert any("retrieved" in t.lower() for t in obs)
    assert any("visited_urls" in t for t in har)
    assert not any("deep" in t.lower() for t in obs)
    claims = generate_observable_cluster_claims(card, "success")
    assert any("answers the input query" in c.lower() for c in claims)
    assert not any("report_type" in c for c in claims)


def test_generate_drops_items_that_name_snake_case_harness_params():
    from overbae.services.eval.surface_binding import enforce_surface_bindings

    kept, _, notes, drop = enforce_surface_bindings(
        checklist=[
            {
                "id": "mixed",
                "q": "Does the output meet this criterion: Report answers the input query with structure appropriate to report_type?",
            },
            {"id": "answer", "q": "Does the report answer the input query?"},
        ],
        variable_mapping=[{"var": "output", "source": "output", "jsonpath": ""}],
        rubric_md="",
        card=REPORT_CARD,
        grades_live_surface=False,
        generate_observes_tools=False,
    )
    assert [i["id"] for i in kept] == ["answer"]
    assert not drop
    assert any("report_type" in n for n in notes)


def test_generate_drops_vacuous_filled_remainder():
    from overbae.services.eval.surface_binding import enforce_surface_bindings

    kept, _, notes, drop = enforce_surface_bindings(
        checklist=[
            {"id": "empty", "q": "Does the output meet this criterion: The report type?"},
            {
                "id": "answer",
                "q": "Does the output meet this criterion: Report answers the input query?",
            },
        ],
        variable_mapping=[{"var": "output", "source": "output", "jsonpath": ""}],
        rubric_md="",
        card=REPORT_CARD,
        grades_live_surface=False,
        generate_observes_tools=False,
    )
    assert [i["id"] for i in kept] == ["answer"]
    assert not drop
    assert any("not a checkable claim" in n for n in notes)


def test_fill_strips_harness_clause_from_mixed_success_criterion():
    from overbae.services.eval.surface_binding import fill_generate_observable_remainder

    card = {
        **REPORT_CARD,
        "success_criteria": [
            "Report answers the input query with structure appropriate to report_type",
            "Citations reference URLs or documents actually retrieved during research",
        ],
    }
    kept, notes = fill_generate_observable_remainder(
        [
            {
                "id": "cites",
                "q": "Do citations in the report come from retrieved sources?",
                "weight": 1.0,
            }
        ],
        card,
        "codebase_card.success_criteria",
        generate_observes_tools=False,
    )
    questions = [str(i.get("q") or "") for i in kept]
    assert any("retrieved sources" in q for q in questions)
    assert any("answers the input query" in q.lower() for q in questions)
    assert not any("report_type" in q for q in questions)
    assert notes


def test_uncovered_claims_names_missing_success_criterion():
    from overbae.services.eval.surface_binding import uncovered_generate_card_claims

    card = {
        **BEHAVIOUR_CARD,
        "success_criteria": [
            "The report answers the user question with specific facts.",
            *BEHAVIOUR_CARD["success_criteria"],
        ],
    }
    uncovered = uncovered_generate_card_claims(
        card,
        [[{"id": "cites", "q": "Do citations in the report come from retrieved sources?"}]],
        generate_observes_tools=False,
    )
    assert any("specific facts" in c for c in uncovered)
    assert not any("deep" in c.lower() for c in uncovered)


@pytest.mark.django_db
def test_heal_fills_success_remainder_and_leaves_citations():
    from overbae.models import Capability, EvalSetMember, Evaluator, Project
    from overbae.services.eval.eval_set import (
        _heal_generative_surface_bindings,
        ensure_default_eval_set,
    )
    from overbae.services.eval.grounding import EvalGroundingContext

    card = {
        **BEHAVIOUR_CARD,
        "success_criteria": [
            "The report answers the user question with specific facts.",
            *BEHAVIOUR_CARD["success_criteria"],
        ],
    }
    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Task Success",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope="final_output",
        applicable_roles=["generative"],
        rubric_md="Grade citations.",
        checklist=[
            {
                "id": "cites",
                "q": "Do citations in the report come from retrieved sources?",
                "weight": 1.0,
            }
        ],
        variable_mapping=[{"var": "output", "source": "output", "jsonpath": ""}],
        config={
            "provenance": {
                "generator": TIER1_GENERATOR,
                "source": "codebase_card.success_criteria",
                "surface_area": "output_contract",
            }
        },
    )
    eval_set = ensure_default_eval_set(capability)
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role="generative", enabled=True
    )
    healed, disabled = _heal_generative_surface_bindings(
        eval_set, EvalGroundingContext(codebase_card=card)
    )
    evaluator.refresh_from_db()
    questions = [str(i.get("q") or "") for i in evaluator.checklist]
    assert healed == 1
    assert disabled == 0
    assert any("retrieved sources" in q for q in questions)
    assert any("specific facts" in q for q in questions)


@pytest.mark.django_db
def test_heal_leaves_disabled_generative_member_disabled():
    from overbae.models import Capability, EvalSetMember, Evaluator, Project
    from overbae.services.eval.eval_set import (
        _heal_generative_surface_bindings,
        ensure_default_eval_set,
    )
    from overbae.services.eval.grounding import EvalGroundingContext

    card = {
        **BEHAVIOUR_CARD,
        "success_criteria": [
            "The report answers the user question with specific facts.",
            *BEHAVIOUR_CARD["success_criteria"],
        ],
    }
    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Quality Signals",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope="final_output",
        applicable_roles=["generative"],
        rubric_md="Grade citations.",
        checklist=[
            {
                "id": "cites",
                "q": "Do citations in the report come from retrieved sources?",
                "weight": 1.0,
            }
        ],
        variable_mapping=[{"var": "output", "source": "output", "jsonpath": ""}],
        config={
            "provenance": {
                "generator": TIER1_GENERATOR,
                "source": "codebase_card.success_criteria",
                "surface_area": "output_contract",
            }
        },
    )
    eval_set = ensure_default_eval_set(capability)
    member = EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role="generative", enabled=False
    )
    healed, disabled = _heal_generative_surface_bindings(
        eval_set, EvalGroundingContext(codebase_card=card)
    )
    member.refresh_from_db()
    evaluator.refresh_from_db()
    questions = [str(i.get("q") or "") for i in evaluator.checklist]
    assert member.enabled is False
    assert disabled == 0
    assert healed >= 1
    assert any("specific facts" in q for q in questions)


@pytest.mark.django_db
def test_heal_replaces_mixed_remainder_with_deliverable_clause():
    from overbae.models import Capability, EvalSetMember, Evaluator, Project
    from overbae.services.eval.eval_set import (
        _heal_generative_surface_bindings,
        ensure_default_eval_set,
    )
    from overbae.services.eval.grounding import EvalGroundingContext

    card = {
        **REPORT_CARD,
        "success_criteria": [
            "Report answers the input query with structure appropriate to report_type",
            "Citations reference URLs or documents actually retrieved during research",
        ],
    }
    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Task Success",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope="final_output",
        applicable_roles=["generative"],
        rubric_md="Grade the report.",
        checklist=[
            {
                "id": "cites",
                "q": "Do citations in the report come from retrieved sources?",
                "weight": 0.25,
            },
            {
                "id": "mixed",
                "q": "Does the output meet this criterion: Report answers the input query with structure appropriate to report_type?",
                "weight": 1.0,
            },
        ],
        variable_mapping=[{"var": "output", "source": "output", "jsonpath": ""}],
        config={
            "provenance": {
                "generator": TIER1_GENERATOR,
                "source": "codebase_card.success_criteria",
                "surface_area": "output_contract",
            }
        },
    )
    eval_set = ensure_default_eval_set(capability)
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role="generative", enabled=True
    )
    healed, disabled = _heal_generative_surface_bindings(
        eval_set, EvalGroundingContext(codebase_card=card)
    )
    evaluator.refresh_from_db()
    questions = [str(i.get("q") or "") for i in evaluator.checklist]
    assert healed == 1
    assert disabled == 0
    assert any("retrieved sources" in q for q in questions)
    assert any("answers the input query" in q.lower() for q in questions)
    assert not any("report_type" in q for q in questions)


@pytest.mark.django_db
def test_seed_existing_skips_disabled_generative_membership():
    from overbae.models import Capability, EvalSetMember, Evaluator, Project
    from overbae.services.eval.card_compiler import allocate_signals
    from overbae.services.eval.eval_set import ensure_default_eval_set
    from overbae.services.eval.grounding import EvalGroundingContext

    card = {
        **BEHAVIOUR_CARD,
        "expected_output": {
            **BEHAVIOUR_CARD["expected_output"],
            "quality_signals": [
                "The report stays faithful to the user question.",
                "Abstain rather than guessing when context is empty.",
            ],
        },
    }
    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    judge = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Quality Signals",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope="final_output",
        applicable_roles=["generative"],
        rubric_md="r",
        checklist=[{"id": "q1", "q": "Is the report faithful?", "weight": 1.0}],
        config={
            "provenance": {
                "generator": TIER1_GENERATOR,
                "source": "codebase_card.expected_output.quality_signals",
                "surface_area": "output_contract",
                "authoring_contract": AUTHORING_CONTRACT,
            }
        },
    )
    eval_set = ensure_default_eval_set(capability)
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=judge, role="generative", enabled=False
    )
    grounding = EvalGroundingContext(codebase_card=card)
    allocation = allocate_signals(
        grounding, [], existing_judges=[judge], construct={"family": "open"}
    )
    assert "codebase_card.expected_output.quality_signals" not in allocation.claimed["generative"]
    residual_keys = {c.key for c in allocation.residual("generative")}
    assert "codebase_card.expected_output.quality_signals" in residual_keys


def test_fill_rewrites_query_claim_to_reference_agreement():
    from overbae.services.eval.surface_binding import fill_generate_observable_remainder

    card = {
        **REPORT_CARD,
        "success_criteria": [
            "Report answers the input query with structure appropriate to report_type",
            "Citations reference URLs or documents actually retrieved during research",
        ],
    }
    kept, notes = fill_generate_observable_remainder(
        [
            {
                "id": "cites",
                "q": "Do citations in the report come from retrieved sources?",
                "weight": 1.0,
            }
        ],
        card,
        "codebase_card.success_criteria",
        generate_observes_tools=False,
        closed_form_reference=True,
    )
    questions = [str(i.get("q") or "") for i in kept]
    assert any("retrieved sources" in q for q in questions)
    assert any("agree with {reference}" in q.lower() for q in questions)
    assert not any("answers the input query" in q.lower() for q in questions)
    assert notes


@pytest.mark.django_db
def test_heal_rebinds_query_item_when_dataset_gold_is_closed_form():
    from conftest import frozen_dataset

    from overbae.models import Capability, EvalSetMember, Evaluator, Project
    from overbae.services.eval.eval_set import (
        _heal_generative_surface_bindings,
        ensure_default_eval_set,
    )
    from overbae.services.eval.grounding import EvalGroundingContext

    card = {
        **REPORT_CARD,
        "success_criteria": [
            "Report answers the input query with structure appropriate to report_type",
            "Citations reference URLs or documents actually retrieved during research",
        ],
    }
    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    dataset = frozen_dataset(
        project,
        [{"input": "Who won?", "expected_output": "Annick Bricaud"}],
        capability=capability,
        name="qa",
        contract="eval",
    )
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Task Success",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope="final_output",
        applicable_roles=["generative"],
        rubric_md="Grade the report.",
        checklist=[
            {
                "id": "cites",
                "q": "Do citations in the report come from retrieved sources?",
                "weight": 0.25,
            },
            {
                "id": "query",
                "q": "Does the output meet this criterion: Report answers the input query?",
                "weight": 1.0,
            },
        ],
        variable_mapping=[{"var": "output", "source": "output", "jsonpath": ""}],
        config={
            "provenance": {
                "generator": TIER1_GENERATOR,
                "source": "codebase_card.success_criteria",
                "surface_area": "output_contract",
            }
        },
    )
    eval_set = ensure_default_eval_set(capability)
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role="generative", enabled=True
    )
    healed, disabled = _heal_generative_surface_bindings(
        eval_set, EvalGroundingContext(codebase_card=card, dataset=dataset)
    )
    evaluator.refresh_from_db()
    questions = [str(i.get("q") or "") for i in evaluator.checklist]
    sources = {str(e.get("source") or "") for e in evaluator.variable_mapping}
    assert healed == 1
    assert disabled == 0
    assert evaluator.requires_reference
    assert "reference" in sources
    assert any("retrieved sources" in q for q in questions)
    assert any("agree with {reference}" in q.lower() for q in questions)
    assert not any("answers the input query" in q.lower() for q in questions)


@pytest.mark.django_db
def test_heal_fills_empty_checklist_code_spans():
    from overbae.models import Capability, EvalSetMember, Evaluator, Project
    from overbae.services.eval.eval_set import (
        _heal_generative_surface_bindings,
        ensure_default_eval_set,
    )

    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Rendering",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope="final_output",
        applicable_roles=["generative"],
        rubric_md="Grade the proposal.",
        checklist=[
            {
                "id": "final_line",
                "q": "Does `` contain a FINAL TRANSACTION PROPOSAL line?",
                "weight": 1.0,
            },
            {
                "id": "faithful",
                "q": "Are the claims in `` faithful to `` and `{reference}`?",
                "weight": 1.0,
            },
        ],
        variable_mapping=[
            {"var": "output", "source": "output", "jsonpath": ""},
            {"var": "input", "source": "input", "jsonpath": ""},
            {"var": "reference", "source": "reference", "jsonpath": ""},
        ],
        config={
            "provenance": {"generator": TIER1_GENERATOR, "source": "codebase_card.output_fields"}
        },
    )
    eval_set = ensure_default_eval_set(capability)
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role="generative", enabled=True
    )
    healed, disabled = _heal_generative_surface_bindings(
        eval_set, EvalGroundingContext(codebase_card={})
    )
    evaluator.refresh_from_db()
    by_id = {str(i.get("id")): str(i.get("q") or "") for i in evaluator.checklist}
    assert healed == 1
    assert disabled == 0
    assert by_id["final_line"] == "Does {output} contain a FINAL TRANSACTION PROPOSAL line?"
    assert by_id["faithful"] == "Are the claims in {output} faithful to {input} and `{reference}`?"
    assert all("``" not in q for q in by_id.values())


def test_rebind_rewrites_unbraced_gold_question():
    from overbae.services.eval.surface_binding import (
        GOLD_AGREEMENT_QUESTION,
        rebind_query_items_to_gold,
    )

    kept, notes = rebind_query_items_to_gold(
        [
            {
                "id": "gold",
                "q": "Does the output agree with the reference answer?",
                "weight": 1.0,
            },
            {
                "id": "cites",
                "q": "Do citations come from retrieved sources?",
                "weight": 0.25,
            },
        ]
    )
    questions = {item["id"]: item["q"] for item in kept}
    assert questions["gold"] == GOLD_AGREEMENT_QUESTION
    assert questions["cites"] == "Do citations come from retrieved sources?"
    assert notes


def test_generate_observes_tools_from_dataset_card_anatomy():
    from overbae.services.eval.card_compiler import generate_observes_tool_calls

    card = EvalGroundingContext(dataset_card={"conversation_anatomy": {"tool_calls": {"total": 4}}})
    empty = EvalGroundingContext()
    assert generate_observes_tool_calls(card)
    assert not generate_observes_tool_calls(empty)


@pytest.mark.django_db
def test_closed_form_uses_only_the_passed_dataset():
    from conftest import frozen_dataset

    from overbae.models import Capability, Project
    from overbae.services.eval.profiler import closed_form_reference_for

    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    qa = frozen_dataset(
        project,
        [{"input": "Who won?", "expected_output": "Annick Bricaud"}],
        capability=capability,
        name="qa",
        contract="eval",
    )
    report = frozen_dataset(
        project,
        [
            {
                "input": "Write a report.",
                "expected_output": "# Findings\n\n" + ("paragraph " * 40),
            }
        ],
        capability=capability,
        name="report",
        contract="eval",
    )
    assert closed_form_reference_for(capability=capability, dataset=qa)
    assert not closed_form_reference_for(capability=capability, dataset=report)
    assert not closed_form_reference_for(capability=capability, dataset=None)


SCALAR_LABEL_CODEBASE_CARD = {
    "_fallback": False,
    "task": "Classify the user's message into one e-commerce intent label.",
    "output_fields": {"intent_label": ""},
    "output_schema": {"required_keys": [], "properties": {}},
    "success_criteria": [
        "Returns exactly one snake_case intent label from the taxonomy",
    ],
    "expected_output": {
        "description": "A single snake_case intent label",
        "example": "cancel_order",
    },
    "vocabulary": {
        "cancel_order": "user wants to cancel",
        "track_shipment": "user asks for tracking",
    },
}


def test_is_gold_label_claim_recognizes_classification_criteria():
    from overbae.services.eval.surface_binding import (
        is_gold_agreement_text,
        is_gold_label_claim,
    )

    claim = "Returned label matches the gold intent"
    assert is_gold_label_claim(claim)
    assert not is_gold_agreement_text(claim)
    assert is_gold_label_claim("predicted class equals ground truth")
    assert not is_gold_label_claim("Returns exactly one snake_case intent label from the taxonomy")


def test_gold_agreement_question_covers_gold_label_claim():
    from overbae.services.eval.surface_binding import (
        GOLD_AGREEMENT_QUESTION,
        _item_covers_cluster_claim,
        is_gold_label_claim,
        uncovered_generate_card_claims,
    )

    claim = "Returned label matches the gold intent"
    assert _item_covers_cluster_claim(GOLD_AGREEMENT_QUESTION, claim)
    card = {
        **SCALAR_LABEL_CODEBASE_CARD,
        "success_criteria": [
            *SCALAR_LABEL_CODEBASE_CARD["success_criteria"],
            claim,
        ],
    }
    uncovered = uncovered_generate_card_claims(
        card,
        [[{"id": "gold", "q": GOLD_AGREEMENT_QUESTION}]],
        generate_observes_tools=False,
        closed_form_reference=True,
    )
    assert not [c for c in uncovered if is_gold_label_claim(c)]


def test_card_warrants_label_accuracy_from_gold_success_criterion():
    card = {
        **SCALAR_LABEL_CODEBASE_CARD,
        "success_criteria": [
            "Predicted label exactly equals the dataset's expected label for that message",
        ],
    }
    assert card_compiler.card_warrants_label_accuracy(card)


def test_compile_managed_mints_label_accuracy_from_card_without_dataset():
    card = {
        **SCALAR_LABEL_CODEBASE_CARD,
        "success_criteria": [
            "Returned label matches the gold intent",
        ],
    }
    grounding = EvalGroundingContext(codebase_card=card)
    names = {spec.name for spec in card_compiler.compile_managed_card_evaluators(grounding)}
    assert "label-accuracy" in names
    spec = next(
        s
        for s in card_compiler.compile_managed_card_evaluators(grounding)
        if s.name == "label-accuracy"
    )
    assert spec.config == {"check": "exact_match", "case_insensitive": True}
    assert spec.provenance.source == "codebase_card.success_criteria"


@pytest.mark.django_db
def test_compile_managed_mints_label_accuracy_for_closed_form_labels():
    from conftest import frozen_dataset

    from overbae.models import Capability, Project
    from overbae.services.eval.grounding import (
        attach_example_dataset,
        resolve_grounding_for_capability,
    )

    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project,
        name="Intent",
        slug=f"intent-{uuid.uuid4().hex[:8]}",
        improvement_metadata={"capability_card": SCALAR_LABEL_CODEBASE_CARD},
    )
    frozen_dataset(
        project,
        [
            {"input": "cancel my order", "expected_output": "cancel_order"},
            {"input": "where is my package", "expected_output": "track_shipment"},
        ],
        capability=capability,
        contract="eval",
    )
    grounding = attach_example_dataset(resolve_grounding_for_capability(capability))
    names = {spec.name for spec in card_compiler.compile_managed_card_evaluators(grounding)}
    assert "label-accuracy" in names
    spec = next(
        s
        for s in card_compiler.compile_managed_card_evaluators(grounding)
        if s.name == "label-accuracy"
    )
    assert spec.config == {"check": "exact_match", "case_insensitive": True}


@pytest.mark.django_db
def test_sync_mints_label_accuracy_member_for_closed_form_scalar_label():
    from conftest import frozen_dataset

    from overbae.models import Capability, Evaluator, Project
    from overbae.services.eval.eval_set import ensure_default_eval_set, sync_card_evaluators

    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project,
        name="Intent",
        slug=f"intent-{uuid.uuid4().hex[:8]}",
        improvement_metadata={"capability_card": SCALAR_LABEL_CODEBASE_CARD},
    )
    frozen_dataset(
        project,
        [
            {"input": "cancel my order", "expected_output": "cancel_order"},
            {"input": "where is my package", "expected_output": "track_shipment"},
        ],
        capability=capability,
        contract="eval",
    )
    sync_card_evaluators(capability)
    evaluator = Evaluator.objects.get(capability=capability, name="label-accuracy")
    assert evaluator.config["check"] == "exact_match"
    eval_set = ensure_default_eval_set(capability)
    assert eval_set.members.filter(evaluator=evaluator, enabled=True).exists()


@pytest.mark.django_db
def test_heal_rebinds_gold_label_success_criterion_on_closed_form_dataset():
    from conftest import frozen_dataset

    from overbae.models import Capability, EvalSetMember, Evaluator, Project
    from overbae.services.eval.eval_set import (
        _heal_generative_surface_bindings,
        ensure_default_eval_set,
    )
    from overbae.services.eval.grounding import (
        attach_example_dataset,
        resolve_grounding_for_capability,
    )
    from overbae.services.eval.surface_binding import GOLD_AGREEMENT_QUESTION

    card = {
        **SCALAR_LABEL_CODEBASE_CARD,
        "success_criteria": [
            *SCALAR_LABEL_CODEBASE_CARD["success_criteria"],
            "Returned label matches the gold intent",
        ],
    }
    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project,
        name="Intent",
        slug=f"intent-{uuid.uuid4().hex[:8]}",
        improvement_metadata={"capability_card": card},
    )
    frozen_dataset(
        project,
        [{"input": "cancel my order", "expected_output": "cancel_order"}],
        capability=capability,
        contract="eval",
    )
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Task Success",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope="final_output",
        applicable_roles=["generative"],
        rubric_md="Grade format.",
        checklist=[
            {
                "id": "taxonomy",
                "q": "Does the output use a valid taxonomy label?",
                "weight": 1.0,
            }
        ],
        variable_mapping=[{"var": "output", "source": "output", "jsonpath": ""}],
        config={
            "provenance": {
                "generator": TIER1_GENERATOR,
                "source": "codebase_card.success_criteria",
                "surface_area": "output_contract",
            }
        },
    )
    eval_set = ensure_default_eval_set(capability)
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role="generative", enabled=True
    )
    grounding = attach_example_dataset(resolve_grounding_for_capability(capability))
    healed, disabled = _heal_generative_surface_bindings(eval_set, grounding)
    evaluator.refresh_from_db()
    questions = [str(item.get("q") or "") for item in evaluator.checklist]
    assert healed == 1
    assert disabled == 0
    assert GOLD_AGREEMENT_QUESTION in questions
    assert evaluator.requires_reference is True


@pytest.mark.django_db
def test_landing_eval_dataset_enqueues_card_evaluator_sync(
    monkeypatch, django_capture_on_commit_callbacks
):
    from conftest import frozen_dataset

    from overbae.models import Capability, Evaluator, Project

    queued: list[str] = []
    monkeypatch.setattr(
        "overbae.tasks.eval.sync_card_evaluators_task.delay",
        lambda *, capability_id: queued.append(capability_id),
    )

    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project,
        name="Intent",
        slug=f"intent-{uuid.uuid4().hex[:8]}",
        improvement_metadata={
            "capability_card": {
                **SCALAR_LABEL_CODEBASE_CARD,
                "success_criteria": [
                    "Predicted label exactly equals the dataset's expected label",
                ],
            }
        },
    )
    with django_capture_on_commit_callbacks(execute=True):
        frozen_dataset(
            project,
            [{"input": "cancel my order", "expected_output": "cancel_order"}],
            capability=capability,
            contract="eval",
        )
    assert queued == [str(capability.id)]

    from overbae.services.eval.eval_set import sync_card_evaluators

    sync_card_evaluators(capability)
    assert Evaluator.objects.filter(capability=capability, name="label-accuracy").exists()
