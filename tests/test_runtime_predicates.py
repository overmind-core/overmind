from __future__ import annotations

import json

import pytest

from overbae.services.eval import funnel as judging
from overbae.services.eval import predicates
from overbae.services.eval.evaluators import judge
from overbae.services.eval.evaluators.base import EvalUnit, JudgeItem, JudgeResult
from overbae.services.eval.rubric_compiler import build_judge_prompt
from tests.factories import evaluator_stub, expectation

_RUNTIME = {
    "expectations": [
        {"id": "currency", "kind": "contains", "spec": "USD", "scope": "trace", "gate": True}
    ],
    "context": {"user_tier": "premium", "retries": 2},
    "checkpoints": [{"name": "payment_confirmed", "span_id": "s"}],
}
_TRAJECTORY = {"modality": "tool_calling", "final_output": "done", "messages": []}


@pytest.mark.parametrize(
    ("pred", "expected"),
    [
        ({"expectation_declared": "currency"}, True),
        ({"expectation_declared": "contains"}, True),  # kind matches too
        ({"expectation_declared": "refund"}, False),
        ({"checkpoint_reached": "payment_confirmed"}, True),
        ({"checkpoint_reached": "shipped"}, False),
        ({"context_equals": {"key": "user_tier", "value": "premium"}}, True),
        ({"context_equals": {"key": "user_tier", "value": "free"}}, False),
        ({"context_equals": {"key": "missing", "value": None}}, False),
        ({"context_present": "retries"}, True),
        ({"context_present": "missing"}, False),
        ({"modality": "tool_calling"}, True),
        ({"modality": "single_turn"}, False),
        ({"all": [{"context_present": "retries"}, {"modality": "tool_calling"}]}, True),
        ({"all": [{"context_present": "retries"}, {"modality": "single_turn"}]}, False),
        ({"any": [{"context_present": "missing"}, {"modality": "tool_calling"}]}, True),
        ({"any": [{"context_present": "missing"}, {"modality": "single_turn"}]}, False),
        ({"not": [{"checkpoint_reached": "shipped"}]}, True),
        ({"not": [{"checkpoint_reached": "payment_confirmed"}]}, False),
        (
            {
                "all": [
                    {"expectation_declared": "currency"},
                    {"not": [{"context_equals": {"key": "user_tier", "value": "free"}}]},
                ]
            },
            True,
        ),
    ],
)
def test_predicate_truth_table(pred, expected):
    applies, reason = predicates.predicate_applies(pred, _RUNTIME, _TRAJECTORY)
    assert applies is expected
    if not expected:
        assert json.dumps(pred, default=str) in reason


def test_output_present_leaf():
    pred = {"output_present": True}
    assert predicates.predicate_applies(pred, {}, {"final_output": "done"})[0] is True
    assert predicates.predicate_applies(pred, {}, {"final_output": "   "})[0] is False
    assert predicates.predicate_applies(pred, {}, {})[0] is False


def test_tool_called_leaf_reads_span_tree_and_messages():
    trajectory = {
        "span_tree": [
            {
                "type": "agent",
                "children": [
                    {"type": "tool_call", "tool": "Tools.act:click", "children": []},
                ],
            }
        ],
        "messages": [
            {"role": "assistant", "tool_calls": [{"function": {"name": "navigate"}}]},
        ],
    }
    pred_true = [
        {"tool_called": "click"},  # dispatcher-qualified name matches bare suffix
        {"tool_called": "Tools.act:click"},
        {"tool_called": "navigate"},
    ]
    for pred in pred_true:
        assert predicates.predicate_applies(pred, {}, trajectory)[0] is True, pred
    applies, reason = predicates.predicate_applies({"tool_called": "input"}, {}, trajectory)
    assert applies is False
    assert "tool_called" in reason
    assert predicates.predicate_applies({"tool_called": "click"}, {}, {})[0] is False


def test_tool_called_malformed_arg_excludes():
    applies, reason = predicates.predicate_applies({"tool_called": 7}, {}, {})
    assert applies is False
    assert reason.startswith("malformed applies_when")


def test_output_present_treats_empty_json_deliverable_as_absent():
    # []/{}/"null" are the declared refusal shapes, not present output.
    pred = {"output_present": True}
    for empty in ("[]", "{}", "null", "None", " [ ] "):
        assert predicates.predicate_applies(pred, {}, {"final_output": empty})[0] is False, empty
    for present in ('[{"a": 1}]', '{"a": null}', "{not-json", "prose answer"):
        assert predicates.predicate_applies(pred, {}, {"final_output": present})[0] is True, present


