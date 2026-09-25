"""A ``deploying`` job has finished remote training: rescuing it with run_finetuning
would queue a duplicate register_finetuned_model on every beat."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from conftest import TRAIN_ROWS, frozen_dataset

from overbae.models.finetuning import FinetuningJob
from overbae.tasks.finetuning_reconciler import _reconcile

pytestmark = pytest.mark.django_db


def _job(status: str) -> FinetuningJob:
    from overbae.models import Project

    project = Project.objects.create(name=f"reconciler-test-{uuid.uuid4().hex[:6]}")
    dataset = frozen_dataset(project, TRAIN_ROWS, name="reconciler-test-ds")
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
