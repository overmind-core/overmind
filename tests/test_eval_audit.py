from __future__ import annotations

import pytest

from overbae.models import (
    Capability,
    Dataset,
    EvalRun,
    EvalSample,
    EvalSet,
    EvalSetMember,
    Evaluator,
    EvalVariant,
    Project,
    Score,
)
from overbae.services.eval.audit import audit_evaluators

pytestmark = pytest.mark.django_db


def _project(slug="audit"):
    return Project.objects.create(name=slug, slug=slug)


def _judge(project, name, **kw):
    kw.setdefault("checklist", [{"id": "q1", "q": "?", "weight": 1.0}])
    return Evaluator.objects.create(
        project=project,
        name=name,
        kind=Evaluator.Kind.LLM_JUDGE,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        **kw,
    )


def test_a_trace_scoring_member_is_never_reported_as_unscored():
    # Regression: trace scoring records verdicts on Span.feedback_score, not in
    # Score, so measuring it against this query reported every one of its
    # members as silent. Five of the audit's first ten findings were this.
    project = _project("ts")
    agent = Capability.objects.create(project=project, name="A", slug="a-ts")
    eval_set = EvalSet.objects.create(project=project, capability=agent, name="S")
    EvalSetMember.objects.create(
        eval_set=eval_set,
        evaluator=_judge(project, "TraceOnly"),
        role=EvalSetMember.Role.TRACE_SCORING,
        enabled=True,
    )
    report = audit_evaluators()
    assert "TraceOnly" not in {f["evaluator"] for f in report["never_scored"]}
    assert "TraceOnly" not in {f["evaluator"] for f in report["never_evaluated"]}


def test_an_unevaluated_agent_is_reported_apart_from_a_silent_evaluator():
    # "Nobody ran an eval for this agent" says nothing about the evaluator, so
    # summing it with a genuine silence is how a pre-run gate cries wolf.
    project = _project("split")
    quiet_agent = Capability.objects.create(project=project, name="Q", slug="q-split")
    quiet_set = EvalSet.objects.create(project=project, capability=quiet_agent, name="Q")
    EvalSetMember.objects.create(
        eval_set=quiet_set,
        evaluator=_judge(project, "NeverRun"),
        role=EvalSetMember.Role.GENERATIVE,
        enabled=True,
    )

    busy_agent = Capability.objects.create(project=project, name="B", slug="b-split")
    busy_set = EvalSet.objects.create(project=project, capability=busy_agent, name="B")
    silent = _judge(project, "Silent")
    scorer = _judge(project, "Scorer")
    for ev in (silent, scorer):
        EvalSetMember.objects.create(
            eval_set=busy_set,
            evaluator=ev,
            role=EvalSetMember.Role.GENERATIVE,
            enabled=True,
        )
    dataset = Dataset.objects.create(project=project, capability=busy_agent, name="d")
    run = EvalRun.objects.create(project=project, name="r", dataset=dataset)
    variant = EvalVariant.objects.create(run=run, label="v", order=0)
    sample = EvalSample.objects.create(run=run, variant=variant)
    Score.objects.create(
        project=project,
        run=run,
        variant=variant,
        sample=sample,
        evaluator=scorer,
        name="Scorer",
        data_type="numeric",
        value=1.0,
        outcome="scored",
    )

    report = audit_evaluators()
    assert {f["evaluator"] for f in report["never_evaluated"]} >= {"NeverRun"}
    assert {f["evaluator"] for f in report["never_scored"]} >= {"Silent"}
    assert "NeverRun" not in {f["evaluator"] for f in report["never_scored"]}
    assert "Scorer" not in {f["evaluator"] for f in report["never_scored"]}


def _generative_member(project, evaluator, slug):
    agent = Capability.objects.create(project=project, name=slug, slug=slug)
    eval_set = EvalSet.objects.create(project=project, capability=agent, name="Default")
    return EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role=EvalSetMember.Role.GENERATIVE
    )


def test_flags_a_rubric_that_tells_the_judge_to_score_itself():
    # rubric_md reaches the judge verbatim, so a rubric written for the older
    # "return me a number" shape argues with a runtime that scores from verdicts.
    project = _project("machinery")
    ev = _judge(
        project,
        "Bespoke",
        rubric_md=(
            "3) amount matches total due — weight 0.18\n"
            "If the schema check fails the overall score MUST be 0.0.\n"
            "Output exactly one numeric score, rounded to 3 decimals."
        ),
    )
    _generative_member(project, ev, "m1")
    found = {
        f["evaluator"]: f["contradictions"] for f in audit_evaluators()["rubric_scoring_machinery"]
    }
    assert set(found["Bespoke"]) == {
        "states its own weights",
        "promises a hard fail",
        "asks the judge for a score",
        "describes its own arithmetic",
    }


