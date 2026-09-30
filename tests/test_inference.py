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

pytestmark = pytest.mark.django_db


def _dataset(capability: Capability) -> Dataset:
    ds = frozen_dataset(capability.project, [{"input": {"q": "test"}}], capability=capability)
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


def test_inference_client_is_model_ready_returns_false_on_error():
    from overbae.services.inference_client import InferenceClient

    client = InferenceClient(base_url="https://example.modal.run", api_key="k")
    assert client.is_model_ready("some-model-id") is False


def test_inference_client_is_model_ready_returns_true_on_status():
    from overbae.services.inference_client import InferenceClient

    p = make_project()
    m = _deployed_model(p)
    client = InferenceClient(base_url="https://example.modal.run", api_key="k")
    assert client.is_model_ready(m.model_id) is True


def test_inference_client_check_health_returns_false_on_error(scripted):
    from overbae.services.inference_client import InferenceClient

    scripted("https://example.modal.run").reply(503, text="unreachable")
    client = InferenceClient(base_url="https://example.modal.run", api_key="k")
    assert client.check_health() is False


def test_make_model_id_slug_format():
    from overbae.services.deployment import model_slug

    p = make_project()
    job = _job(p)
    slug = model_slug(job)
    assert slug.startswith("ft-")
    assert " " not in slug
    assert slug == slug.lower()
    assert str(job.id)[:8] in slug


@pytest.mark.django_db
def test_deploy_task_skips_when_no_remote_job_id():
    from overbae.tasks.model_deployment import register_finetuned_model

    p = make_project()
    capability = make_capability(p)
    dataset = _dataset(capability)
    job = FinetuningJob.objects.create(
        project=p,
        dataset=dataset,
        base_model="meta-llama/Meta-Llama-3.1-8B-Instruct-Reference",
        status="succeeded",
        remote_job_id="",
    )

    register_finetuned_model(job_id=str(job.id))
    assert not DeployedModel.objects.filter(finetuning_job=job).exists()


@pytest.mark.django_db
def test_deploy_task_skips_non_succeeded_job():
    from overbae.tasks.model_deployment import register_finetuned_model

    p = make_project()
    job = _job(p, status="running")

    register_finetuned_model(job_id=str(job.id))
    assert not DeployedModel.objects.filter(finetuning_job=job).exists()


def test_deploy_task_only_initializes_durable_state(fake_modal):
    from overbae.tasks.model_deployment import register_finetuned_model

    job = _job(make_project(), status="succeeded")
    register_finetuned_model(job_id=str(job.id))
    register_finetuned_model(job_id=str(job.id))
    deployed = DeployedModel.objects.get(finetuning_job=job)
    assert deployed.status == "queued"
    assert deployed.deployment_stage == "base"
    assert deployed.deployment_attempts == 1
    assert fake_modal.log == []


def test_deployed_models_list_returns_200():
    u = make_user()
    p = make_project()
    make_member(u, p)
    _deployed_model(p, _job(p))

    r = auth_client(u).get(reverse("deployedmodel-list"))
    assert r.status_code == status.HTTP_200_OK
    assert r.data["count"] == 1


def test_deployed_models_list_scoped_to_user_projects():
    u_a = make_user()
    p_a = make_project()
    make_member(u_a, p_a)
    _deployed_model(p_a, _job(p_a))

    p_b = make_project()
    _deployed_model(p_b, _job(p_b))

    r = auth_client(u_a).get(reverse("deployedmodel-list"))
    assert r.status_code == status.HTTP_200_OK
    assert r.data["count"] == 1
    assert str(r.data["results"][0]["project"]) == str(p_a.id)


def test_deployed_models_list_filters_by_project_server_side():
    """The 30 noisy rows exceed the 25-row page, so a client-side match would miss ``mine``."""
    u = make_user()
    wanted, noisy = make_project(), make_project()
    make_member(u, wanted)
    make_member(u, noisy)
    mine = _deployed_model(wanted, _job(wanted))
    for _ in range(30):
        _deployed_model(noisy, _job(noisy))

    r = auth_client(u).get(reverse("deployedmodel-list"), {"project": str(wanted.id)})
    assert r.status_code == status.HTTP_200_OK
    assert r.data["count"] == 1
    assert str(r.data["results"][0]["id"]) == str(mine.id)


