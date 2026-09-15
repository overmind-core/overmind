from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from conftest import frozen_dataset
from django.db import IntegrityError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.api.filters import NO_CAPABILITY
from overbae.models import (
    Capability,
    Dataset,
    FinetuningJob,
    Project,
    ProjectMembership,
    User,
)
from overbae.models.inference import DeployedModel, InferenceCall

pytestmark = pytest.mark.django_db


def _user(email: str | None = None) -> User:
    return User.objects.create_user(
        email=email or f"u-{uuid.uuid4().hex[:6]}@test.com",
        password="pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        projects_limit=5,
    )


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _membership(user: User, project: Project) -> None:
    ProjectMembership.objects.create(user=user, project=project)


def _capability(project: Project) -> Capability:
    slug = f"a-{uuid.uuid4().hex[:8]}"
    return Capability.objects.create(project=project, name=slug, slug=slug)


def _dataset(capability: Capability) -> Dataset:
    ds = frozen_dataset(capability.project, [{"input": {"q": "test"}}], capability=capability)
    return ds


def _job(project: Project, *, status: str = "succeeded") -> FinetuningJob:
    capability = _capability(project)
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


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def test_deployed_model_create_and_str():
    p = _project()
    m = DeployedModel.objects.create(
        project=p,
        model_id="ft-test-abc12345",
        status=DeployedModel.Status.QUEUED,
        base_model_id="meta-llama/Meta-Llama-3.1-8B-Instruct-Reference",
    )
    assert str(m) == "ft-test-abc12345 [queued]"
    assert m.is_terminal is False


def test_deployed_model_is_terminal_for_ready():
    p = _project()
    m = DeployedModel.objects.create(
        project=p,
        model_id=f"ft-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.READY,
        base_model_id="x",
    )
    assert m.is_terminal is True


def test_deployed_model_is_terminal_for_failed():
    p = _project()
    m = DeployedModel.objects.create(
        project=p,
        model_id=f"ft-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.FAILED,
        base_model_id="x",
    )
    assert m.is_terminal is True


def test_deployed_model_is_not_terminal_while_deploying():
    p = _project()
    m = DeployedModel.objects.create(
        project=p,
        model_id=f"ft-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.DEPLOYING,
        base_model_id="x",
    )
    assert m.is_terminal is False


def test_deployed_model_model_id_unique():
    p = _project()
    model_id = f"ft-unique-{uuid.uuid4().hex[:8]}"
    DeployedModel.objects.create(project=p, model_id=model_id, base_model_id="x")
    with pytest.raises(IntegrityError):
        DeployedModel.objects.create(project=p, model_id=model_id, base_model_id="x")


def test_inference_client_headers():
    from overbae.services.inference_client import InferenceClient

    client = InferenceClient(base_url="https://example.modal.run", api_key="test-key-123")
    assert client._headers["Authorization"] == "Bearer test-key-123"
    assert client._headers["Content-Type"] == "application/json"


def test_inference_client_base_url_strips_trailing_slash():
    from overbae.services.inference_client import InferenceClient

    client = InferenceClient(base_url="https://example.modal.run/", api_key="k")
    assert not client._base_url.endswith("/")


def test_inference_client_is_model_ready_returns_false_on_error():
    from overbae.services.inference_client import InferenceClient

    client = InferenceClient(base_url="https://example.modal.run", api_key="k")
    assert client.is_model_ready("some-model-id") is False


def test_inference_client_is_model_ready_returns_true_on_status():
    from overbae.services.inference_client import InferenceClient

    p = _project()
    m = _deployed_model(p)
    client = InferenceClient(base_url="https://example.modal.run", api_key="k")
    assert client.is_model_ready(m.model_id) is True


def test_inference_client_check_health_returns_false_on_error():
    from overbae.services.inference_client import InferenceClient

    client = InferenceClient(base_url="https://example.modal.run", api_key="k")
    with patch.object(client, "_get", side_effect=Exception("unreachable")):
        assert client.check_health() is False


