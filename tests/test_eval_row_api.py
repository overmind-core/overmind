import uuid

import pytest
from conftest import EVAL_ROWS, frozen_dataset
from rest_framework.test import APIClient

from overbae.models import EvalRun, EvalRunRow, Project, ProjectMembership, User

pytestmark = pytest.mark.django_db


def test_run_rows_include_paired_input_output_and_reference():
    user = User.objects.create_user(email=f"eval-rows-{uuid.uuid4()}@test.com")
    project = Project.objects.create(name="Eval rows", slug=f"eval-rows-{uuid.uuid4()}")
    ProjectMembership.objects.create(user=user, project=project)
    dataset = frozen_dataset(project, EVAL_ROWS, contract="eval")
    run = EvalRun.objects.create(
        project=project,
        dataset=dataset,
        cell=dataset.active_cell,
        name="Paired rows",
        mode="generate",
        status="completed",
    )
    first = EvalRunRow.objects.create(run=run, row_index=0, output="answer 1", state="done")
    EvalRunRow.objects.create(run=run, row_index=1, output="answer 2", state="done")
    client = APIClient()
    client.force_authenticate(user)

    response = client.get(
        "/api/eval-run-rows/", {"run": str(run.id), "ordering": "row_index", "page_size": 1}
    )
    assert response.status_code == 200, response.data
    assert response.data["count"] == 2
    assert response.data["results"][0]["input"] == "q1"
    assert response.data["results"][0]["output"] == "answer 1"
    assert response.data["results"][0]["reference"] == "a1"

    second = client.get(
        "/api/eval-run-rows/",
        {"run": str(run.id), "ordering": "row_index", "page_size": 1, "page": 2},
    )
    assert second.status_code == 200, second.data
    assert second.data["results"][0]["input"] == "q2"
    assert second.data["results"][0]["output"] == "answer 2"
    assert second.data["results"][0]["reference"] == "a2"

    detail = client.get(f"/api/eval-run-rows/{first.id}/")
    assert detail.status_code == 200, detail.data
    assert detail.data["input"] == "q1"
    assert detail.data["output"] == "answer 1"


def test_historical_run_without_rows_returns_no_paired_results():
    user = User.objects.create_user(email=f"eval-legacy-{uuid.uuid4()}@test.com")
    project = Project.objects.create(name="Legacy", slug=f"eval-legacy-{uuid.uuid4()}")
    ProjectMembership.objects.create(user=user, project=project)
    run = EvalRun.objects.create(project=project, name="Legacy", status="completed")
    client = APIClient()
    client.force_authenticate(user)

    response = client.get("/api/eval-run-rows/", {"run": str(run.id)})
    assert response.status_code == 200, response.data
    assert response.data["count"] == 0
    assert response.data["results"] == []


def test_run_row_without_dataset_cell_has_no_input_or_reference():
    user = User.objects.create_user(email=f"eval-no-cell-{uuid.uuid4()}@test.com")
    project = Project.objects.create(name="No cell", slug=f"eval-no-cell-{uuid.uuid4()}")
    ProjectMembership.objects.create(user=user, project=project)
    run = EvalRun.objects.create(project=project, name="No cell", status="completed")
    row = EvalRunRow.objects.create(run=run, row_index=0, output="answer", state="done")
    client = APIClient()
    client.force_authenticate(user)

    response = client.get("/api/eval-run-rows/", {"run": str(run.id)})
    assert response.status_code == 200, response.data
    assert response.data["results"][0]["input"] is None
    assert response.data["results"][0]["output"] == "answer"
    assert response.data["results"][0]["reference"] is None
    detail = client.get(f"/api/eval-run-rows/{row.id}/")
    assert detail.status_code == 200, detail.data
    assert detail.data["input"] is None


def test_historical_display_index_does_not_invent_a_dataset_reference():
    user = User.objects.create_user(email=f"eval-index-{uuid.uuid4()}@test.com")
    project = Project.objects.create(name="Historical", slug=f"eval-index-{uuid.uuid4()}")
    ProjectMembership.objects.create(user=user, project=project)
    dataset = frozen_dataset(project, EVAL_ROWS, contract="eval")
    run = EvalRun.objects.create(
        project=project,
        dataset=dataset,
        cell=dataset.active_cell,
        mode="generate",
        status="completed",
    )
    EvalRunRow.objects.create(
        run=run,
        row_index=0,
        output="uncaptured input",
        metadata={"legacy_sample": {"row_index": None}},
    )
    EvalRunRow.objects.create(
        run=run, row_index=1, output="first input", metadata={"legacy_sample": {"row_index": 0}}
    )
    client = APIClient()
    client.force_authenticate(user)
    response = client.get("/api/eval-run-rows/", {"run": str(run.id), "ordering": "row_index"})
    assert response.status_code == 200, response.data
    first, second = response.data["results"]
    assert first["input"] is None
    assert first["reference"] is None
    assert second["input"] == "q1"
    assert second["reference"] == "a1"
