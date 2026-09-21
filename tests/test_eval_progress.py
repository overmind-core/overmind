from __future__ import annotations

import uuid

import pytest

from overbae.api.eval_serializers import (
    EvalRunListSerializer,
    EvalSampleListSerializer,
    compute_run_progress,
)
from overbae.models import (
    EvalRun,
    EvalSample,
    Evaluator,
    EvalVariant,
    Project,
    RunEvaluator,
    Score,
)

pytestmark = pytest.mark.django_db


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _run(project, status=EvalRun.Status.RUNNING) -> EvalRun:
    return EvalRun.objects.create(project=project, name="run", status=status)


def _evaluator(project, name="quality") -> Evaluator:
    return Evaluator.objects.create(
        project=project, name=name, kind="llm_judge", scope="sample", version=1
    )


def _attach(run, evaluator, enabled=True) -> RunEvaluator:
    return RunEvaluator.objects.create(
        run=run, evaluator=evaluator, snapshot={"name": evaluator.name}, enabled=enabled
    )


def _score(run, sample, run_eval, name="quality", value=0.8, passed=True) -> Score:
    return Score.objects.create(
        project=run.project,
        run=run,
        variant=sample.variant,
        sample=sample,
        run_evaluator=run_eval,
        name=name,
        value=value,
        passed=passed,
    )


def test_progress_pending_run_without_samples():
    run = _run(_project(), status=EvalRun.Status.PENDING)
    progress = compute_run_progress(run)
    assert progress["phase"] == "pending"
    assert progress["total"] == 0
    assert progress["prepared"] == 0
    assert progress["scored"] == 0
    assert progress["score_total"] == 0
    assert progress["variants"] == []
    assert progress["evaluator_stats"] == []


def test_progress_generating_with_per_variant_breakdown():
    project = _project()
    run = _run(project)
    v1 = EvalVariant.objects.create(run=run, label="model-a", order=0)
    v2 = EvalVariant.objects.create(run=run, label="model-b", order=1)

    EvalSample.objects.create(run=run, variant=v1, trajectory={"final_output": "hi"})
    EvalSample.objects.create(run=run, variant=v1)
    # An errored sample counts as prepared.
    EvalSample.objects.create(run=run, variant=v2, error="boom")
    EvalSample.objects.create(run=run, variant=v2)

    progress = compute_run_progress(run)
    assert progress["phase"] == "generating"
    assert progress["total"] == 4
    assert progress["prepared"] == 2
    assert progress["errors"] == 1

    by_label = {v["label"]: v for v in progress["variants"]}
    assert by_label["model-a"] == {
        "id": str(v1.id),
        "label": "model-a",
        "total": 2,
        "prepared": 1,
        "errors": 0,
    }
    assert by_label["model-b"] == {
        "id": str(v2.id),
        "label": "model-b",
        "total": 2,
        "prepared": 1,
        "errors": 1,
    }


def test_progress_scoring_counts_sample_evaluator_pairs():
    project = _project()
    run = _run(project)
    variant = EvalVariant.objects.create(run=run, label="model-a")
    re1 = _attach(run, _evaluator(project, "quality"))
    re2 = _attach(run, _evaluator(project, "accuracy"))
    _attach(run, _evaluator(project, "disabled"), enabled=False)

    samples = [
        EvalSample.objects.create(run=run, variant=variant, trajectory={"final_output": "x"})
        for _ in range(3)
    ]

    _score(run, samples[0], re1, name="quality", value=0.9, passed=True)
    _score(run, samples[0], re2, name="accuracy", value=0.5, passed=False)
    _score(run, samples[1], re1, name="quality", value=0.7, passed=True)

    progress = compute_run_progress(run)
    assert progress["phase"] == "scoring"
    assert progress["prepared"] == 3
    assert progress["total"] == 3
    # 3 samples x 2 enabled evaluators; disabled evaluators are excluded.
    assert progress["score_total"] == 6
    assert progress["scored"] == 3