@pytest.mark.parametrize(
    "pred",
    [
        "not-a-dict",
        {},
        {"all": [{"modality": "x"}], "any": []},
        {"unknown_op": "x"},
        {"all": []},
        {"all": "not-a-list"},
        {"expectation_declared": 7},
        {"context_equals": {"key": "k"}},
        {"not": [{"bad": True}]},
        {"output_present": "yes"},
    ],
)
def test_malformed_predicates_exclude_with_named_reason(pred):
    applies, reason = predicates.predicate_applies(pred, _RUNTIME, _TRAJECTORY)
    assert applies is False
    assert reason.startswith("malformed applies_when")


def test_filter_checklist_partitions_and_records_reason():
    checklist = [
        {"id": "always", "q": "always applies?"},
        {"id": "gated_in", "q": "in?", "applies_when": {"expectation_declared": "currency"}},
        {"id": "gated_out", "q": "out?", "applies_when": {"checkpoint_reached": "shipped"}},
    ]
    applicable, excluded = predicates.filter_checklist(checklist, _RUNTIME, _TRAJECTORY)
    assert [i["id"] for i in applicable] == ["always", "gated_in"]
    assert excluded[0]["item"]["id"] == "gated_out"
    na = predicates.not_applicable_sub_verdicts(excluded)
    assert na == [
        {
            "id": "gated_out",
            "verdict": None,
            "score": None,
            "outcome": "not_applicable",
            "reasoning": excluded[0]["reason"],
        }
    ]


class TestExpectationPrepass:
    def test_declared_and_met_passes(self):
        subs, synthetic, gated_fail = predicates.expectation_prepass(
            [expectation()], "Total: 100 usd."
        )
        assert subs[0]["id"] == "rt_currency"
        assert subs[0]["verdict"] is True
        assert synthetic == []
        assert gated_fail is False

    def test_declared_and_unmet_gated_fails(self):
        subs, _synthetic, gated_fail = predicates.expectation_prepass(
            [expectation()], "Total: 100."
        )
        assert subs[0]["verdict"] is False
        assert gated_fail is True

    def test_unmet_ungated_fails_without_gating(self):
        subs, _synthetic, gated_fail = predicates.expectation_prepass(
            [expectation(gate=False)], "Total: 100."
        )
        assert subs[0]["verdict"] is False
        assert gated_fail is False

    def test_undeclared_produces_nothing(self):
        assert predicates.expectation_prepass([], "anything") == ([], [], False)

    def test_contains_list_spec_requires_all(self):
        subs, _, _ = predicates.expectation_prepass(
            [expectation(spec=["USD", "total"])], "Total: 100 USD"
        )
        assert subs[0]["verdict"] is True
        subs, _, _ = predicates.expectation_prepass(
            [expectation(spec=["USD", "vat"])], "Total: 100 USD"
        )
        assert subs[0]["verdict"] is False

    def test_regex_search(self):
        subs, _, _ = predicates.expectation_prepass(
            [expectation(kind="regex", spec=r"\b[A-Z]{3}\b")], "Total: 100 USD"
        )
        assert subs[0]["verdict"] is True

    def test_invalid_regex_records_null_verdict_and_never_gates(self):
        subs, _, gated_fail = predicates.expectation_prepass(
            [expectation(kind="regex", spec="([unclosed")], "anything"
        )
        assert subs[0]["verdict"] is None
        assert "invalid regex" in subs[0]["reasoning"]
        assert gated_fail is False

    def test_schema_required_keys(self):
        exp = expectation(kind="schema", spec={"required": ["amount", "currency"]})
        subs, _, _ = predicates.expectation_prepass([exp], '{"amount": 1, "currency": "USD"}')
        assert subs[0]["verdict"] is True
        subs, _, gated_fail = predicates.expectation_prepass([exp], '{"amount": 1}')
        assert subs[0]["verdict"] is False
        assert "currency" in subs[0]["reasoning"]
        assert gated_fail is True

    def test_schema_invalid_json_fails(self):
        subs, _, _ = predicates.expectation_prepass(
            [expectation(kind="schema", spec=["amount"])], "not json"
        )
        assert subs[0]["verdict"] is False

    def test_constraint_compiles_to_synthetic_checklist_item(self):
        exp = expectation(
            exp_id="tone", kind="constraint", spec="Reply must be formal.", gate=False
        )
        subs, synthetic, _ = predicates.expectation_prepass([exp], "whatever")
        assert subs == []
        assert synthetic == [
            {"id": "rt_tone", "q": "Reply must be formal.", "weight": 1.0, "gate": False}
        ]

    def test_conversation_scope_skipped_at_unit_level(self):
        assert predicates.expectation_prepass([expectation(scope="conversation")], "no usd") == (
            [],
            [],
            False,
        )