def test_inference_client_stream_chat_yields_lines():
    from overbae.services.inference_client import InferenceClient

    client = InferenceClient(base_url="https://example.modal.run", api_key="k")
    fake_resp = MagicMock()
    fake_resp.iter_lines.return_value = [b"data: hello", b"data: world"]
    with patch.object(client, "chat_completions", return_value=fake_resp):
        chunks = list(
            client.stream_chat_completions(
                model_id="ft-test",
                messages=[{"role": "user", "content": "hi"}],
            )
        )
    assert chunks == ["data: hello\n\n", "data: world\n\n"]


def test_make_model_id_slug_format():
    from overbae.tasks.model_deployment import _make_model_id

    p = _project()
    job = _job(p)
    slug = _make_model_id(job)
    assert slug.startswith("ft-")
    assert " " not in slug
    assert slug == slug.lower()
    assert str(job.id)[:8] in slug


@pytest.mark.django_db
def test_deploy_task_skips_when_no_remote_job_id():
    from overbae.tasks.model_deployment import register_finetuned_model

    p = _project()
    capability = _capability(p)
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

    p = _project()
    job = _job(p, status="running")

    register_finetuned_model(job_id=str(job.id))
    assert not DeployedModel.objects.filter(finetuning_job=job).exists()


def _mock_modal():
    mock_register_cls = MagicMock()
    mock_register_inst = MagicMock()
    mock_register_cls.return_value = mock_register_inst
    mock_register_inst.register.remote.return_value = {
        "weights_path": "/vol/ft-abc",
        "is_lora": False,
        "quantization": "fp8",
        "num_parameters": 1000,
        "base_model": "meta-llama/Meta-Llama-3.1-8B-Instruct-Reference",
        "ready": True,
    }

    mock_api_cls = MagicMock()
    mock_api_inst = MagicMock()
    mock_api_cls.return_value = mock_api_inst
    mock_api_inst.register_model.remote.return_value = "https://worker.example.com"

    mock_fn = MagicMock()
    mock_fn.remote.return_value = None

    def _from_name(app, cls_name, **_kw):
        if app == "overmind-register":
            return mock_register_cls
        return mock_api_cls

    import contextlib

    @contextlib.contextmanager
    def _ctx():
        with (
            patch("modal.Cls.from_name", side_effect=_from_name),
            patch("modal.Function.from_name", return_value=mock_fn),
        ):
            yield mock_register_inst, mock_api_inst

    return _ctx()


@pytest.mark.django_db
def test_deploy_task_creates_deployed_model_and_triggers_modal():
    from overbae.tasks.model_deployment import register_finetuned_model

    p = _project()
    job = _job(p, status="succeeded")

    with _mock_modal():
        register_finetuned_model(job_id=str(job.id))

    deployed = DeployedModel.objects.get(finetuning_job=job)
    assert deployed.status == DeployedModel.Status.READY
    assert deployed.deployed_at is not None
    assert deployed.quantization == DeployedModel.Quantization.FP8
    assert deployed.is_lora is False
    assert deployed.weights_path == "/vol/ft-abc"


@pytest.mark.django_db
def test_deploy_task_marks_failed_on_trigger_error():
    from overbae.tasks.model_deployment import register_finetuned_model

    p = _project()
    job = _job(p, status="succeeded")

    mock_register_cls = MagicMock()
    mock_register_inst = MagicMock()
    mock_register_cls.return_value = mock_register_inst
    mock_register_inst.register.remote.side_effect = RuntimeError("Modal is down")

    from celery.exceptions import Retry

    # called_directly=True would make self.retry() re-raise instead of signalling Retry.
    register_finetuned_model.push_request(is_eager=True, called_directly=False)
    try:
        with (
            patch("modal.Cls.from_name", return_value=mock_register_cls),
            pytest.raises(Retry),
        ):
            register_finetuned_model.run(job_id=str(job.id))
    finally:
        register_finetuned_model.pop_request()

    deployed = DeployedModel.objects.get(finetuning_job=job)
    assert deployed.status == DeployedModel.Status.FAILED
    # Persist a console-safe summary — never the provider exception text.
    assert deployed.error_message == "Download failed"
    assert "Modal" not in (deployed.error_message or "")


