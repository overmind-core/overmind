import uuid

import pytest
from conftest import EVAL_ROWS, frozen_dataset
from rest_framework.test import APIClient

from overbae.models import EvalRun, EvalRunRow, Project, ProjectMembership, User

pytestmark = [pytest.mark.django_db, pytest.mark.covers("data-logic")]


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