def _evaluator(**overrides):
    return evaluator_stub(
        **{
            "name": "Judge",
            "kind": "llm_judge",
            "score_type": "boolean",
            "checklist": [{"id": "quality", "q": "Is the answer helpful?", "weight": 1.0}],
            "variable_mapping": [{"var": "output", "source": "output"}],
            **overrides,
        }
    )


def _unit(final_output: str, runtime: dict | None = None) -> EvalUnit:
    trajectory = {
        "final_output": final_output,
        "messages": [{"role": "user", "content": "how much?"}],
        "modality": "single_turn",
        "metadata": {},
    }
    if runtime is not None:
        trajectory["runtime"] = runtime
    return EvalUnit(trajectory=trajectory)


def _patch_judge(monkeypatch, captured: dict, *, score=1.0, items=None):
    def fake_invoke_judge(prompt, **kwargs):
        captured["prompt"] = prompt
        return judging.JudgeOutcome(
            parsed=JudgeResult(items=items or [], score=score, reasoning="ok"),
            raw="{}",
            stats={"response_cost": 0.0, "response_ms": 0},
            judge_trace_id="jt",
        )

    monkeypatch.setattr(judging, "invoke_judge", fake_invoke_judge)


def test_currency_declared_unmet_gated_caps_boolean_score(monkeypatch):
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0, items=[JudgeItem(id="quality", verdict=True)])
    runtime = {"expectations": [expectation()]}
    drafts = judge.evaluate(_unit("Total: 100.", runtime), _evaluator(), {})
    draft = drafts[0]
    assert draft.value == 0.0
    assert draft.passed is False
    det = next(s for s in draft.sub_scores if s.get("id") == "rt_currency")
    assert det["verdict"] is False
    assert any(s.get("_runtime_gate", {}).get("gated_fail") for s in draft.sub_scores)
    assert any(s.get("_runtime", {}).get("envelope_present") is True for s in draft.sub_scores)


def test_currency_declared_met_passes_through(monkeypatch):
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0, items=[JudgeItem(id="quality", verdict=True)])
    runtime = {"expectations": [expectation()]}
    drafts = judge.evaluate(_unit("Total: 100 USD.", runtime), _evaluator(), {})
    draft = drafts[0]
    assert draft.value == 1.0
    assert draft.passed is True
    det = next(s for s in draft.sub_scores if s.get("id") == "rt_currency")
    assert det["verdict"] is True


def test_behaviour_outcome_judge_does_not_inherit_envelope_expects(monkeypatch):
    captured: dict = {}
    _patch_judge(
        monkeypatch,
        captured,
        score=1.0,
        items=[JudgeItem(id="serves-running-intent", verdict=True, score=1.0)],
    )
    runtime = {
        "expectations": [
            expectation(),
            expectation(
                exp_id="entity-refs-grounded",
                kind="constraint",
                spec="Entity references use only [[source_ref|display name]] tokens.",
                gate=False,
            ),
        ]
    }
    ev = _evaluator(
        score_type="numeric",
        config={"behaviour": {"behaviour_key": "happy", "role": "outcome"}},
        checklist=[
            {"id": "serves-running-intent", "q": "Did the actions serve the ask?", "gate": True}
        ],
        rubric_md="Judge intent only.",
    )
    draft = judge.evaluate(_unit("queued the job without tokens", runtime), ev, {})[0]
    assert draft.value == 1.0
    assert draft.passed is True
    prompt = captured["prompt"]
    assert "serves-running-intent" in prompt
    assert "Declared expectations" not in prompt
    assert "rt_currency" not in prompt
    assert "source_ref" not in prompt
    assert not any(str(s.get("id") or "").startswith("rt_") for s in draft.sub_scores)


def test_undeclared_leaves_draft_untouched(monkeypatch):
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0)
    drafts = judge.evaluate(_unit("Total: 100."), _evaluator(), {})
    draft = drafts[0]
    assert draft.value == 1.0
    assert not any("rt_" in str(s.get("id", "")) for s in draft.sub_scores)
    assert not any("_runtime" in s for s in draft.sub_scores)
    assert "Runtime declarations" not in captured["prompt"]


def test_constraint_expectation_joins_judge_checklist(monkeypatch):
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0)
    runtime = {
        "expectations": [
            expectation(exp_id="tone", kind="constraint", spec="Be formal.", gate=False)
        ]
    }
    judge.evaluate(_unit("hello", runtime), _evaluator(), {})
    assert "(rt_tone) Be formal." in captured["prompt"]


