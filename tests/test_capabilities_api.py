"""The capability REST surface: create binds an identity, lists show current
rows, DELETE hides a row without dropping it, and the agent graph endpoint."""

import uuid

import pytest
from conftest import EVAL_ROWS, frozen_dataset
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import (
    Capability,
    DeployedModel,
    FinetuningJob,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.capabilities import identity

pytestmark = [pytest.mark.django_db, pytest.mark.covers("identity")]


def _trained_model(project, capability, *, status="ready"):
    dataset = frozen_dataset(project, EVAL_ROWS, capability=capability, contract="eval")
    job = FinetuningJob.objects.create(
        project=project, capability=capability, dataset=dataset, base_model="Qwen/Qwen3-8B"
    )
    return DeployedModel.objects.create(
        project=project, finetuning_job=job, model_id=f"ft-{job.id}", status=status
    )


@pytest.mark.covers("tenancy")
def test_benchmark_rejects_other_projects_and_evaluation_infrastructure():
    project = _project()
    capability = _capability(project, "Support")
    other = _project()
    foreign = _trained_model(other, _capability(other, "Other"))
    infrastructure = DeployedModel.objects.create(
        project=project, model_id="base--qwen", status="ready"
    )
    client = _client(project)
    for model in (foreign, infrastructure):
        response = client.patch(
            f"/api/capabilities/{capability.id}/", {"benchmark_model": str(model.id)}, format="json"
        )
        assert response.status_code == 400
        assert "benchmark_model" in response.json()


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _capability(project, name, **extra) -> Capability:
    return Capability.objects.create(
        project=project, name=name, slug=name.lower().replace(" ", "-"), **extra
    )


def _client(project) -> APIClient:
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    ProjectMembership.objects.create(user=user, project=project)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def test_create_slugifies_the_name_and_binds_its_identity():
    project = _project()
    client = _client(project)
    res = client.post(
        "/api/capabilities/",
        {"project": str(project.id), "name": "Refund Bot"},
        format="json",
    )
    assert res.status_code == 201, res.content
    capability = Capability.objects.get(id=res.json()["id"])
    assert capability.slug == "refund-bot" and capability.observed
    assert identity.lookup(project.id, "refund-bot") == capability


def test_agent_graph_endpoint_returns_current_rows_without_a_scan_report():
    project = _project()
    current = _capability(project, "Brain")
    _capability(project, "Old Judge", status=Capability.Status.LEFTOVER)
    _capability(project, "Gone", status=Capability.Status.DELETED)
    client = _client(project)
    body = client.get("/api/agent/", {"project": project.id}).json()
    assert [c["id"] for c in body["capabilities"]] == [str(current.id)]
    assert "history" not in body
    assert "last_scan" not in body
    assert "analyzed_sha" not in body and "repo_full_name" not in body
    assert client.get("/api/agent/", {"project": _project().id}).status_code == 404
