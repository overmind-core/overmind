from __future__ import annotations

import pytest
from asgiref.sync import async_to_sync
from factories import make_project

from overbae.innermind import comparison, deterministic, evidence
from overbae.models import EvalRun, EvalScore, Span

pytestmark = pytest.mark.covers("metrics-math")


@pytest.mark.django_db
def test_comparison_uses_the_same_nonnull_pairs_for_both_means():
    project = make_project()
    baseline = EvalRun.objects.create(project=project, name="baseline")
    candidate = EvalRun.objects.create(project=project, name="candidate")
    for run, values in [(baseline, [0.1, 0.9]), (candidate, [0.1, None])]:
        for index, value in enumerate(values):
            EvalScore.objects.create(
                project=project,
                run=run,
                source_id=str(index),
                source_type="dataset_row",
                score=value,
                metrics={"quality": {"score": value}},
            )
    result = comparison.compare(baseline, candidate)
    assert result["overall"]["delta"] == 0
    assert result["metrics"][0]["delta"] == 0
    assert result["paired"] == result["ties"] == 1
    assert result["wins"] == result["losses"] == 0
    assert result["coverage"] == {"observed_rows": 2, "baseline_scored": 2, "candidate_scored": 1}


@pytest.mark.parametrize("value", ['float("nan")', '(float("nan"), "reason")', 'float("inf")'])
def test_deterministic_nonfinite_scores_are_errors(value):
    span = Span(span_id="span", trace_id="trace", attributes={}, usage={})
    result = async_to_sync(deterministic.run)(
        f"def evaluate(span):\n    return {value}\n", span, [span]
    )
    assert result["outcome"] == "error"
    assert result["score"] is None


@pytest.mark.covers("capacity")
def test_cyclic_ancestry_terminates_without_inventing_intent():
    a = Span(span_id="a", parent_span_id="b", attributes={})
    b = Span(span_id="b", parent_span_id="a", attributes={})
    assert evidence.read_intent(a, {"a": a, "b": b}) == ""
