from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from factories import make_project

from overbae.models import (
    EvalRun,
    EvalSample,
    Evaluator,
    EvalVariant,
    RunEvaluator,
    Score,
)
from overbae.tasks import eval as eval_tasks
from overbae.tasks.eval_watchdog import EVAL_RUN_STALL_MINUTES, reap_stalled_eval_runs

pytestmark = pytest.mark.django_db


def _run(project, status=EvalRun.Status.RUNNING) -> EvalRun:
    return EvalRun.objects.create(project=project, name="run", status=status)


def _make_stale(run, minutes: int) -> None:
    """Force ``updated_at`` into the past (bypasses ``auto_now`` via update())."""
    old = timezone.now() - timedelta(minutes=minutes)
    EvalRun.objects.filter(pk=run.pk).update(updated_at=old)


def test_watchdog_marks_stale_running_run_failed():
    run = _run(make_project())
    _make_stale(run, EVAL_RUN_STALL_MINUTES + 5)

    result = reap_stalled_eval_runs()

    run.refresh_from_db()
    assert run.status == EvalRun.Status.FAILED
    assert "stalled" in run.error
    assert run.completed_at is not None
    assert result["reaped"] == 1
    assert result["finalized"] == 0


def test_watchdog_leaves_recent_running_run_alone():
    run = _run(make_project())

    result = reap_stalled_eval_runs()

    run.refresh_from_db()
    assert run.status == EvalRun.Status.RUNNING
    assert run.error == ""
    assert result == {"reaped": 0, "finalized": 0}


def test_watchdog_leaves_run_with_recent_scores_alone():
    project = make_project()
    run = _run(project)
    variant = EvalVariant.objects.create(run=run, label="v")
    evaluator = Evaluator.objects.create(
        project=project, name="q", kind="llm_judge", scope="sample", version=1
    )
    run_eval = RunEvaluator.objects.create(
        run=run, evaluator=evaluator, snapshot={"name": "q"}, enabled=True
    )
    sample = EvalSample.objects.create(run=run, variant=variant, trajectory={"a": 1})
    # Two samples but only one scored => scoring still in progress (not "all present").
    EvalSample.objects.create(run=run, variant=variant, trajectory={"a": 1})
    Score.objects.create(
        project=project, run=run, variant=variant, sample=sample, run_evaluator=run_eval, name="q"
    )
    # updated_at is old, but last_activity also counts the just-created score.
    _make_stale(run, EVAL_RUN_STALL_MINUTES + 5)

    result = reap_stalled_eval_runs()

    run.refresh_from_db()
    assert run.status == EvalRun.Status.RUNNING
    assert result == {"reaped": 0, "finalized": 0}


def test_watchdog_ignores_terminal_runs():
    project = make_project()
    for status in (EvalRun.Status.COMPLETED, EvalRun.Status.FAILED, EvalRun.Status.CANCELLED):
        run = _run(project, status=status)
        _make_stale(run, EVAL_RUN_STALL_MINUTES + 60)

    result = reap_stalled_eval_runs()
    assert result == {"reaped": 0, "finalized": 0}


def test_watchdog_finalizes_when_all_scores_present():
    """All scores written but the callback was lost: finalize, don't fail."""
    project = make_project()
    run = _run(project)
    variant = EvalVariant.objects.create(run=run, label="v", is_baseline=True)
    evaluator = Evaluator.objects.create(
        project=project, name="q", kind="llm_judge", scope="sample", version=1
    )
    run_eval = RunEvaluator.objects.create(
        run=run, evaluator=evaluator, snapshot={"name": "q"}, enabled=True
    )
    sample = EvalSample.objects.create(run=run, variant=variant, trajectory={"a": 1})
    score = Score.objects.create(
        project=project,
        run=run,
        variant=variant,
        sample=sample,
        run_evaluator=run_eval,
        name="q",
        value=1.0,
        passed=True,
    )
    # Make both the row anchor AND the score old so last_activity is stale.
    _make_stale(run, EVAL_RUN_STALL_MINUTES + 5)
    Score.objects.filter(pk=score.pk).update(
        created_at=timezone.now() - timedelta(minutes=EVAL_RUN_STALL_MINUTES + 5)
    )

    result = reap_stalled_eval_runs()

    run.refresh_from_db()
    assert run.status == EvalRun.Status.COMPLETED
    assert result["finalized"] == 1
    assert result["reaped"] == 0
    assert "variants" in run.summary


def test_handle_eval_failure_marks_running_run_failed():
    run = _run(make_project())

    eval_tasks.handle_eval_failure(RuntimeError("boom"), eval_run_id=str(run.id))

    run.refresh_from_db()
    assert run.status == EvalRun.Status.FAILED
    assert "boom" in run.error
    assert run.completed_at is not None


def test_handle_eval_failure_is_idempotent_and_preserves_completed():
    project = make_project()
    completed = _run(project, status=EvalRun.Status.COMPLETED)

    eval_tasks.handle_eval_failure(RuntimeError("late"), eval_run_id=str(completed.id))

    completed.refresh_from_db()
    assert completed.status == EvalRun.Status.COMPLETED
    assert completed.error == ""


def test_a_second_failure_keeps_the_first_error():
    run = _run(make_project())
    eval_tasks.handle_eval_failure(RuntimeError("first"), eval_run_id=str(run.id))
    eval_tasks.handle_eval_failure(RuntimeError("second"), eval_run_id=str(run.id))

    run.refresh_from_db()
    assert run.status == EvalRun.Status.FAILED
    assert "first" in run.error and "second" not in run.error
