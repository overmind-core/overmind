from __future__ import annotations

import uuid

import pytest
from conftest import frozen_dataset
from django.urls import reverse
from factories import auth_client, make_capability, make_member, make_project, make_user
from rest_framework import status

from overbae.api.filters import NO_CAPABILITY
from overbae.models import (
    Capability,
    Dataset,
    FinetuningJob,
    Project,
)
from overbae.models.inference import DeployedModel, InferenceCall

pytestmark = [pytest.mark.django_db, pytest.mark.covers("state-machines")]


def _dataset(capability: Capability) -> Dataset:
    ds = frozen_dataset(
        capability.project, [{"input": {"q": "test"}}], capability=capability, contract="eval"
    )
    return ds


def _job(project: Project, *, status: str = "succeeded") -> FinetuningJob:
    capability = make_capability(project)
    dataset = _dataset(capability)
    return FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="meta-llama/Meta-Llama-3.1-8B-Instruct-Reference",
        status=status,
        remote_job_id=f"tj-{uuid.uuid4().hex[:8]}",
    )


def _deployed_model(project: Project, job: FinetuningJob | None = None) -> DeployedModel:
    return DeployedModel.objects.create(
        project=project,
        finetuning_job=job,
        model_id=f"ft-llama31-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.READY,
        base_model_id="meta-llama/Meta-Llama-3.1-8B-Instruct-Reference",
    )


def test_inference_client_check_health_returns_false_on_error(scripted):
    from overbae.services.inference_client import InferenceClient

    scripted("https://example.modal.run").reply(503, text="unreachable")
    client = InferenceClient(base_url="https://example.modal.run", api_key="k")
    assert client.check_health() is False


def test_deployed_models_list_filters_to_the_no_capability_bucket():
    u = make_user()
    p = make_project()
    make_member(u, p)
    capability = make_capability(p)
    attributed_job = _job(p)
    FinetuningJob.objects.filter(pk=attributed_job.pk).update(capability=capability)

    attributed = _deployed_model(p, attributed_job)
    job_without_capability = _deployed_model(p, _job(p))
    _deployed_model(p)

    client = auth_client(u)
    bucket = client.get(
        reverse("deployedmodel-list"), {"capability": NO_CAPABILITY, "project": str(p.id)}
    )
    assert bucket.status_code == status.HTTP_200_OK
    assert {str(row["id"]) for row in bucket.data["results"]} == {
        str(job_without_capability.id),
    }
    assert {row["capability_id"] for row in bucket.data["results"]} == {None}

    named = client.get(
        reverse("deployedmodel-list"), {"capability": str(capability.id), "project": str(p.id)}
    )
    assert [str(row["id"]) for row in named.data["results"]] == [str(attributed.id)]
    assert bucket.data["count"] + named.data["count"] == 2


def test_deployed_models_list_rejects_a_malformed_capability_id():
    """The ``NO_CAPABILITY`` sentinel costs the param its ``UUIDFilter``; the 400 is raised by hand."""
    u = make_user()
    p = make_project()
    make_member(u, p)
    _deployed_model(p)

    r = auth_client(u).get(reverse("deployedmodel-list"), {"capability": "not-a-uuid"})
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert NO_CAPABILITY in str(r.data["capability"])


@pytest.mark.covers("tenancy")
def test_deployed_models_retrieve_forbidden_for_other_user():
    u_a = make_user()
    p_a = make_project()
    make_member(u_a, p_a)
    m = _deployed_model(p_a)

    u_b = make_user()  # no membership in p_a
    r = auth_client(u_b).get(reverse("deployedmodel-detail", args=[str(m.id)]))
    assert r.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.covers("identity")
def test_deployed_models_delete_marks_deleted(scripted):
    gateway = scripted("http://inference.test").reply(json_body={"ok": True})
    u = make_user()
    p = make_project()
    make_member(u, p)
    m = _deployed_model(p)

    r = auth_client(u).delete(reverse("deployedmodel-detail", args=[str(m.id)]))
    assert r.status_code == status.HTTP_204_NO_CONTENT
    m.refresh_from_db()
    assert m.status == DeployedModel.Status.DELETED
    assert [call.method for call in gateway.calls] == ["DELETE"]


def test_deploying_a_live_model_keeps_it_serving_without_new_gpu_work(fake_modal):
    u = make_user()
    p = make_project()
    make_member(u, p)
    job = _job(p, status="succeeded")
    m = _deployed_model(p, job)

    r = auth_client(u).post(reverse("deployedmodel-deploy", args=[str(m.id)]))

    assert r.status_code == status.HTTP_200_OK
    assert r.data["status"] == "ready"
    assert fake_modal.log == []