def test_deployed_models_list_filters_by_capability_and_status():
    u = make_user()
    p = make_project()
    make_member(u, p)
    capability = make_capability(p)
    job = _job(p)
    FinetuningJob.objects.filter(pk=job.pk).update(capability=capability)
    mine = _deployed_model(p, job)
    other = _deployed_model(p, _job(p))
    DeployedModel.objects.filter(pk=other.pk).update(status=DeployedModel.Status.FAILED)

    client = auth_client(u)
    by_capability = client.get(
        reverse("deployedmodel-list"), {"capability": str(capability.id), "project": str(p.id)}
    )
    assert [str(row["id"]) for row in by_capability.data["results"]] == [str(mine.id)]

    by_status = client.get(reverse("deployedmodel-list"), {"status": "failed"})
    assert [str(row["id"]) for row in by_status.data["results"]] == [str(other.id)]


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


@pytest.mark.parametrize("deployment_status", ["deploying", "warming", "ready", "failed"])
def test_deployed_models_list_excludes_eval_infrastructure_before_pagination(deployment_status):
    user = make_user()
    project = make_project()
    make_member(user, project)
    trained = _deployed_model(project, _job(project))
    DeployedModel.objects.filter(pk=trained.pk).update(status=deployment_status)
    for index in range(26):
        baseline = DeployedModel.objects.create(
            project=project,
            model_id=f"base--eval-{index}",
            status=deployment_status,
        )

    client = auth_client(user)
    response = client.get(reverse("deployedmodel-list"), {"page_size": 1})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["next"] is None
    assert [str(row["id"]) for row in response.data["results"]] == [str(trained.id)]

    search = client.get(reverse("deployedmodel-list"), {"search": "base--eval"})
    assert search.data["count"] == 0
    detail = client.get(reverse("deployedmodel-detail", args=[baseline.id]))
    assert detail.status_code == status.HTTP_200_OK
    assert DeployedModel.objects.filter(project=project, finetuning_job__isnull=True).count() == 26


def test_deployed_models_list_rejects_a_malformed_capability_id():
    """The ``NO_CAPABILITY`` sentinel costs the param its ``UUIDFilter``; the 400 is raised by hand."""
    u = make_user()
    p = make_project()
    make_member(u, p)
    _deployed_model(p)

    r = auth_client(u).get(reverse("deployedmodel-list"), {"capability": "not-a-uuid"})
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert NO_CAPABILITY in str(r.data["capability"])


def test_deployed_models_list_honours_search_and_ordering():
    u = make_user()
    p = make_project()
    make_member(u, p)
    first = _deployed_model(p, _job(p))
    second = _deployed_model(p, _job(p))

    client = auth_client(u)
    found = client.get(reverse("deployedmodel-list"), {"search": first.model_id})
    assert [str(row["id"]) for row in found.data["results"]] == [str(first.id)]

    oldest_first = client.get(reverse("deployedmodel-list"), {"ordering": "created_at"})
    assert [str(row["id"]) for row in oldest_first.data["results"]] == [
        str(first.id),
        str(second.id),
    ]
    default = client.get(reverse("deployedmodel-list"))
    assert [str(row["id"]) for row in default.data["results"]] == [str(second.id), str(first.id)]


