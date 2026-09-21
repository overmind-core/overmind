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
    Span,
    User,
)
from overbae.services.capabilities import identity

pytestmark = pytest.mark.django_db


def _trained_model(project, capability, *, status="ready"):
    dataset = frozen_dataset(project, EVAL_ROWS, capability=capability)
    job = FinetuningJob.objects.create(
        project=project, capability=capability, dataset=dataset, base_model="Qwen/Qwen3-8B"
    )
    return DeployedModel.objects.create(
        project=project, finetuning_job=job, model_id=f"ft-{job.id}", status=status
    )


def test_benchmark_selection_does_not_change_live_model_and_can_reset_to_codebase():
    project = _project()
    capability = _capability(project, "Support", model="anthropic/claude-opus-4-7")
    live = _trained_model(project, capability)
    benchmark = _trained_model(project, capability)
    capability.active_model = live
    capability.save(update_fields=["active_model"])
    client = _client(project)
    url = f"/api/capabilities/{capability.id}/"

    response = client.patch(url, {"benchmark_model": str(benchmark.id)}, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["benchmark_model"] == str(benchmark.id)
    capability.refresh_from_db()
    assert capability.active_model_id == live.id
    listed = client.get("/api/capabilities/", {"project": project.id}).json()["results"]
    assert listed[0]["benchmark_model"] == str(benchmark.id)

    response = client.patch(url, {"benchmark_model": None}, format="json")
    assert response.status_code == 200, response.content
    capability.refresh_from_db()
    assert capability.benchmark_model_id is None
    assert capability.active_model_id == live.id


@pytest.mark.parametrize("status", ["queued", "deploying", "failed", "deleted", "deleting"])
def test_benchmark_must_be_ready(status):
    project = _project()
    capability = _capability(project, "Support")
    model = _trained_model(project, capability, status=status)
    response = _client(project).patch(
        f"/api/capabilities/{capability.id}/", {"benchmark_model": str(model.id)}, format="json"
    )
    assert response.status_code == 400
    assert "benchmark_model" in response.json()


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


def test_list_shows_current_rows_and_leftover_only_on_request():
    project = _project()
    current = _capability(project, "Brain")
    leftover = _capability(project, "Old Judge", status=Capability.Status.LEFTOVER)
    _capability(project, "Gone", status=Capability.Status.DELETED)
    client = _client(project)

    ids = {
        r["id"] for r in client.get("/api/capabilities/", {"project": project.id}).json()["results"]
    }
    assert ids == {str(current.id)}
    asked = client.get("/api/capabilities/", {"project": project.id, "status": "leftover"}).json()
    assert {r["id"] for r in asked["results"]} == {str(leftover.id)}
    assert client.get(f"/api/capabilities/{leftover.id}/").json()["status"] == "leftover"


def test_delete_hides_the_row_and_keeps_its_data():
    project = _project()
    capability = _capability(project, "Brain")
    span = Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        project=project,
        capability=capability,
        start_time_ns=1,
        end_time_ns=2,
    )
    dataset = frozen_dataset(project, EVAL_ROWS, capability=capability, name="rows")
    client = _client(project)

    assert client.delete(f"/api/capabilities/{capability.id}/").status_code == 204
    capability.refresh_from_db()
    assert capability.status == Capability.Status.DELETED
    assert client.get(f"/api/capabilities/{capability.id}/").status_code == 404
    assert client.get("/api/capabilities/", {"project": project.id}).json()["results"] == []
    assert identity.lookup(project.id, "brain") is None
    span.refresh_from_db()
    dataset.refresh_from_db()
    assert span.capability_id == capability.id and dataset.capability_id == capability.id
    assert client.delete(f"/api/capabilities/{capability.id}/").status_code == 404


def test_deleted_capability_alias_stops_serving():
    project = _project()
    model = DeployedModel.objects.create(
        project=project, model_id="ft-x", status=DeployedModel.Status.READY, base_model_id="m"
    )
    live = _capability(project, "Triage", active_model=model)
    gone = _capability(project, "Old Triage", active_model=model)
    gone.set_status(Capability.Status.DELETED)
    client = _client(project)
    models = client.get("/api/v1/models").json()["data"]
    assert any(m["id"] == f"overmind/{live.id}" for m in models)
    assert not any(m["id"] == f"overmind/{gone.id}" for m in models)


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


def test_list_reads_the_dataset_product_off_the_active_cell():
    project = _project()
    capability = _capability(project, "Brain")
    frozen_dataset(project, EVAL_ROWS, capability=capability, name="rows")
    client = _client(project)

    row = client.get("/api/capabilities/", {"project": project.id}).json()["results"][0]
    assert row["dataset_size"] == len(EVAL_ROWS)
    assert row["dataset_has_expected_output"] is True
