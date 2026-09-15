"""Celery dispatch is stubbed: only the API contract and DB state are asserted."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from conftest import frozen_dataset
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import (
    Capability,
    Dataset,
    FinetuningJob,
    FinetuningJobEvent,
    Project,
    ProjectMembership,
    User,
)

pytestmark = pytest.mark.django_db

CELERY_PATH = "overbae.tasks.finetuning.run_finetuning.apply_async"


def _user(email: str) -> User:
    return User.objects.create_user(
        email=email,
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        projects_limit=5,
    )


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def _project(name: str = "P") -> Project:
    return Project.objects.create(name=name, slug=f"p-{uuid.uuid4().hex[:8]}")


def _capability(project: Project) -> Capability:
    slug = f"a-{uuid.uuid4().hex[:8]}"
    return Capability.objects.create(project=project, name=slug, slug=slug)


def _dataset(capability: Capability, *, n_points: int = 2) -> Dataset:
    rows = [
        {
            "input": {
                "messages": [
                    {"role": "user", "content": f"in-{i}"},
                    {"role": "assistant", "content": f"out-{i}"},
                ]
            }
        }
        for i in range(n_points)
    ]
    return frozen_dataset(capability.project, rows, capability=capability)


def _job_payload(*, project_id: str, dataset_id: str, **overrides) -> dict:
    payload = {
        "project": project_id,
        "dataset": dataset_id,
        "name": "ft-test",
        "use_case": "test run",
        "base_model": "meta-llama/Llama-3.2-3B-Instruct",
        "hyperparameters": {"learning_rate": 3e-4, "epochs": 2},
    }
    payload.update(overrides)
    return payload


def _setup_project_with_dataset() -> tuple[User, Project, Capability, Dataset]:
    u = _user(f"u-{uuid.uuid4().hex[:6]}@example.com")
    p = _project()
    ProjectMembership.objects.create(user=u, project=p)
    a = _capability(p)
    ds = _dataset(a)
    return u, p, a, ds


class _FakeAsyncResult:
    def __init__(self, task_id: str = "task-id-1"):
        self.id = task_id


def test_unauthenticated_requests_rejected():
    r = APIClient().get(reverse("finetuningjob-list"))
    assert r.status_code == 401


def test_list_only_shows_jobs_in_user_projects():
    u_a, p_a, _, ds_a = _setup_project_with_dataset()
    _u_b, p_b, _, ds_b = _setup_project_with_dataset()

    FinetuningJob.objects.create(project=p_a, dataset=ds_a, base_model="m")
    FinetuningJob.objects.create(project=p_b, dataset=ds_b, base_model="m")

    r = _auth_client(u_a).get(reverse("finetuningjob-list"))
    assert r.status_code == 200
    projects = {str(row["project"]) for row in r.data["results"]}
    assert projects == {str(p_a.id)}


def test_create_dispatches_celery_and_persists_job():
    u, p, _, ds = _setup_project_with_dataset()

    with patch(CELERY_PATH, return_value=_FakeAsyncResult("celery-xyz")) as mock_apply:
        r = _auth_client(u).post(
            reverse("finetuningjob-list"),
            _job_payload(project_id=str(p.id), dataset_id=str(ds.id)),
            format="json",
        )

    assert r.status_code == 201, r.data
    job = FinetuningJob.objects.get(pk=r.data["id"])
    assert job.status == FinetuningJob.Status.QUEUED
    assert job.triggered_by_id == u.id
    assert job.celery_task_id == "celery-xyz"
    assert job.base_model == "meta-llama/Llama-3.2-3B-Instruct"
    assert job.hyperparameters == {"learning_rate": 3e-4, "epochs": 2}

    mock_apply.assert_called_once()
    kwargs = mock_apply.call_args.kwargs
    assert kwargs["kwargs"] == {"job_id": str(job.id)}


def test_create_rejects_dataset_from_other_project():
    u, p, _, _ = _setup_project_with_dataset()
    foreign_project = _project("foreign")
    foreign_capability = _capability(foreign_project)
    foreign_ds = _dataset(foreign_capability)

    with patch(CELERY_PATH) as mock_apply:
        r = _auth_client(u).post(
            reverse("finetuningjob-list"),
            _job_payload(project_id=str(p.id), dataset_id=str(foreign_ds.id)),
            format="json",
        )

    assert r.status_code == 400
    assert "dataset" in r.data
    mock_apply.assert_not_called()


def test_create_rejects_when_user_not_project_member():
    u_member, p, _, ds = _setup_project_with_dataset()
    outsider = _user("outsider@example.com")

    with patch(CELERY_PATH) as mock_apply:
        r = _auth_client(outsider).post(
            reverse("finetuningjob-list"),
            _job_payload(project_id=str(p.id), dataset_id=str(ds.id)),
            format="json",
        )

    assert r.status_code == 400
    assert "project" in r.data
    mock_apply.assert_not_called()
    list_r = _auth_client(outsider).get(reverse("finetuningjob-list"))
    assert list_r.status_code == 200
    assert list_r.data["count"] == 0
    _ = u_member  # silence linter; only used to seed membership


def test_create_rejects_capability_from_other_project():
    u, p, _, ds = _setup_project_with_dataset()
    other_project = _project("other")
    other_capability = _capability(other_project)

    with patch(CELERY_PATH) as mock_apply:
        r = _auth_client(u).post(
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


def test_retrieve_includes_events_inline():
    u, p, _, ds = _setup_project_with_dataset()
    job = FinetuningJob.objects.create(project=p, dataset=ds, base_model="m")
    FinetuningJobEvent.objects.create(job=job, event_type="status_change", message="→ queued")
    FinetuningJobEvent.objects.create(job=job, event_type="log", message="prepared")

    r = _auth_client(u).get(reverse("finetuningjob-detail", kwargs={"id": job.id}))
    assert r.status_code == 200
    assert len(r.data["events"]) == 2


def test_retrieve_404_for_foreign_job():
    u, _, _, _ = _setup_project_with_dataset()
    _, p2, _, ds2 = _setup_project_with_dataset()
    foreign = FinetuningJob.objects.create(project=p2, dataset=ds2, base_model="m")

    r = _auth_client(u).get(reverse("finetuningjob-detail", kwargs={"id": foreign.id}))
    assert r.status_code == 404


def test_cancel_transitions_non_terminal_job():
    u, p, _, ds = _setup_project_with_dataset()
    job = FinetuningJob.objects.create(
        project=p, dataset=ds, base_model="m", status=FinetuningJob.Status.RUNNING
    )

    r = _auth_client(u).post(reverse("finetuningjob-cancel", kwargs={"id": job.id}))
    assert r.status_code == 200
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.CANCELLED


def test_cancel_is_noop_for_terminal_job():
    u, p, _, ds = _setup_project_with_dataset()
    job = FinetuningJob.objects.create(
        project=p, dataset=ds, base_model="m", status=FinetuningJob.Status.SUCCEEDED
    )

    r = _auth_client(u).post(reverse("finetuningjob-cancel", kwargs={"id": job.id}))
    assert r.status_code == 200
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.SUCCEEDED


def test_retry_dispatches_celery_for_failed_job():
    u, p, _, ds = _setup_project_with_dataset()
    job = FinetuningJob.objects.create(
        project=p,
        dataset=ds,
        base_model="m",
        status=FinetuningJob.Status.FAILED,
        error_message="boom",
    )

    with patch(CELERY_PATH, return_value=_FakeAsyncResult("retry-task")) as mock_apply:
        r = _auth_client(u).post(reverse("finetuningjob-retry", kwargs={"id": job.id}))

    assert r.status_code == 200
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.QUEUED
    assert job.error_message == ""
    assert job.celery_task_id == "retry-task"
    mock_apply.assert_called_once_with(kwargs={"job_id": str(job.id)})


def test_retry_rejects_non_failed_job():
    u, p, _, ds = _setup_project_with_dataset()
    job = FinetuningJob.objects.create(
        project=p, dataset=ds, base_model="m", status=FinetuningJob.Status.RUNNING
    )

    with patch(CELERY_PATH) as mock_apply:
        r = _auth_client(u).post(reverse("finetuningjob-retry", kwargs={"id": job.id}))

    assert r.status_code == 400
    mock_apply.assert_not_called()


def test_events_endpoint_returns_events_for_job():
    u, p, _, ds = _setup_project_with_dataset()
    job = FinetuningJob.objects.create(project=p, dataset=ds, base_model="m")
    FinetuningJobEvent.objects.create(job=job, event_type="log", message="one")
    FinetuningJobEvent.objects.create(job=job, event_type="error", message="two")

    r = _auth_client(u).get(reverse("finetuningjob-events", kwargs={"id": job.id}))
    assert r.status_code == 200
    assert {row["message"] for row in r.data} == {"one", "two"}


def test_list_filters_by_status():
    u, p, _, ds = _setup_project_with_dataset()
    FinetuningJob.objects.create(
        project=p, dataset=ds, base_model="m", status=FinetuningJob.Status.QUEUED
    )
    FinetuningJob.objects.create(
        project=p, dataset=ds, base_model="m", status=FinetuningJob.Status.SUCCEEDED
    )

    r = _auth_client(u).get(reverse("finetuningjob-list") + "?status=succeeded")
    assert r.status_code == 200
    statuses = {row["status"] for row in r.data["results"]}
    assert statuses == {"succeeded"}


def test_datasets_list_filters_by_project():
    u, p_mine, capability_mine, _ = _setup_project_with_dataset()
    _dataset(capability_mine)
    _dataset(capability_mine)
    _u_other, _p_other, foreign_capability, _ = _setup_project_with_dataset()
    _dataset(foreign_capability)

    r = _auth_client(u).get(reverse("dataset-list") + f"?project={p_mine.id}")
    assert r.status_code == 200
    assert r.data["count"] == 3
    for row in r.data["results"]:
        assert str(row["capability"]) == str(capability_mine.id)
