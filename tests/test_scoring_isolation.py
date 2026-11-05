from __future__ import annotations

import pytest
from factories import make_project

from overbae.innermind import comparison
from overbae.models import EvalRun, EvalScore

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