def test_progress_evaluator_stats_rolling_aggregates():
    project = _project()
    run = _run(project)
    variant = EvalVariant.objects.create(run=run, label="model-a")
    run_eval = _attach(run, _evaluator(project, "quality"))
    s1 = EvalSample.objects.create(run=run, variant=variant, trajectory={"a": 1})
    s2 = EvalSample.objects.create(run=run, variant=variant, trajectory={"a": 1})

    _score(run, s1, run_eval, name="quality", value=1.0, passed=True)
    _score(run, s2, run_eval, name="quality", value=0.5, passed=False)
    # __prediction bookkeeping rows are excluded from stats.
    _score(run, s1, run_eval, name="quality__prediction", value=None, passed=None)

    stats = compute_run_progress(run)["evaluator_stats"]
    assert stats == [
        {"name": "quality", "scored": 2, "mean": 0.75, "pass_rate": 0.5},
    ]


def test_progress_aggregating_when_all_pairs_scored():
    project = _project()
    run = _run(project)
    variant = EvalVariant.objects.create(run=run, label="model-a")
    run_eval = _attach(run, _evaluator(project))
    sample = EvalSample.objects.create(run=run, variant=variant, trajectory={"a": 1})
    _score(run, sample, run_eval)

    progress = compute_run_progress(run)
    assert progress["phase"] == "aggregating"
    assert progress["scored"] == progress["score_total"] == 1


def test_progress_phase_maps_terminal_statuses():
    project = _project()
    for status in (EvalRun.Status.COMPLETED, EvalRun.Status.FAILED, EvalRun.Status.CANCELLED):
        run = _run(project, status=status)
        assert compute_run_progress(run)["phase"] == status


def test_run_list_serializer_progress_only_for_live_runs():
    project = _project()
    live = _run(project, status=EvalRun.Status.RUNNING)
    variant = EvalVariant.objects.create(run=live, label="model-a")
    EvalSample.objects.create(run=live, variant=variant, trajectory={"a": 1})
    done = _run(project, status=EvalRun.Status.COMPLETED)

    live_data = EvalRunListSerializer(live).data
    assert live_data["progress"]["phase"] == "scoring"
    assert live_data["progress"]["prepared"] == 1

    done_data = EvalRunListSerializer(done).data
    assert done_data["progress"] is None


def test_sample_list_serializer_live_feed_fields():
    project = _project()
    run = _run(project)
    variant = EvalVariant.objects.create(run=run, label="model-a")
    prepared = EvalSample.objects.create(
        run=run,
        variant=variant,
        trajectory={"final_output": "x" * 500, "messages": []},
    )
    errored = EvalSample.objects.create(run=run, variant=variant, error="rate limited")
    untouched = EvalSample.objects.create(run=run, variant=variant)

    prepared_data = EvalSampleListSerializer(prepared).data
    assert prepared_data["is_prepared"] is True
    assert prepared_data["variant_label"] == "model-a"
    assert prepared_data["output_preview"] == "x" * 240

    errored_data = EvalSampleListSerializer(errored).data
    assert errored_data["is_prepared"] is True
    assert errored_data["output_preview"] == ""
    assert errored_data["error"] == "rate limited"

    untouched_data = EvalSampleListSerializer(untouched).data
    assert untouched_data["is_prepared"] is False
    assert untouched_data["output_preview"] == ""


def test_sample_output_preview_falls_back_to_last_assistant_message():
    project = _project()
    run = _run(project)
    variant = EvalVariant.objects.create(run=run, label="model-a")
    sample = EvalSample.objects.create(
        run=run,
        variant=variant,
        trajectory={
            "messages": [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": "the answer"},
            ]
        },
    )
    assert EvalSampleListSerializer(sample).data["output_preview"] == "the answer"


def test_sample_expected_preview_clips_string_and_json():
    project = _project()
    run = _run(project)
    variant = EvalVariant.objects.create(run=run, label="model-a")
    gold = EvalSample.objects.create(run=run, variant=variant, expected="Paris" + "!" * 300)
    structured = EvalSample.objects.create(run=run, variant=variant, expected={"label": "positive"})
    empty = EvalSample.objects.create(run=run, variant=variant, expected=None)

    gold_preview = EvalSampleListSerializer(gold).data["expected_preview"]
    assert gold_preview == ("Paris" + "!" * 300)[:240]
    assert EvalSampleListSerializer(structured).data["expected_preview"] == '{"label": "positive"}'
    assert EvalSampleListSerializer(empty).data["expected_preview"] == ""
