from __future__ import annotations

import uuid

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Dataset, FinetuningJob, Project, ProjectMembership, User

pytestmark = pytest.mark.django_db

RUNS_URL = reverse("finetuningjob-runs")
LIST_URL = reverse("finetuningjob-list")


def _setup() -> tuple[APIClient, Project, Dataset]:
    user = User.objects.create_user(
        email=f"ft-runs-{uuid.uuid4().hex[:8]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    dataset = frozen_dataset(project, TRAIN_ROWS, name="ds", contract="train")
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client, project, dataset


def _job(project, dataset, **kwargs) -> FinetuningJob:
    return FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model=kwargs.pop("base_model", "meta-llama/Llama-3.2-1B-Instruct"),
        status=kwargs.pop("status", FinetuningJob.Status.SUCCEEDED),
        **kwargs,
    )


@pytest.mark.parametrize("endpoint", [LIST_URL, RUNS_URL])
def test_training_history_reads_dataset_versions_once_per_page(endpoint):
    client, project, dataset = _setup()
    cell = dataset.active_cell
    assert cell is not None
    _job(project, dataset, cell=cell)
    with CaptureQueriesContext(connection) as first:
        response = client.get(endpoint, {"project": str(project.id), "page_size": 25})
    assert response.status_code == 200, response.content
    for _ in range(9):
        _job(project, dataset, cell=cell)
    with CaptureQueriesContext(connection) as many:
        response = client.get(endpoint, {"project": str(project.id), "page_size": 25})
    assert response.status_code == 200, response.content
    assert response.json()["count"] == 10
    assert len(many) <= len(first) + 1, (len(first), len(many))


def test_runs_never_reach_another_projects_jobs():
    client, project, dataset = _setup()
    _job(project, dataset, group_id=uuid.uuid4())
    stranger = Project.objects.create(name="X", slug=f"x-{uuid.uuid4().hex[:8]}")
    stranger_dataset = frozen_dataset(stranger, TRAIN_ROWS, name="ds", contract="train")
    _job(stranger, stranger_dataset, group_id=uuid.uuid4())

    assert client.get(RUNS_URL).json()["count"] == 1


def test_facets_never_leak_another_projects_values():
    client, project, dataset = _setup()
    _job(project, dataset, base_model="mine")
    stranger = Project.objects.create(name="X", slug=f"x-{uuid.uuid4().hex[:8]}")
    stranger_dataset = frozen_dataset(stranger, TRAIN_ROWS, name="secret", contract="train")
    _job(stranger, stranger_dataset, base_model="theirs")

    assert client.get(reverse("finetuningjob-base-models")).json() == ["mine"]
    assert [row["name"] for row in client.get(reverse("finetuningjob-datasets")).json()] == ["ds"]


def test_validation_pins_the_requested_cell_and_rejects_another_dataset_cell():
    client, project, dataset = _setup()
    other = frozen_dataset(project, TRAIN_ROWS, contract="train")
    url = reverse("finetuningjob-validate-dataset")
    body = {
        "dataset_id": str(dataset.id),
        "cell_id": str(dataset.active_cell.id),
        "validation_enabled": False,
    }
    response = client.post(url, body, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["stats"]["checkpoint"] == str(dataset.active_cell.id)
    assert (
        client.post(url, {**body, "cell_id": str(other.active_cell.id)}, format="json").status_code
        == 404
    )
