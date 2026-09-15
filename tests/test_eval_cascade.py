from __future__ import annotations

import re

from overbae.services.eval import cascade
from overbae.services.eval import funnel as judging
from overbae.services.eval.evaluators.base import EvalUnit, JudgeItem, JudgeResult
from tests.factories import evaluator_stub


def _evaluator(**overrides):
    return evaluator_stub(
        **{
            "name": "trajectory_quality",
            "kind": "trajectory",
            "scope": "trajectory",
            "config": {
                "long_trace_strategy": "per_step",
                "aggregation": "mean",
                "step_fail_threshold": 0.5,
                # Judge every step (disable salient-only/cap) so tests are explicit.
                "judge_salient_only": False,
                "max_steps_judged": 0,
            },
            "pass_threshold": 0.5,
            "rubric_md": "Judge trajectory quality.",
            **overrides,
        }
    )


def _unit_with_steps(tools, structured_extra=None):
    nodes = []
    for i, tool in enumerate(tools):
        nodes.append(
            {
                "id": f"step_{i}",
                "tool": tool,
                "arguments": {},
                "result": "ok",
                "error": "",
                "depends_on": [f"step_{i - 1}"] if i else [],
            }
        )
    structured = {"tool_graph": {"nodes": nodes}, "approx_tokens": 5, "num_tool_calls": len(nodes)}
    structured.update(structured_extra or {})
    return EvalUnit(
        trajectory={"final_output": "done", "messages": [{"role": "user", "content": "go"}]},
        structured=structured,
    )


def _patch_judge(monkeypatch, fake):
    monkeypatch.setattr(cascade.judging, "invoke_judge", fake)
    monkeypatch.setattr(
        cascade.judging,
        "resolve_judge",
        lambda *a, **k: judging.ResolvedJudge(None, None, "openai"),
    )


def _outcome(items=None, score=1.0):
    parsed = JudgeResult(
        items=[JudgeItem(id=i, score=s, reasoning="") for i, s in (items or [])],
        score=score,
        reasoning="r",
    )
    return judging.JudgeOutcome(
        parsed=parsed, raw="{}", stats={"response_cost": 0.0001}, judge_trace_id="t"
    )


def test_batched_single_call_for_many_steps(monkeypatch):
    calls = []

    def fake(prompt, **kw):
        calls.append(prompt)
        ids = re.findall(r"\[(step_\d+)\]", prompt)
        return _outcome(items=[(sid, 0.9) for sid in ids])

    _patch_judge(monkeypatch, fake)
    unit = _unit_with_steps([f"t{i}" for i in range(8)])
    ev = _evaluator(config={**_evaluator().config, "step_window": 20})
    out = cascade.run_cascade(
        unit, ev, {"project_id": "p"}, strategy="per_step", budget=60000, approx_tokens=5
    )
    assert len(calls) == 1, f"expected 1 batched call, got {len(calls)}"
    assert len(out.drafts[0].sub_scores) == 8


def test_windowing_when_many_steps(monkeypatch):
    calls = []

    def fake(prompt, **kw):
        calls.append(prompt)
        ids = re.findall(r"\[(step_\d+)\]", prompt)
        return _outcome(items=[(sid, 0.8) for sid in ids])

    _patch_judge(monkeypatch, fake)
    unit = _unit_with_steps([f"t{i}" for i in range(10)])
    ev = _evaluator(config={**_evaluator().config, "step_window": 4})
    cascade.run_cascade(
        unit, ev, {"project_id": "p"}, strategy="per_step", budget=60000, approx_tokens=5
    )
    # 10 steps / window 4 => 3 calls (4 + 4 + 2), far fewer than 10.
    assert len(calls) == 3


def test_root_cause_vs_propagated(monkeypatch):
    def fake(prompt, **kw):
        return _outcome(items=[("step_0", 0.2), ("step_1", 0.3)])

    _patch_judge(monkeypatch, fake)
    unit = _unit_with_steps(["search", "summarize"])
    ev = _evaluator()
    draft = cascade.run_cascade(
        unit, ev, {"project_id": "p"}, strategy="per_step", budget=60000, approx_tokens=5
    ).drafts[0]
    sub = {s["id"]: s for s in draft.sub_scores}
    assert sub["step_0"]["failure_role"] == "root_cause"
    assert sub["step_1"]["failure_role"] == "propagated"
    assert draft.failure_role == "root_cause"


