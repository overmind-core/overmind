"""Celery observes Modal train; it never babysits the GPU lease.

A live FunctionCall past 4h must stay ``running``. The 4h poll loop used to mark
those jobs ``failed`` while train.py kept going.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from overbae.models.finetuning import FinetuningJob
from overbae.services.finetuning_runner import PollSnapshot
from overbae.tasks.finetuning_reconciler import _RUN_TASK, _reconcile

pytestmark = pytest.mark.django_db


def test_inflight_function_call_beats_stale_volume_failed():
    """Modal retries rewrite meta.json to failed between attempts."""
    from overbae.services.finetuning_runner import resolve_modal_job_state

    assert resolve_modal_job_state("failed", in_flight=True, call_ok=False) == "running"
    assert resolve_modal_job_state("cancelled", in_flight=True, call_ok=False) == "cancelled"
    assert resolve_modal_job_state("running", in_flight=False, call_ok=True) == "succeeded"
    # Finished train, FunctionCall expired / from_id blip: still deploy.
    assert resolve_modal_job_state("succeeded", in_flight=False, call_ok=False) == "succeeded"
    assert (
        resolve_modal_job_state("failed", in_flight=False, call_ok=False, has_final=True)
        == "succeeded"
    )
    # Call error with no checkpoint: stay running (stall/poll-error budget fails it).
    assert resolve_modal_job_state("failed", in_flight=False, call_ok=False) == "running"
    assert resolve_modal_job_state("running", in_flight=False, call_ok=False) == "running"


def _job(**kwargs) -> FinetuningJob:
    from overbae.models import Dataset, Project

    project = Project.objects.create(name=f"observe-{uuid.uuid4().hex[:6]}")
    dataset = Dataset.objects.create(project=project, name="observe-ds", intent="train")
    defaults = {
        "project": project,
        "dataset": dataset,
        "base_model": "Qwen/Qwen3-8B",
        "status": FinetuningJob.Status.RUNNING,
        "provider": FinetuningJob.Provider.MODAL,
        "remote_job_id": "ft-abc:fc-123",
        "started_at": timezone.now() - timedelta(hours=5),
    }
    defaults.update(kwargs)
    return FinetuningJob.objects.create(**defaults)


def _runner(*, state: str = "running", error: str = "") -> MagicMock:
    runner = MagicMock()
    snap = PollSnapshot(state=state, error=error)
    runner.poll.return_value = snap
    runner.is_terminal_ok.side_effect = lambda s: s in {"succeeded", "completed"}
    runner.is_terminal_fail.side_effect = lambda s: s in {"failed", "error"}
    runner.is_terminal_cancelled.side_effect = lambda s: s in {"cancelled", "canceled"}
    runner.fetch_epoch_losses.return_value = []
    return runner


def _run_reconcile(*, runner, active_tasks: list[dict] | None = None):
    sent: list[tuple[str, dict]] = []
    app = MagicMock()
    app.control.inspect.return_value.active.return_value = {"w1": active_tasks or []}
    app.control.inspect.return_value.reserved.return_value = {}
    app.control.inspect.return_value.scheduled.return_value = {}

    def _send(name, kwargs=None):
        sent.append((name, kwargs or {}))
        return MagicMock(id=str(uuid.uuid4()))

    app.send_task.side_effect = _send
    with (
        patch("overbae.celery.get_celery_app", return_value=app),
        patch("overbae.services.finetuning_runner.get_runner", return_value=runner),
        patch("overbae.tasks.model_deployment.register_finetuned_model.delay"),
    ):
        result = _reconcile()
    return result, sent, runner


def test_running_job_past_four_hours_stays_running_while_remote_alive():
    job = _job()
    runner = _runner(state="running")
    _, sent, runner = _run_reconcile(runner=runner)

    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.RUNNING
    assert job.error_message == ""
    assert runner.poll.call_count == 1
    assert all(n != _RUN_TASK for n, _ in sent)


def test_running_job_is_observed_not_requeued():
    job = _job(started_at=timezone.now())
    runner = _runner(state="running")
    _, sent, _ = _run_reconcile(runner=runner)
    assert all(k.get("job_id") != str(job.id) for _, k in sent)
    runner.poll.assert_called_once_with(job.remote_job_id)


def test_succeeded_poll_finalizes_once():
    job = _job(started_at=timezone.now())
    runner = _runner(state="succeeded")
    _run_reconcile(runner=runner)
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.DEPLOYING
    _run_reconcile(runner=runner)
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.DEPLOYING
    assert job.events.filter(message="Fine-tuning completed — deploying model").count() == 1


def test_queued_without_remote_still_enqueues_submit():
    job = _job(status=FinetuningJob.Status.QUEUED, remote_job_id="", started_at=None)
    runner = _runner()
    _, sent, _ = _run_reconcile(runner=runner)
    mine = [(n, k) for n, k in sent if k.get("job_id") == str(job.id)]
    assert mine == [(_RUN_TASK, {"job_id": str(job.id)})]
    runner.poll.assert_not_called()


def test_preparing_with_remote_id_is_observed():
    job = _job(status=FinetuningJob.Status.PREPARING, started_at=None)
    runner = _runner(state="running")
    _, sent, runner = _run_reconcile(runner=runner)
    assert all(n != _RUN_TASK for n, _ in sent)
    runner.poll.assert_called_once()
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.RUNNING


def test_run_finetuning_does_not_poll_after_submit():
    from overbae.tasks.finetuning import run_finetuning

    job = _job()
    runner = _runner(state="running")
    runner.poll.side_effect = AssertionError("submit task must not poll")
    with patch("overbae.services.finetuning_runner.get_runner", return_value=runner):
        result = run_finetuning(job_id=str(job.id))
    assert result["status"] == "running"
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.RUNNING


def test_queued_with_remote_is_observed_not_resubmitted():
    job = _job(status=FinetuningJob.Status.QUEUED)
    runner = _runner(state="running")
    _, sent, runner = _run_reconcile(runner=runner)
    assert all(n != _RUN_TASK for n, _ in sent)
    runner.poll.assert_called_once_with(job.remote_job_id)
    runner.submit.assert_not_called()


def test_preparing_without_remote_is_not_double_submitted():
    job = _job(
        status=FinetuningJob.Status.PREPARING,
        remote_job_id="",
        celery_task_id="submit-in-flight",
        started_at=None,
    )
    runner = _runner()
    _, sent, runner = _run_reconcile(runner=runner, active_tasks=[])
    assert all(k.get("job_id") != str(job.id) for _, k in sent)
    runner.poll.assert_not_called()


def test_failed_poll_with_weights_deploys():
    job = _job()
    runner = _runner(state="failed", error="OutputExpired")
    runner.poll.return_value = PollSnapshot(
        state="failed",
        output_model_name="modal/ft-abc/final",
        weights_url="/data/runs/ft-abc/final",
        error="OutputExpired",
    )
    _run_reconcile(runner=runner)
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.DEPLOYING
    assert job.output_model_name == "modal/ft-abc/final"
    runner.cancel.assert_not_called()


def test_failed_poll_without_weights_cancels_remote():
    job = _job()
    runner = _runner(state="failed", error="train.py exited 1")
    _run_reconcile(runner=runner)
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.FAILED
    runner.cancel.assert_called_once_with(job.remote_job_id)


def test_stalled_progress_fails_and_cancels():
    job = _job()
    job.progress = {
        "trained_steps": 100,
        "latest_train_loss": 1.2,
        "checkpoints": [],
        "metrics": {"loss": [], "learning_rate": [], "grad_norm": []},
        "observe": {
            "last_move_at": (timezone.now() - timedelta(minutes=31)).isoformat(),
            "saw_step": True,
        },
    }
    job.save(update_fields=["progress"])
    runner = _runner(state="running")
    runner.poll.return_value = PollSnapshot(
        state="running", step=100, trained_steps=100, train_loss=1.2
    )
    _run_reconcile(runner=runner)
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.FAILED
    runner.cancel.assert_called_once_with(job.remote_job_id)


def test_poll_errors_fail_after_budget():
    job = _job()
    job.progress = {"observe": {"poll_errors": 19}}
    job.save(update_fields=["progress"])
    runner = _runner()
    runner.poll.side_effect = RuntimeError("revoked key")
    _run_reconcile(runner=runner)
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.FAILED
    runner.cancel.assert_called_once_with(job.remote_job_id)
