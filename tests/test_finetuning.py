"""Celery dispatch is stubbed: only the API contract and DB state are asserted."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from conftest import EVAL_ROWS, frozen_dataset
from django.urls import reverse
from factories import auth_client, make_capability, make_matrix_row, make_project, make_user
from rest_framework.test import APIClient

from overbae.models import (
    Capability,
    Dataset,
    EvalSet,
    FinetuningJob,
    FinetuningJobEvent,
    Project,
    ProjectMembership,
    User,
)

pytestmark = [pytest.mark.django_db, pytest.mark.covers("tenancy")]

CELERY_PATH = "overbae.tasks.finetuning.run_finetuning.apply_async"


def _dataset(capability: Capability, *, n_points: int = 2) -> Dataset:
    rows = [
        {
            "messages": [
                {"role": "user", "content": f"in-{i}"},
                {"role": "assistant", "content": f"out-{i}"},
            ]
        }
        for i in range(n_points)
    ]
    return frozen_dataset(capability.project, rows, capability=capability, contract="train")


def _job_payload(*, project_id: str, dataset_id: str, **overrides) -> dict:
    project = Project.objects.get(pk=project_id)
    evaluation = frozen_dataset(project, EVAL_ROWS, contract="eval")
    eval_set = EvalSet.objects.create(project=project, name="Test evaluations")
    eval_set.rows.add(make_matrix_row(eval_set.project))
    payload = {
        "project": project_id,
        "dataset": dataset_id,
        "eval_dataset": str(evaluation.id),
        "eval_set": str(eval_set.id),
        "name": "ft-test",
        "use_case": "test run",
        "base_model": "meta-llama/Llama-3.2-3B-Instruct",
        "hyperparameters": {"learning_rate": 3e-4, "epochs": 2},
    }
    payload.update(overrides)
    return payload


def _setup_project_with_dataset() -> tuple[User, Project, Capability, Dataset]:
    u = make_user(f"u-{uuid.uuid4().hex[:6]}@example.com")
    p = make_project()
    ProjectMembership.objects.create(user=u, project=p)
    a = make_capability(p)
    ds = _dataset(a)
    return u, p, a, ds


class _FakeAsyncResult:
    def __init__(self, task_id: str = "task-id-1"):
        self.id = task_id


@pytest.mark.covers("authentication")
def test_unauthenticated_requests_rejected():
    r = APIClient().get(reverse("finetuningjob-list"))
    assert r.status_code == 401


def test_create_rejects_dataset_from_other_project():
    u, p, _, _ = _setup_project_with_dataset()
    foreign_project = make_project("foreign")
    foreign_capability = make_capability(foreign_project)
    foreign_ds = _dataset(foreign_capability)

    with patch(CELERY_PATH) as mock_apply:
        r = auth_client(u).post(
            reverse("finetuningjob-list"),
            _job_payload(project_id=str(p.id), dataset_id=str(foreign_ds.id)),
            format="json",
        )

    assert r.status_code == 400
    assert "dataset" in r.data
    mock_apply.assert_not_called()


@pytest.mark.covers("authentication")
def test_create_rejects_when_user_not_project_member():
    u_member, p, _, ds = _setup_project_with_dataset()
    outsider = make_user("outsider@example.com")

    with patch(CELERY_PATH) as mock_apply:
        r = auth_client(outsider).post(
            reverse("finetuningjob-list"),
            _job_payload(project_id=str(p.id), dataset_id=str(ds.id)),
            format="json",
        )

    assert r.status_code == 400
    assert "project" in r.data
    mock_apply.assert_not_called()
    list_r = auth_client(outsider).get(reverse("finetuningjob-list"))
    assert list_r.status_code == 200
    assert list_r.data["count"] == 0
    _ = u_member  # silence linter; only used to seed membership


def test_create_rejects_capability_from_other_project():
    u, p, _, ds = _setup_project_with_dataset()
    other_project = make_project("other")
    other_capability = make_capability(other_project)

    with patch(CELERY_PATH) as mock_apply:
        r = auth_client(u).post(
            reverse("finetuningjob-list"),
            _job_payload(
                project_id=str(p.id),
                dataset_id=str(ds.id),
                capability=str(other_capability.id),
            ),
            format="json",
        )

    assert r.status_code == 400
    assert "capability" in r.data
    mock_apply.assert_not_called()


def test_retrieve_404_for_foreign_job():
    u, _, _, _ = _setup_project_with_dataset()
    _, p2, _, ds2 = _setup_project_with_dataset()
    foreign = FinetuningJob.objects.create(project=p2, dataset=ds2, base_model="m")

    r = auth_client(u).get(reverse("finetuningjob-detail", kwargs={"id": foreign.id}))
    assert r.status_code == 404


@pytest.mark.covers("state-machines")
def test_retry_dispatches_celery_for_failed_job():
    u, p, _, ds = _setup_project_with_dataset()
    job = FinetuningJob.objects.create(
        project=p,
        dataset=ds,
        base_model="m",
        status=FinetuningJob.Status.FAILED,
        error_message="boom",
        remote_job_id="failed-provider-job",
    )

    with patch(CELERY_PATH, return_value=_FakeAsyncResult("retry-task")) as mock_apply:
        r = auth_client(u).post(reverse("finetuningjob-retry", kwargs={"id": job.id}))

    assert r.status_code == 200
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.QUEUED
    assert job.error_message == ""
    assert job.celery_task_id == "retry-task"
    mock_apply.assert_called_once_with(kwargs={"job_id": str(job.id)})


@pytest.mark.covers("state-machines")
def test_retry_rejects_non_failed_job():
    u, p, _, ds = _setup_project_with_dataset()
    job = FinetuningJob.objects.create(
        project=p, dataset=ds, base_model="m", status=FinetuningJob.Status.RUNNING
    )

    with patch(CELERY_PATH) as mock_apply:
        r = auth_client(u).post(reverse("finetuningjob-retry", kwargs={"id": job.id}))

    assert r.status_code == 400
    mock_apply.assert_not_called()


@pytest.mark.covers("http-api")
def test_events_endpoint_returns_events_for_job():
    u, p, _, ds = _setup_project_with_dataset()
    job = FinetuningJob.objects.create(project=p, dataset=ds, base_model="m")
    FinetuningJobEvent.objects.create(job=job, event_type="log", message="one")
    FinetuningJobEvent.objects.create(job=job, event_type="error", message="two")

    r = auth_client(u).get(reverse("finetuningjob-events", kwargs={"id": job.id}))
    assert r.status_code == 200
    assert {row["message"] for row in r.data} == {"one", "two"}
