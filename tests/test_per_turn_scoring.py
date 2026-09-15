from __future__ import annotations

from types import SimpleNamespace


def test_per_turn_judge_emits_one_draft_per_dimension(monkeypatch):
    from overbae.services.eval import funnel as judging
    from overbae.services.eval import per_turn_judge
    from overbae.services.eval.evaluators.base import EvalUnit

    verdict = per_turn_judge._TurnVerdict(
        tool_choice=1.0, args_grounded=0.5, progress=1, safety=None, reasoning="ok"
    )
    monkeypatch.setattr(
        judging,
        "resolve_default_judge",
        lambda *a, **k: SimpleNamespace(model_name="x", model_spec=None, family="x"),
    )
    monkeypatch.setattr(
        judging,
        "invoke_judge",
        lambda *a, **k: SimpleNamespace(parsed=verdict, stats={}, judge_trace_id="t"),
    )
    ev = SimpleNamespace(
        name="Per-turn decision quality",
        config={"per_turn_judge": True, "dimensions": ["tool_choice", "args_grounded", "progress"]},
        rubric_md="rubric",
    )
    unit = EvalUnit(
        structured={
            "tool_graph": {"nodes": [{"tool": "a", "arguments": {}}]},
            "_reference_final": "ref",
            "_candidate_final": "cand",
        },
        expected={"trajectory": [{"tool": "a", "arguments": {}}], "tools": []},
    )
    drafts = per_turn_judge.evaluate(unit, ev, {})
    assert [d.name for d in drafts] == [
        "Per-turn decision quality: tool choice",
        "Per-turn decision quality: args grounded",
        "Per-turn decision quality: progress",
    ]
    assert [d.value for d in drafts] == [1.0, 0.5, 1.0]  # progress +1 → 1.0
    assert drafts[2].string_value == "1"


def test_per_turn_judge_gates_tool_dims_when_reference_has_no_tools(monkeypatch):
    from overbae.services.eval import funnel as judging
    from overbae.services.eval import per_turn_judge
    from overbae.services.eval.evaluators.base import EvalUnit

    verdict = per_turn_judge._TurnVerdict(
        tool_choice=0.0, args_grounded=0.0, progress=1, safety=None, reasoning="reasoning-only turn"
    )
    monkeypatch.setattr(
        judging,
        "resolve_default_judge",
        lambda *a, **k: SimpleNamespace(model_name="x", model_spec=None, family="x"),
    )
    monkeypatch.setattr(
        judging,
        "invoke_judge",
        lambda *a, **k: SimpleNamespace(parsed=verdict, stats={}, judge_trace_id="t"),
    )
    ev = SimpleNamespace(
        name="Per-turn decision quality",
        config={"per_turn_judge": True, "dimensions": ["tool_choice", "args_grounded", "progress"]},
        rubric_md="r",
    )
    unit = EvalUnit(
        structured={"tool_graph": {"nodes": []}, "_reference_final": "Thought: parse the SMILES"},
        expected={"trajectory": [], "tools": []},
    )
    drafts = per_turn_judge.evaluate(unit, ev, {})
    # tool_choice/args_grounded are gated out rather than scored 0.
    assert [d.name for d in drafts] == ["Per-turn decision quality: progress"]


def test_per_turn_judge_abstains_when_nothing_recorded():
    from overbae.services.eval import per_turn_judge
    from overbae.services.eval.evaluators.base import EvalUnit

    ev = SimpleNamespace(
        name="Per-turn decision quality", config={"per_turn_judge": True}, rubric_md="r"
    )
    unit = EvalUnit(structured={"tool_graph": {"nodes": []}}, expected="plain text reference")
    assert per_turn_judge.evaluate(unit, ev, {}) == []


def test_wants_safety_detects_safety_domain():
    from overbae.services.eval import per_turn_judge

    assert per_turn_judge._wants_safety({"failure_modes": ["explosive hazard check"]}) is True
    assert per_turn_judge._wants_safety({"success_criteria": ["answer the MCQ"]}) is False