def test_deployed_models_list_composes_search_with_the_capability_filter():
    """``?capability=`` (DjangoFilterBackend) and ``?search=`` (SearchFilter) are separate backends
    applied in sequence — they must intersect, not override each other."""
    u = make_user()
    p = make_project()
    make_member(u, p)

    def attributed_job(capability):
        job = _job(p)
        FinetuningJob.objects.filter(pk=job.pk).update(capability=capability)
        return job

    mine, theirs = make_capability(p), make_capability(p)

    # A job holds at most one deployment (OneToOne), so each row needs its own.
    wanted = _deployed_model(p, attributed_job(mine))
    same_capability_no_match = _deployed_model(p, attributed_job(mine))
    other_capability_matches = _deployed_model(p, attributed_job(theirs))
    DeployedModel.objects.filter(pk=wanted.pk).update(model_id="ft-needle-in-scope")
    DeployedModel.objects.filter(pk=other_capability_matches.pk).update(
        model_id="ft-needle-elsewhere"
    )

    client = auth_client(u)
    scope = {"project": str(p.id)}
    both = client.get(
        reverse("deployedmodel-list"), {**scope, "capability": str(mine.id), "search": "needle"}
    )
    assert both.status_code == status.HTTP_200_OK
    assert [str(row["id"]) for row in both.data["results"]] == [str(wanted.id)]

    # Each half alone keeps a row the intersection drops — else an ignored filter would pass.
    search_only = client.get(reverse("deployedmodel-list"), {**scope, "search": "needle"})
    assert {str(row["id"]) for row in search_only.data["results"]} == {
        str(wanted.id),
        str(other_capability_matches.id),
    }
    capability_only = client.get(
        reverse("deployedmodel-list"), {**scope, "capability": str(mine.id)}
    )
    assert {str(row["id"]) for row in capability_only.data["results"]} == {
        str(wanted.id),
        str(same_capability_no_match.id),
    }


def test_deployed_models_list_still_overlays_median_latency_when_filtered():
    u = make_user()
    p = make_project()
    make_member(u, p)
    m = _deployed_model(p, _job(p))
    for latency in (10, 20, 3000):
        InferenceCall.objects.create(
            deployed_model=m,
            project=p,
            latency_ms=latency,
            tokens_per_second=10.0,
            is_cold=False,
        )

    r = auth_client(u).get(reverse("deployedmodel-list"), {"project": str(p.id)})
    assert r.status_code == status.HTTP_200_OK
    # Median, not the 1010ms mean the SQL annotation produced.
    assert r.data["results"][0]["avg_latency_ms"] == 20


def test_deployed_models_retrieve():
    u = make_user()
    p = make_project()
    make_member(u, p)
    m = _deployed_model(p)

    r = auth_client(u).get(reverse("deployedmodel-detail", args=[str(m.id)]))
    assert r.status_code == status.HTTP_200_OK
    assert r.data["model_id"] == m.model_id
    assert r.data["status"] == "ready"


def test_deployed_models_retrieve_forbidden_for_other_user():
    u_a = make_user()
    p_a = make_project()
    make_member(u_a, p_a)
    m = _deployed_model(p_a)

    u_b = make_user()  # no membership in p_a
    r = auth_client(u_b).get(reverse("deployedmodel-detail", args=[str(m.id)]))
    assert r.status_code == status.HTTP_404_NOT_FOUND


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


def test_model_metrics_endpoint_empty_is_zeroed():
    u = make_user()
    p = make_project()
    make_member(u, p)
    m = _deployed_model(p)

    r = auth_client(u).get(reverse("deployedmodel-metrics", args=[str(m.id)]))
    assert r.status_code == status.HTTP_200_OK
    assert r.data["request_count"] == 0
    assert r.data["total_tokens"] == 0
    assert r.data["cost"] is None
    assert r.data["cost_is_estimate"] is False


def test_model_activity_endpoint_returns_points():
    u = make_user()
    p = make_project()
    make_member(u, p)
    m = _deployed_model(p)
    InferenceCall.objects.create(deployed_model=m, project=p, prompt_tokens=7, completion_tokens=3)

    r = auth_client(u).get(reverse("deployedmodel-activity", args=[str(m.id)]))
    assert r.status_code == status.HTTP_200_OK
    points = r.data["points"]
    assert len(points) == 1
    assert points[0]["request_count"] == 1
    assert points[0]["total_tokens"] == 10


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