def test_gate_short_circuits_with_zero_judge_calls(monkeypatch):
    calls = []

    def fake(prompt, **kw):
        calls.append(prompt)
        return _outcome(score=1.0)

    _patch_judge(monkeypatch, fake)
    unit = _unit_with_steps(["x"])
    unit.trajectory["final_output"] = "BAD"
    ev = _evaluator(
        config={
            "long_trace_strategy": "per_step",
            "gates": [{"check": "contains", "reference": "GOOD"}],
        }
    )
    out = cascade.run_cascade(
        unit, ev, {"project_id": "p"}, strategy="per_step", budget=60000, approx_tokens=5
    )
    assert out.drafts[0].value == 0.0
    assert out.drafts[0].failure_role == "root_cause"
    assert len(calls) == 0, "gate failure must not make any LLM calls"


def test_salient_only_selection(monkeypatch):
    judged_ids = []

    def fake(prompt, **kw):
        ids = re.findall(r"\[(step_\d+)\]", prompt)
        judged_ids.extend(ids)
        return _outcome(items=[(sid, 0.9) for sid in ids])

    _patch_judge(monkeypatch, fake)
    unit = _unit_with_steps(
        ["a", "b", "c", "d"],
        structured_extra={"salient_steps": [{"type": "tool_error", "ref": "step_1"}]},
    )
    ev = _evaluator(config={**_evaluator().config, "judge_salient_only": True})
    cascade.run_cascade(
        unit, ev, {"project_id": "p"}, strategy="per_step", budget=60000, approx_tokens=5
    )
    assert judged_ids == ["step_1"]


def test_maybe_route_skips_outcome_role_over_budget():
    unit = _unit_with_steps(["lookup", "analyze"])
    unit.structured["approx_tokens"] = 100_000
    ev = _evaluator(
        config={
            **_evaluator().config,
            "long_trace_strategy": "per_step",
            "behaviour": {"role": "outcome"},
        }
    )
    assert cascade.maybe_route(unit, ev, {}) is None


_LONG_REASONING = (
    "the agent retrieved the quarterly figures but summed the values by hand "
    "rather than calculating them with the spreadsheet tool so the reported "
    "total drifted from the ledger and the reconciliation step never flagged "
    "the delta because it compared against the same hand-summed number "
    "instead of the authoritative ledger export it had already fetched"
)


def test_clip_clean_full_word_and_sentence_boundaries():
    assert cascade._clip_clean("short reason", 140) == "short reason"
    word_cut = cascade._clip_clean(_LONG_REASONING, 140)
    assert word_cut.endswith("…")
    body = word_cut.removesuffix("…")
    assert _LONG_REASONING.startswith(body)
    assert _LONG_REASONING[len(body)] == " "  # never a mid-word cut
    sentences = "The first step fetched the ledger. The second step summed by hand. " * 5
    sentence_cut = cascade._clip_clean(sentences, 140)
    assert sentence_cut.endswith(".")
    assert sentences.startswith(sentence_cut)


def test_persisted_reason_never_ends_mid_word(monkeypatch):
    def fake(prompt, **kw):
        ids = re.findall(r"\[(step_\d+)\]", prompt)
        parsed = JudgeResult(
            items=[JudgeItem(id=sid, score=0.2, reasoning=_LONG_REASONING) for sid in ids],
            score=0.2,
            reasoning="",
        )
        return judging.JudgeOutcome(
            parsed=parsed, raw="{}", stats={"response_cost": 0.0001}, judge_trace_id="t"
        )

    _patch_judge(monkeypatch, fake)
    unit = _unit_with_steps(["search", "sum", "reconcile"])
    draft = cascade.run_cascade(
        unit, _evaluator(), {"project_id": "p"}, strategy="per_step", budget=60000, approx_tokens=5
    ).drafts[0]
    assert draft.reasoning.startswith("Root-cause failure at step_0")
    body = draft.reasoning.split(": ", 1)[1].removesuffix("…")
    assert _LONG_REASONING.startswith(body)
    assert _LONG_REASONING[len(body)] == " "


def test_coverage_signature():
    assert (
        cascade._coverage(judged=4, total=4, approx_tokens=10, budget=60000, holistic_ran=False)
        == 1.0
    )
    assert (
        cascade._coverage(judged=2, total=10, approx_tokens=10, budget=5, holistic_ran=False) == 0.2
    )
    # A holistic pass lifts coverage even when only some steps were judged.
    assert (
        cascade._coverage(judged=2, total=10, approx_tokens=10, budget=5, holistic_ran=True) == 0.9
    )
