"""Celery observes Modal train; it never babysits the GPU lease.

A live FunctionCall past 4h must stay ``running``. The 4h poll loop used to mark
those jobs ``failed`` while train.py kept going.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from factories import reconcile_training

from overbae.models.finetuning import FinetuningJob

pytestmark = pytest.mark.django_db

_RUN_TASK = "overbae.tasks.finetuning.run_finetuning"


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


@pytest.fixture
def remote(sft, fake_modal):
    def arrange(*, meta="running", call="pending", error=None, final=False, steps=2):
        sft.runs["ft-abc"] = {"status": meta, "steps": steps, "final": final}
        return fake_modal.adopt("fc-123", "sft_train", state=call, error=error)

    return arrange


def _polls(fake_modal) -> int:
    return len([name for _, name, _, _ in fake_modal.log if name == "get_progress"])


def _cancelled(fake_modal) -> bool:
    return any(call == "fc-123" for call, _ in fake_modal.cancelled) and bool(
        fake_modal.called("mark_cancelled")
    )


def test_running_job_past_four_hours_stays_running_while_remote_alive(remote, fake_modal):
    job = _job()
    remote()
    sent = reconcile_training()

    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.RUNNING
    assert job.error_message == ""
    assert _polls(fake_modal) == 1
    assert all(n != _RUN_TASK for n, _ in sent)


def test_running_job_is_observed_not_requeued(remote, fake_modal):
    job = _job(started_at=timezone.now())
    remote()
    sent = reconcile_training()
    assert all(k.get("job_id") != str(job.id) for _, k in sent)
    assert _polls(fake_modal) == 1


def test_succeeded_poll_finalizes_once(remote):
    job = _job(started_at=timezone.now())
    remote(meta="succeeded", call="done")
    reconcile_training()
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.DEPLOYING
    reconcile_training()
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.DEPLOYING
    assert job.events.filter(message="Fine-tuning completed — deploying model").count() == 1


def test_queued_without_remote_still_enqueues_submit(remote, fake_modal):
    job = _job(status=FinetuningJob.Status.QUEUED, remote_job_id="", started_at=None)
    sent = reconcile_training()
    mine = [(n, k) for n, k in sent if k.get("job_id") == str(job.id)]
    assert mine == [(_RUN_TASK, {"job_id": str(job.id)})]
    assert _polls(fake_modal) == 0


def test_preparing_with_remote_id_is_observed(remote, fake_modal):
    job = _job(status=FinetuningJob.Status.PREPARING, started_at=None)
    remote()
    sent = reconcile_training()
    assert all(n != _RUN_TASK for n, _ in sent)
    assert _polls(fake_modal) == 1
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.RUNNING


def test_run_finetuning_does_not_poll_after_submit(remote, fake_modal):
    from overbae.tasks.finetuning import run_finetuning

    job = _job()
    remote()
    result = run_finetuning(job_id=str(job.id))
    assert result["status"] == "running"
    assert _polls(fake_modal) == 0
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.RUNNING


def test_queued_with_remote_is_observed_not_resubmitted(remote, fake_modal):
    _job(status=FinetuningJob.Status.QUEUED)
    remote()
    sent = reconcile_training()
    assert all(n != _RUN_TASK for n, _ in sent)
    assert _polls(fake_modal) == 1
    assert fake_modal.spawns() == []


def test_preparing_without_remote_is_not_double_submitted(remote, fake_modal):
    job = _job(
        status=FinetuningJob.Status.PREPARING,
        remote_job_id="",
        celery_task_id="submit-in-flight",
        started_at=None,
    )
    sent = reconcile_training(active_tasks=[])
    assert all(k.get("job_id") != str(job.id) for _, k in sent)
    assert _polls(fake_modal) == 0


def test_a_failed_call_with_final_weights_still_deploys(remote, fake_modal):
    job = _job()
    remote(meta="failed", call="failed", error=RuntimeError("OutputExpired"), final=True)
    reconcile_training()
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.DEPLOYING
    assert job.output_model_name
    assert fake_modal.cancelled == []


def test_a_failed_call_without_weights_fails_and_cancels_the_remote(remote, fake_modal):
    job = _job()
    remote(meta="failed", call="failed", error=RuntimeError("train.py exited 1"))
    reconcile_training()
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.FAILED
    assert _cancelled(fake_modal)


def test_progress_that_stops_moving_for_half_an_hour_fails_and_cancels(remote, fake_modal):
    job = _job()
    remote(steps=5)
    reconcile_training()
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.RUNNING
    job.progress["observe"]["last_move_at"] = (timezone.now() - timedelta(minutes=31)).isoformat()
    job.save(update_fields=["progress"])

    reconcile_training()
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.FAILED
    assert _cancelled(fake_modal)


def test_poll_errors_fail_after_budget(remote, fake_modal):
    job = _job()
    job.progress = {"observe": {"poll_errors": 19}}
    job.save(update_fields=["progress"])
    remote()

    def revoked(run_id):
        raise RuntimeError("revoked key")

    fake_modal.deploy("overmind-sft", "get_progress", revoked)
    reconcile_training()
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.FAILED
    assert _cancelled(fake_modal)


def test_a_deploying_job_is_not_resubmitted(remote):
    job = _job(status=FinetuningJob.Status.DEPLOYING)
    sent = reconcile_training()
    assert [(n, k) for n, k in sent if k.get("job_id") == str(job.id)] == []
