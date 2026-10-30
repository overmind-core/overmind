import uuid

import pytest
from conftest import EVAL_ROWS, frozen_dataset
from rest_framework.test import APIClient

from overbae.models import (
    Capability,
    EvalRun,
    NativeEvaluationPlan,
    Project,
    ProjectMembership,
    User,
)

pytestmark = pytest.mark.django_db


def test_activity_pages_filters_and_scopes_both_evaluation_formats():
    user = User.objects.create_user(email=f"activity-{uuid.uuid4()}@test.com")
    project = Project.objects.create(name="Activity", slug="activity")
    ProjectMembership.objects.create(user=user, project=project)
    other = Project.objects.create(name="Other", slug="other")
    capability = Capability.objects.create(project=project, name="Matching", slug="matching")
    dataset = frozen_dataset(project, EVAL_ROWS, contract="eval")
    dataset.capability = capability
    dataset.save(update_fields=["capability"])
    response_run = EvalRun.objects.create(
        project=project, dataset=dataset, name="A responses", status="completed"
    )
    plan = NativeEvaluationPlan.objects.create(
        project=project,
        final_cell=dataset.active_cell,
        name="B probabilities",
        state="prepared",
        config={"participants": [{"key": "base"}, {"key": "trained"}]},
    )
    NativeEvaluationPlan.objects.create(
        project=other, final_cell=dataset.active_cell, name="Hidden", state="completed"
    )
    client = APIClient()
    client.force_authenticate(user)
    url = "/api/eval-runs/activity/"
    params = {"project": str(project.id), "page_size": 1, "ordering": "name"}
    first = client.get(url, params)
    assert first.status_code == 200, first.data
    assert first.data["count"] == 2
    assert first.data["results"][0]["id"] == str(response_run.id)
    assert first.data["results"][0]["kind"] == "generative"
    second = client.get(url, {**params, "page": 2})
    record = second.data["results"][0]
    assert record["id"] == str(plan.id)
    assert record["kind"] == "decision"
    assert record["status"] == "prepared"
    assert record["variant_count"] == 2
    assert record["evaluator_count"] is None
    assert record["capability_id"] == str(capability.id)
    for filters in ({"search": "probabilities"}, {"status": "prepared"}):
        result = client.get(url, {**params, **filters})
        assert result.data["count"] == 1
        assert result.data["results"][0]["id"] == str(plan.id)
    for filters in ({"dataset": str(dataset.id)}, {"capability": str(capability.id)}):
        assert client.get(url, {**params, **filters}).data["count"] == 2
    assert client.get(url, {"project": str(other.id)}).data["count"] == 0
    facets = client.get("/api/eval-runs/activity-datasets/", {"project": str(project.id)})
    assert facets.data == [{"id": str(dataset.id), "name": dataset.name}]
    assert client.get(url, {**params, "status": "fabricated"}).status_code == 400