def test_gated_synthetic_constraint_uses_existing_gate_mechanism(monkeypatch):
    captured: dict = {}
    _patch_judge(
        monkeypatch,
        captured,
        score=1.0,
        items=[JudgeItem(id="quality", verdict=True), JudgeItem(id="rt_tone", verdict=False)],
    )
    runtime = {
        "expectations": [
            expectation(exp_id="tone", kind="constraint", spec="Be formal.", gate=True)
        ]
    }
    drafts = judge.evaluate(_unit("yo", runtime), _evaluator(), {})
    assert drafts[0].value == 0.0


def test_applies_when_excludes_item_from_prompt_with_na_verdict(monkeypatch):
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0)
    evaluator = _evaluator(
        checklist=[
            {"id": "quality", "q": "Is the answer helpful?", "weight": 1.0},
            {
                "id": "refund_check",
                "q": "Was the refund policy honored?",
                "weight": 1.0,
                "applies_when": {"expectation_declared": "refund"},
            },
        ]
    )
    drafts = judge.evaluate(_unit("hello", {"context": {"k": "v"}}), evaluator, {})
    assert "Is the answer helpful?" in captured["prompt"]
    assert "refund policy" not in captured["prompt"]
    na = next(s for s in drafts[0].sub_scores if s.get("id") == "refund_check")
    assert na["outcome"] == "not_applicable"
    assert "applies_when" in na["reasoning"]


def test_all_items_excluded_returns_not_applicable_without_judge_call(monkeypatch):
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0)
    evaluator = _evaluator(
        checklist=[
            {
                "id": "refund_check",
                "q": "Refund honored?",
                "applies_when": {"expectation_declared": "refund"},
            }
        ]
    )
    drafts = judge.evaluate(_unit("hello"), evaluator, {})
    assert drafts[0].outcome == "not_applicable"
    assert drafts[0].value is None
    assert "prompt" not in captured
    assert drafts[0].sub_scores[0]["id"] == "refund_check"


def test_all_items_excluded_skips_orphan_synthetic_constraints(monkeypatch):
    """The verdict on a declared constraint belongs to evaluators whose checklist applies."""
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=0.0)
    evaluator = _evaluator(
        checklist=[
            {
                "id": "refund_check",
                "q": "Refund honored?",
                "applies_when": {"expectation_declared": "refund"},
            }
        ]
    )
    runtime = {
        "expectations": [
            expectation(exp_id="tone", kind="constraint", spec="Be formal.", gate=False)
        ]
    }
    drafts = judge.evaluate(_unit("hello", runtime), evaluator, {})
    assert drafts[0].outcome == "not_applicable"
    assert drafts[0].value is None
    assert "prompt" not in captured


def test_build_judge_prompt_runtime_section_binding():
    evaluator = _evaluator()
    runtime = {
        "expectations": [expectation()],
        "context": {"user_tier": "premium"},
        "checkpoints": [{"name": "start", "span_id": "a"}, {"name": "paid", "span_id": "b"}],
        "prompt_records": [
            {
                "span_id": "s1",
                "template": "Tools: {tools}",
                "kwargs": {"tools": "search"},
                "rendered": "Tools: search",
            }
        ],
    }
    prompt = build_judge_prompt(evaluator, {"output": "100 USD"}, runtime=runtime)
    assert "Runtime declarations" in prompt
    assert "currency (contains, gate): USD" in prompt
    assert "user_tier: premium" in prompt
    assert "Checkpoint path: start -> paid" in prompt
    assert "Tools: {tools}" in prompt
    assert '{"tools": "search"}' in prompt


def test_evaluator_level_applies_when_false_yields_single_not_applicable(monkeypatch):
    from overbae.services.eval.evaluators import base as eval_base

    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0)
    evaluator = _evaluator(config={"applies_when": {"expectation_declared": "invoice"}})
    drafts = eval_base.evaluate(_unit("hello", {"context": {"k": "v"}}), evaluator, {})
    assert len(drafts) == 1
    assert drafts[0].outcome == "not_applicable"
    assert drafts[0].value is None
    assert "applies_when not satisfied" in drafts[0].reasoning
    assert drafts[0].sub_scores[0]["_applies_when"]["predicate"] == {
        "expectation_declared": "invoice"
    }
    assert "prompt" not in captured