def test_a_trace_scoring_rubric_may_ask_for_a_score():
    # Trace scoring asks the model for a number by design, so the same wording
    # is correct there and flagging it would be noise.
    project = _project("ts-rubric")
    ev = _judge(project, "TraceJudge", rubric_md="Output exactly one numeric score.")
    agent = Capability.objects.create(project=project, name="t", slug="t-rubric")
    eval_set = EvalSet.objects.create(project=project, capability=agent, name="Default")
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=ev, role=EvalSetMember.Role.TRACE_SCORING
    )
    found = {f["evaluator"] for f in audit_evaluators()["rubric_scoring_machinery"]}
    assert "TraceJudge" not in found


def test_a_plain_rubric_is_not_flagged():
    # The check earns its place by being specific: it fired on 1 of 41 live
    # judges. A rubric that merely describes what good looks like is fine.
    project = _project("plain")
    ev = _judge(
        project,
        "Plain",
        rubric_md=(
            "An amount is correct when it matches the total due, ignoring "
            "currency symbols and thousands separators."
        ),
    )
    _generative_member(project, ev, "m2")
    found = {f["evaluator"] for f in audit_evaluators()["rubric_scoring_machinery"]}
    assert "Plain" not in found


def test_flags_a_judge_with_no_checklist():
    ev = _judge(_project(), "Bare", checklist=[])
    names = [f["evaluator"] for f in audit_evaluators()["missing_checklist"]]
    assert ev.name in names


def test_flags_a_checklist_variable_nothing_binds():
    _judge(
        _project(),
        "Unbound",
        checklist=[{"id": "a", "q": "Consistent with {{corpus}}?", "weight": 1.0}],
        variable_mapping=[{"var": "output", "source": "output"}],
    )
    found = {f["evaluator"]: f["variables"] for f in audit_evaluators()["unbound_variables"]}
    assert found["Unbound"] == ["corpus"]


def test_flags_an_item_comparing_confidence_to_the_reference():
    _judge(
        _project(),
        "ConfItem",
        checklist=[
            {
                "id": "confidence_matches",
                "q": "Does output.confidence match the reference value?",
                "weight": 1.0,
            }
        ],
    )
    found = {f["evaluator"] for f in audit_evaluators()["confidence_comparisons"]}
    assert "ConfItem" in found


def test_flags_confidence_judged_against_the_evidence_not_just_a_reference():
    # Tier-1 authoring produced exactly this and the first version of the check
    # missed it: no comparison word, but it still asks a judge to decide on one
    # row whether a stated confidence is warranted.
    _judge(
        _project("evidence"),
        "ConfEvidence",
        checklist=[
            {
                "id": "confidence_alignment_with_evidence",
                "q": "Confidence calibration — does output.confidence reflect the evidence strength?",
                "weight": 1.0,
            }
        ],
    )
    found = {f["evaluator"] for f in audit_evaluators()["confidence_comparisons"]}
    assert "ConfEvidence" in found


def test_a_confidence_format_check_is_left_alone():
    # Checking the TYPE or RANGE of a stated confidence is a contract check and
    # has a right answer; only judging whether the value is warranted does not.
    _judge(
        _project("format"),
        "ConfFormat",
        checklist=[
            {
                "id": "confidence_range_and_type",
                "q": "Confidence format — is output.confidence a number between 0.0 and 1.0?",
                "weight": 1.0,
            }
        ],
    )
    found = {f["evaluator"] for f in audit_evaluators()["confidence_comparisons"]}
    assert "ConfFormat" not in found


def test_hedging_in_prose_is_not_treated_as_grading_a_confidence():
    # "uncertainty" contains "certainty". Whether an answer hedges appropriately
    # is a judgement about the text, not a calibration call on a stated number.
    _judge(
        _project("hedge"),
        "Hedging",
        checklist=[
            {
                "id": "appropriate_uncertainty_qualification",
                "q": "Is uncertainty appropriately qualified in the answer?",
                "weight": 1.0,
            }
        ],
    )
    found = {f["evaluator"] for f in audit_evaluators()["confidence_comparisons"]}
    assert "Hedging" not in found