@pytest.mark.django_db
def test_deploy_task_idempotent_skips_already_ready():
    from overbae.tasks.model_deployment import register_finetuned_model

    p = _project()
    job = _job(p, status="succeeded")
    existing = _deployed_model(p, job)

    with _mock_modal() as (mock_register, _):
        register_finetuned_model(job_id=str(job.id))

    mock_register.register.remote.assert_not_called()
    existing.refresh_from_db()
    assert existing.status == DeployedModel.Status.READY


def test_deployed_models_list_requires_auth():
    r = APIClient().get(reverse("deployedmodel-list"))
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_deployed_models_list_returns_200():
    u = _user()
    p = _project()
    _membership(u, p)
    _deployed_model(p)

    r = _auth_client(u).get(reverse("deployedmodel-list"))
    assert r.status_code == status.HTTP_200_OK
    assert r.data["count"] == 1


def test_deployed_models_list_scoped_to_user_projects():
    u_a = _user()
    p_a = _project()
    _membership(u_a, p_a)
    _deployed_model(p_a)

    p_b = _project()
    _deployed_model(p_b)

    r = _auth_client(u_a).get(reverse("deployedmodel-list"))
    assert r.status_code == status.HTTP_200_OK
    assert r.data["count"] == 1
    assert str(r.data["results"][0]["project"]) == str(p_a.id)


def test_deployed_models_list_filters_by_project_server_side():
    """The 30 noisy rows exceed the 25-row page, so a client-side match would miss ``mine``."""
    u = _user()
    wanted, noisy = _project(), _project()
    _membership(u, wanted)
    _membership(u, noisy)
    mine = _deployed_model(wanted)
    for _ in range(30):
        _deployed_model(noisy)

    r = _auth_client(u).get(reverse("deployedmodel-list"), {"project": str(wanted.id)})
    assert r.status_code == status.HTTP_200_OK
    assert r.data["count"] == 1
    assert str(r.data["results"][0]["id"]) == str(mine.id)


def test_deployed_models_list_filters_by_capability_and_status():
    u = _user()
    p = _project()
    _membership(u, p)
    capability = _capability(p)
    job = _job(p)
    FinetuningJob.objects.filter(pk=job.pk).update(capability=capability)
    mine = _deployed_model(p, job)
    other = _deployed_model(p, _job(p))
    DeployedModel.objects.filter(pk=other.pk).update(status=DeployedModel.Status.FAILED)

    client = _auth_client(u)
    by_capability = client.get(
        reverse("deployedmodel-list"), {"capability": str(capability.id), "project": str(p.id)}
    )
    assert [str(row["id"]) for row in by_capability.data["results"]] == [str(mine.id)]

    by_status = client.get(reverse("deployedmodel-list"), {"status": "failed"})
    assert [str(row["id"]) for row in by_status.data["results"]] == [str(other.id)]


def test_deployed_models_list_filters_to_the_no_capability_bucket():
    """Both unassigned shapes — a job carrying no capability and no job at all — share one bucket;
    the nullable join must stay a LEFT OUTER or the jobless row drops."""
    u = _user()
    p = _project()
    _membership(u, p)
    capability = _capability(p)
    attributed_job = _job(p)
    FinetuningJob.objects.filter(pk=attributed_job.pk).update(capability=capability)

    attributed = _deployed_model(p, attributed_job)
    job_without_capability = _deployed_model(p, _job(p))
    without_job = _deployed_model(p)

    client = _auth_client(u)
    bucket = client.get(
        reverse("deployedmodel-list"), {"capability": NO_CAPABILITY, "project": str(p.id)}
    )
    assert bucket.status_code == status.HTTP_200_OK
    assert {str(row["id"]) for row in bucket.data["results"]} == {
        str(job_without_capability.id),
        str(without_job.id),
    }
    assert {row["capability_id"] for row in bucket.data["results"]} == {None}

    # The two buckets partition the project: nothing unreachable, nothing double-counted.
    named = client.get(
        reverse("deployedmodel-list"), {"capability": str(capability.id), "project": str(p.id)}
    )
    assert [str(row["id"]) for row in named.data["results"]] == [str(attributed.id)]
    assert bucket.data["count"] + named.data["count"] == 3