def test_evaluator_level_applies_when_true_runs_normally(monkeypatch):
    from overbae.services.eval.evaluators import base as eval_base

    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0, items=[JudgeItem(id="quality", verdict=True)])
    evaluator = _evaluator(config={"applies_when": {"expectation_declared": "invoice"}})
    runtime = {
        "expectations": [expectation(exp_id="invoice", kind="constraint", spec="x", gate=False)]
    }
    drafts = eval_base.evaluate(_unit("an invoice summary", runtime), evaluator, {})
    assert drafts[0].outcome == "scored"
    assert "prompt" in captured


def test_evaluator_level_malformed_applies_when_excludes_with_reason(monkeypatch):
    from overbae.services.eval.evaluators import base as eval_base

    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0)
    evaluator = _evaluator(config={"applies_when": {"bogus_op": 1}})
    drafts = eval_base.evaluate(_unit("hello"), evaluator, {})
    assert drafts[0].outcome == "not_applicable"
    assert drafts[0].reasoning.startswith("malformed applies_when")
    assert "prompt" not in captured


def test_correct_refusal_empty_output_with_declared_expectation_is_not_graded(monkeypatch):
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=0.0)
    runtime = {
        "expectations": [
            expectation(
                exp_id="non-invoice-null-fields",
                kind="constraint",
                spec="Payable fields must be null.",
                gate=True,
            )
        ]
    }
    drafts = judge.evaluate(_unit("[]", runtime), _evaluator(), {})
    assert drafts[0].outcome == "not_applicable"
    assert drafts[0].value is None
    assert "non-invoice-null-fields" in drafts[0].reasoning
    assert "prompt" not in captured


def test_broken_empty_output_without_declarations_keeps_low_provenance_grading(monkeypatch):
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=0.0)
    drafts = judge.evaluate(_unit(""), _evaluator(), {})
    assert drafts[0].outcome == "scored"
    assert drafts[0].value == 0.0
    assert drafts[0].reasoning.startswith("⚠ Low provenance")
    assert "prompt" in captured


def test_explicit_empty_container_is_rejection_evidence_not_low_provenance(monkeypatch):
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0, items=[JudgeItem(id="quality", verdict=True)])
    drafts = judge.evaluate(_unit("[]"), _evaluator(), {})
    assert drafts[0].outcome == "scored"
    assert "Low provenance" not in (drafts[0].reasoning or "")
    assert "prompt" in captured
    assert "rejection decision" in captured["prompt"]


def test_empty_output_with_deterministically_failed_expectation_is_genuine_fail(monkeypatch):
    """Brokenness, not refusal: scored at score_min without a judge call."""
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0, items=[JudgeItem(id="quality", verdict=True)])
    runtime = {"expectations": [expectation()]}  # gated contains "USD", unmet on ""
    drafts = judge.evaluate(_unit("", runtime), _evaluator(), {})
    assert drafts[0].outcome == "scored"
    assert drafts[0].value == 0.0
    assert drafts[0].passed is False
    assert "rt_currency" in drafts[0].reasoning
    assert "prompt" not in captured


def test_empty_output_det_failure_scores_numeric_evaluators_consistently(monkeypatch):
    """Numeric evaluators have no gate cap, so the deterministic verdict must cover them."""
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0, items=[JudgeItem(id="quality", verdict=True)])
    runtime = {"expectations": [expectation()]}
    evaluator = _evaluator(score_type="numeric")
    drafts = judge.evaluate(_unit("", runtime), evaluator, {})
    assert drafts[0].outcome == "scored"
    assert drafts[0].value == 0.0
    assert "prompt" not in captured
    det = next(s for s in drafts[0].sub_scores if s.get("id") == "rt_currency")
    assert det["verdict"] is False


def test_nonempty_output_with_declared_expectations_grades_normally(monkeypatch):
    captured: dict = {}
    _patch_judge(monkeypatch, captured, score=1.0, items=[JudgeItem(id="quality", verdict=True)])
    runtime = {
        "expectations": [
            expectation(exp_id="tone", kind="constraint", spec="Be formal.", gate=False)
        ]
    }
    drafts = judge.evaluate(_unit("A perfectly formal answer.", runtime), _evaluator(), {})
    assert drafts[0].outcome == "scored"
    assert drafts[0].value == 1.0


def test_build_judge_prompt_unchanged_without_runtime():
    evaluator = _evaluator()
    baseline = build_judge_prompt(evaluator, {"output": "x"})
    assert build_judge_prompt(evaluator, {"output": "x"}, runtime=None) == baseline
    assert "Runtime declarations" not in baseline
    with_checklist = build_judge_prompt(evaluator, {"output": "x"}, checklist=evaluator.checklist)
    assert with_checklist == baseline
