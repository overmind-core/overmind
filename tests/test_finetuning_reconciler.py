"""A ``deploying`` job has finished remote training: rescuing it with run_finetuning
would queue a duplicate register_finetuned_model on every beat."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from conftest import TRAIN_ROWS, frozen_dataset

from overbae.models.finetuning import FinetuningJob
from overbae.tasks.finetuning_reconciler import (
    _REGISTER_TASK,
    _RUN_TASK,
    _reconcile,
    rescue_task_for,
)

pytestmark = pytest.mark.django_db


def test_rescue_task_routing():
    assert rescue_task_for("deploying") == _REGISTER_TASK
    assert rescue_task_for("queued") == _RUN_TASK
    assert rescue_task_for("running") == _RUN_TASK


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
    assert mine == [(_REGISTER_TASK, {"job_id": str(job.id)})]


def test_deploying_job_with_inflight_register_not_kicked():
    job = _job("deploying")
    inflight = [
        {"id": str(uuid.uuid4()), "name": _REGISTER_TASK, "kwargs": {"job_id": str(job.id)}}
    ]
    _, sent = _run_reconcile(active_tasks=inflight)
    assert all(k.get("job_id") != str(job.id) for _, k in sent)


def test_running_job_is_not_rescued_with_run_finetuning():
    job = _job("running")
    _, sent = _run_reconcile(active_tasks=[])
    assert all(k.get("job_id") != str(job.id) for _, k in sent)