def test_deployed_models_list_rejects_a_malformed_capability_id():
    """The ``NO_CAPABILITY`` sentinel costs the param its ``UUIDFilter``; the 400 is raised by hand."""
    u = _user()
    p = _project()
    _membership(u, p)
    _deployed_model(p)

    r = _auth_client(u).get(reverse("deployedmodel-list"), {"capability": "not-a-uuid"})
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert NO_CAPABILITY in str(r.data["capability"])


def test_deployed_models_list_honours_search_and_ordering():
    u = _user()
    p = _project()
    _membership(u, p)
    first = _deployed_model(p)
    second = _deployed_model(p)

    client = _auth_client(u)
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
    u = _user()
    p = _project()
    _membership(u, p)

    def attributed_job(capability):
        job = _job(p)
        FinetuningJob.objects.filter(pk=job.pk).update(capability=capability)
        return job

    mine, theirs = _capability(p), _capability(p)

    # A job holds at most one deployment (OneToOne), so each row needs its own.
    wanted = _deployed_model(p, attributed_job(mine))
    same_capability_no_match = _deployed_model(p, attributed_job(mine))
    other_capability_matches = _deployed_model(p, attributed_job(theirs))
    DeployedModel.objects.filter(pk=wanted.pk).update(model_id="ft-needle-in-scope")
    DeployedModel.objects.filter(pk=other_capability_matches.pk).update(
        model_id="ft-needle-elsewhere"
    )

    client = _auth_client(u)
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
    u = _user()
    p = _project()
    _membership(u, p)
    m = _deployed_model(p)
    for latency in (10, 20, 3000):
        InferenceCall.objects.create(
            deployed_model=m,
            project=p,
            latency_ms=latency,
            tokens_per_second=10.0,
            is_cold=False,
        )

    r = _auth_client(u).get(reverse("deployedmodel-list"), {"project": str(p.id)})
    assert r.status_code == status.HTTP_200_OK
    # Median, not the 1010ms mean the SQL annotation produced.
    assert r.data["results"][0]["avg_latency_ms"] == 20


def test_deployed_models_retrieve():
    u = _user()
    p = _project()
    _membership(u, p)
    m = _deployed_model(p)

    r = _auth_client(u).get(reverse("deployedmodel-detail", args=[str(m.id)]))
    assert r.status_code == status.HTTP_200_OK
    assert r.data["model_id"] == m.model_id
    assert r.data["status"] == "ready"


def test_deployed_models_retrieve_forbidden_for_other_user():
    u_a = _user()
    p_a = _project()
    _membership(u_a, p_a)
    m = _deployed_model(p_a)

    u_b = _user()  # no membership in p_a
    r = _auth_client(u_b).get(reverse("deployedmodel-detail", args=[str(m.id)]))
    assert r.status_code == status.HTTP_404_NOT_FOUND


def test_deployed_models_delete_marks_deleted(settings):
    settings.INFERENCE_API_URL = ""  # Skip actual Modal call
    u = _user()
    p = _project()
    _membership(u, p)
    m = _deployed_model(p)

    r = _auth_client(u).delete(reverse("deployedmodel-detail", args=[str(m.id)]))
    assert r.status_code == status.HTTP_204_NO_CONTENT
    m.refresh_from_db()
    assert m.status == DeployedModel.Status.DELETED


