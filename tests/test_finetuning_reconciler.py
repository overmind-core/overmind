"""A ``deploying`` job has finished remote training: rescuing it with run_finetuning
would queue a duplicate register_finetuned_model on every beat."""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from django.utils import timezone

from overbae.models.finetuning import FinetuningJob
from overbae.tasks.finetuning_reconciler import _reconcile

pytestmark = pytest.mark.django_db


def _job(status: str) -> FinetuningJob:
    from overbae.models import Project

    project = Project.objects.create(name=f"reconciler-test-{uuid.uuid4().hex[:6]}")
    dataset = frozen_dataset(project, TRAIN_ROWS, name="reconciler-test-ds", contract="train")
    return FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="Qwen/Qwen3-8B",
        status=status,
        remote_job_id="proj:job",
    )


def _run_reconcile(*, active_tasks: list[dict]):
    sent: list[tuple[str, dict]] = []
    app = MagicMock()
    app.control.inspect.return_value.active.return_value = {"w1": active_tasks}
    app.control.inspect.return_value.reserved.return_value = {}
    app.control.inspect.return_value.scheduled.return_value = {}

    def _send(name, kwargs=None):
        sent.append((name, kwargs or {}))
        return MagicMock(id=str(uuid.uuid4()))

    app.send_task.side_effect = _send
    with (
        patch("overbae.celery.get_celery_app", return_value=app),
        patch("overbae.tasks.finetuning.observe_finetuning_job"),
    ):
        result = _reconcile()
    return result, sent


def test_deploying_job_rescued_with_register_task():
    job = _job("deploying")
    _, sent = _run_reconcile(active_tasks=[])
    mine = [(n, k) for n, k in sent if k.get("job_id") == str(job.id)]
    assert mine == []


def test_lost_submission_task_is_reconciled_without_another_gpu_dispatch():
    job = _job("preparing")
    job.remote_job_id = ""
    job.celery_task_id = "lost-task"
    job.provider_submission = {
        "state": "submitting",
        "run_id": f"ft-{job.id}-saved",
        "intent_at": (timezone.now() - timedelta(hours=1)).isoformat(),
    }
    job.save()
    FinetuningJob.objects.filter(pk=job.pk).update(updated_at=timezone.now() - timedelta(hours=1))
    with patch("overbae.services.training_submission.recover") as recover:
        _, sent = _run_reconcile(active_tasks=[])
    job.refresh_from_db()
    assert job.status == "submission_unknown"
    recover.assert_called_once()
    assert sent == []


def test_lost_cpu_preparation_task_reuses_existing_job():
    job = _job("preparing")
    job.remote_job_id, job.celery_task_id = "", "lost-task"
    job.save()
    FinetuningJob.objects.filter(pk=job.pk).update(updated_at=timezone.now() - timedelta(hours=1))
    _, sent = _run_reconcile(active_tasks=[])
    assert sent == [("overbae.tasks.finetuning.run_finetuning", {"job_id": str(job.id)})]


@pytest.mark.parametrize("status", ["preparing", "submission_unknown"])
def test_lost_modal_staging_requeues_same_job_before_gpu_dispatch(status):
    job = _job(status)
    job.provider = "modal"
    job.remote_job_id, job.celery_task_id = "", "lost-task"
    job.provider_submission = {
        "state": "submitting" if status == "preparing" else "unknown",
        "intent_at": (timezone.now() - timedelta(hours=1)).isoformat(),
    }
    job.save()
    FinetuningJob.objects.filter(pk=job.pk).update(updated_at=timezone.now() - timedelta(hours=1))
    _, sent = _run_reconcile(active_tasks=[])
    job.refresh_from_db()
    assert sent == [("overbae.tasks.finetuning.run_finetuning", {"job_id": str(job.id)})]
    assert job.status == "queued"
    assert not job.provider_submission
    assert job.progress["submission_recoveries"][-1]["task_id"] == "lost-task"


def test_active_modal_staging_is_not_requeued():
    job = _job("submission_unknown")
    job.provider = "modal"
    job.remote_job_id, job.celery_task_id = "", "live-task"
    job.provider_submission = {"state": "unknown"}
    job.save()
    _, sent = _run_reconcile(active_tasks=[{"id": "live-task"}])
    assert sent == []