def test_an_evaluator_newer_than_the_last_run_is_not_called_silent():
    # A suite that grows after a run would otherwise report every new member as
    # having stayed silent through runs that predate it.
    project = _project("fresh")
    agent = Capability.objects.create(project=project, name="A", slug="a-fresh")
    eval_set = EvalSet.objects.create(project=project, capability=agent, name="Default")
    old = _judge(project, "WasThere")
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=old, role=EvalSetMember.Role.GENERATIVE
    )
    dataset = Dataset.objects.create(project=project, capability=agent, name="d")
    run = EvalRun.objects.create(project=project, name="r", dataset=dataset)
    variant = EvalVariant.objects.create(run=run, label="v", order=0)
    sample = EvalSample.objects.create(run=run, variant=variant)
    Score.objects.create(
        project=project,
        run=run,
        variant=variant,
        sample=sample,
        evaluator=old,
        name="Something Else",
        data_type="numeric",
        value=1.0,
        outcome="scored",
    )

    # Authored after that run, so it was never asked.
    fresh = _judge(project, "AddedLater")
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=fresh, role=EvalSetMember.Role.GENERATIVE
    )

    report = audit_evaluators()
    assert "AddedLater" not in {f["evaluator"] for f in report["never_scored"]}
    assert "AddedLater" in {f["evaluator"] for f in report["never_evaluated"]}
    assert "WasThere" in {f["evaluator"] for f in report["never_scored"]}


def test_ignores_confidence_mentioned_as_a_condition():
    # Regression: a risk-flag item reading "any confidence < 0.85" is not a
    # reference comparison, and flagging it trains people to ignore the audit.
    _judge(
        _project(),
        "RiskFlags",
        checklist=[
            {
                "id": "flags_portfolio_issues",
                "q": (
                    "For every detected portfolio issue in the input (missing amount, "
                    "any confidence < 0.85), does the output flag it as the reference expects?"
                ),
                "weight": 1.0,
            }
        ],
    )
    found = {f["evaluator"] for f in audit_evaluators()["confidence_comparisons"]}
    assert "RiskFlags" not in found


def test_flags_items_that_always_fail_together():
    """The synonym-items defect: one error costs the whole score."""
    project = _project()
    agent = Capability.objects.create(project=project, name="a", slug="a")
    dataset = Dataset.objects.create(project=project, capability=agent, name="d")
    run = EvalRun.objects.create(project=project, name="r", data_source="dataset", dataset=dataset)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)
    for name, verdicts in (
        ("Synonyms", [False, False, False]),
        ("Independent", [False, True, True]),
    ):
        for _ in range(6):
            sample = EvalSample.objects.create(
                run=run, variant=variant, trajectory={"final_output": "x"}
            )
            Score.objects.create(
                project=project,
                run=run,
                variant=variant,
                sample=sample,
                name=name,
                data_type="numeric",
                value=0.5,
                outcome="scored",
                sub_scores=[{"id": f"i{n}", "verdict": v} for n, v in enumerate(verdicts)],
            )
    by_name = {f["evaluator"]: f for f in audit_evaluators()["co_failure"]}
    # Only one verdict combination was ever emitted, so this is the degenerate
    # case, not evidence of overlapping items.
    assert by_name["Synonyms"]["degenerate"] is True
    assert by_name["Synonyms"]["suspect"] is False


def test_separates_genuine_overlap_from_a_score_that_never_varies():
    """`seed_demo` stamps every item of one evaluator from a single boolean, which
    reads as total co-failure while saying nothing about item overlap."""
    project = _project()
    agent = Capability.objects.create(project=project, name="a", slug="a")
    dataset = Dataset.objects.create(project=project, capability=agent, name="d")
    run = EvalRun.objects.create(project=project, name="r", data_source="dataset", dataset=dataset)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)

    # Overlapping items: the judge varies, but a failure usually costs several.
    patterns = [
        [False, False, False],
        [False, False, True],
        [False, True, True],
        [True, True, True],
        [False, False, False],
        [False, False, True],
    ]
    for verdicts in patterns:
        sample = EvalSample.objects.create(
            run=run, variant=variant, trajectory={"final_output": "x"}
        )
        Score.objects.create(
            project=project,
            run=run,
            variant=variant,
            sample=sample,
            name="Overlapping",
            data_type="numeric",
            value=0.5,
            outcome="scored",
            sub_scores=[{"id": f"i{n}", "verdict": v} for n, v in enumerate(verdicts)],
        )
    found = {f["evaluator"]: f for f in audit_evaluators()["co_failure"]}["Overlapping"]
    assert found["degenerate"] is False
    assert found["suspect"] is True