def test_deployed_models_deploy_action_dispatches_task():
    u = _user()
    p = _project()
    _membership(u, p)
    job = _job(p, status="succeeded")
    m = _deployed_model(p, job)

    with patch("overbae.tasks.model_deployment.register_finetuned_model") as mock_task:
        mock_task.delay = MagicMock()
        r = _auth_client(u).post(reverse("deployedmodel-deploy", args=[str(m.id)]))

    assert r.status_code == status.HTTP_200_OK
    mock_task.delay.assert_called_once_with(job_id=str(job.id))


def test_deployed_models_retry_action_dispatches_task():
    u = _user()
    p = _project()
    _membership(u, p)
    job = _job(p, status="deploying")
    m = _deployed_model(p, job)
    DeployedModel.objects.filter(pk=m.pk).update(
        status=DeployedModel.Status.FAILED, error_message="Pre-warm failed: boom"
    )

    with patch("overbae.tasks.model_deployment.register_finetuned_model") as mock_task:
        mock_task.delay = MagicMock()
        r = _auth_client(u).post(reverse("deployedmodel-retry", args=[str(m.id)]))

    assert r.status_code == status.HTTP_200_OK
    mock_task.delay.assert_called_once_with(job_id=str(job.id))
    m.refresh_from_db()
    assert m.status == DeployedModel.Status.QUEUED
    assert m.error_message == ""


def test_deployed_models_retry_rejects_undeployable_job():
    """A failed training job has no checkpoint — the deploy task would no-op and park it QUEUED."""
    u = _user()
    p = _project()
    _membership(u, p)
    job = _job(p, status="failed")
    m = _deployed_model(p, job)
    DeployedModel.objects.filter(pk=m.pk).update(status=DeployedModel.Status.FAILED)

    with patch("overbae.tasks.model_deployment.register_finetuned_model") as mock_task:
        mock_task.delay = MagicMock()
        r = _auth_client(u).post(reverse("deployedmodel-retry", args=[str(m.id)]))

    assert r.status_code == status.HTTP_400_BAD_REQUEST
    mock_task.delay.assert_not_called()
    m.refresh_from_db()
    assert m.status == DeployedModel.Status.FAILED


def test_deployed_models_url_resolves():
    url = reverse("deployedmodel-list")
    assert url == "/api/deployed-models/"


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
    u = _user()
    p = _project()
    _membership(u, p)
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

    r = _auth_client(u).get(reverse("deployedmodel-metrics", args=[str(m.id)]))
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
    u = _user()
    p = _project()
    _membership(u, p)
    m = _deployed_model(p)

    r = _auth_client(u).get(reverse("deployedmodel-metrics", args=[str(m.id)]))
    assert r.status_code == status.HTTP_200_OK
    assert r.data["request_count"] == 0
    assert r.data["total_tokens"] == 0
    assert r.data["cost"] is None
    assert r.data["cost_is_estimate"] is False


def test_model_activity_endpoint_returns_points():
    u = _user()
    p = _project()
    _membership(u, p)
    m = _deployed_model(p)
    InferenceCall.objects.create(deployed_model=m, project=p, prompt_tokens=7, completion_tokens=3)

    r = _auth_client(u).get(reverse("deployedmodel-activity", args=[str(m.id)]))
    assert r.status_code == status.HTTP_200_OK
    points = r.data["points"]
    assert len(points) == 1
    assert points[0]["request_count"] == 1
    assert points[0]["total_tokens"] == 10


def test_deployed_model_serializer_fields():
    from overbae.api.serializers import DeployedModelSerializer

    p = _project()
    job = _job(p)
    m = DeployedModel.objects.create(
        project=p,
        finetuning_job=job,
        model_id=f"ft-ser-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.READY,
        base_model_id="meta-llama/Meta-Llama-3.1-8B-Instruct-Reference",
    )

    data = DeployedModelSerializer(m).data
    assert data["model_id"] == m.model_id
    assert data["status"] == "ready"
    assert str(data["finetuning_job_id"]) == str(job.id)
    assert str(data["project"]) == str(p.id)
    assert "created_at" in data