@pytest.mark.covers("recovery")
def test_retrying_a_failed_deployment_requeues_it_and_clears_the_error(fake_modal):
    u = make_user()
    p = make_project()
    make_member(u, p)
    job = _job(p, status="deploying")
    m = _deployed_model(p, job)
    DeployedModel.objects.filter(pk=m.pk).update(
        status=DeployedModel.Status.FAILED, error_message="Pre-warm failed: boom"
    )

    r = auth_client(u).post(reverse("deployedmodel-retry", args=[str(m.id)]))

    assert r.status_code == status.HTTP_200_OK
    assert fake_modal.log == []
    m.refresh_from_db()
    assert m.status == DeployedModel.Status.QUEUED
    assert m.error_message == ""


def test_deployed_models_retry_rejects_undeployable_job():
    """A failed training job has no checkpoint — the deploy task would no-op and park it QUEUED."""
    u = make_user()
    p = make_project()
    make_member(u, p)
    job = _job(p, status="failed")
    m = _deployed_model(p, job)
    DeployedModel.objects.filter(pk=m.pk).update(status=DeployedModel.Status.FAILED)

    r = auth_client(u).post(reverse("deployedmodel-retry", args=[str(m.id)]))

    assert r.status_code == status.HTTP_400_BAD_REQUEST
    m.refresh_from_db()
    assert m.status == DeployedModel.Status.FAILED


@pytest.mark.covers("ledger")
def test_inference_pricing_estimate_and_unknown_gpu():
    from overbae.services.inference_pricing import estimate_call_cost, gpu_usd_per_second

    # H100 = $3.95/h → $/sec; 2000 ms of generation.
    cost = estimate_call_cost("H100", 2000)
    # Helper rounds to 6 dp, so compare within that resolution.
    assert cost == pytest.approx(2000 / 1000 * (3.95 / 3600), abs=1e-6)
    # Unknown GPU or missing timing → honest None, never 0.
    assert estimate_call_cost("TPUv9", 2000) is None
    assert estimate_call_cost("H100", None) is None
    assert gpu_usd_per_second("nope") is None


@pytest.mark.covers("measured-facts")
def test_model_metrics_endpoint_aggregates():
    u = make_user()
    p = make_project()
    make_member(u, p)
    m = _deployed_model(p)
    m.gpu_type = "H100"
    m.save(update_fields=["gpu_type"])

    for _ in range(3):
        InferenceCall.objects.create(
            deployed_model=m,
            project=p,
            prompt_tokens=10,
            completion_tokens=5,
            cost=0.001,
            tokens_per_second=50.0,
            latency_ms=300,
        )

    r = auth_client(u).get(reverse("deployedmodel-metrics", args=[str(m.id)]))
    assert r.status_code == status.HTTP_200_OK
    assert r.data["request_count"] == 3
    assert r.data["prompt_tokens"] == 30
    assert r.data["completion_tokens"] == 15
    assert r.data["total_tokens"] == 45
    assert r.data["cost"] == pytest.approx(0.003)
    assert r.data["cost_is_estimate"] is True
    assert r.data["avg_tokens_per_second"] == pytest.approx(50.0)
    assert r.data["avg_latency_ms"] == pytest.approx(300.0)


@pytest.mark.covers("measured-facts")
def test_metrics_separate_failures_end_to_end_and_warm_engine_latency():
    user, project = make_user(), make_project()
    make_member(user, project)
    model = _deployed_model(project, _job(project))
    InferenceCall.objects.create(
        deployed_model=model, project=project, latency_ms=10, end_to_end_ms=100, is_cold=False
    )
    InferenceCall.objects.create(
        deployed_model=model, project=project, latency_ms=500, end_to_end_ms=500, is_cold=True
    )
    failed = InferenceCall.objects.create(
        deployed_model=model,
        project=project,
        latency_ms=9999,
        end_to_end_ms=9999,
        outcome="failed",
        error_code="server_error",
    )
    response = auth_client(user).get(reverse("deployedmodel-metrics", args=[model.id]))
    assert response.status_code == 200
    assert response.data["request_count"] == 3
    assert response.data["failed_request_count"] == 1
    assert response.data["cold_request_count"] == 1
    assert response.data["avg_latency_ms"] == 10
    assert response.data["end_to_end_p50_ms"] == 300
    assert response.data["latest_failure"]["id"] == str(failed.pk)
