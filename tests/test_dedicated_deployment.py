"""Dedicated GPU menu, hourly metering, and dedicated/<model> chat routing."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.core.errors import InputValidationError
from overbae.modal.gpu_selector import GPU_TIERS, dedicated_gpu_names, select_gpu
from overbae.models import BillingTelemetry, DedicatedDeployment, InferenceCall, Project, User
from overbae.models.billing import BillingService
from overbae.models.inference import DeployedModel
from overbae.services import dedicated
from overbae.services.provider_pricing import refresh_rates

pytestmark = pytest.mark.django_db

_SMALL = {
    "total_params_b": 3,
    "num_attn_layers": 28,
    "num_kv_heads": 8,
    "head_dim": 128,
}
_HUGE = {
    "total_params_b": 70,
    "num_attn_layers": 80,
    "num_kv_heads": 8,
    "head_dim": 128,
}


def _user() -> User:
    return User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )


def _ready(project: Project) -> DeployedModel:
    return DeployedModel.objects.create(
        project=project,
        model_id=f"ft-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.READY,
        base_model_id="meta-llama/Llama-3.2-3B-Instruct",
        gpu_type="L4",
    )


def _ok(**_kwargs):
    return {
        "status": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(
            {
                "model": "dedicated/internal",
                "system_fingerprint": "vllm-0.30.0-nohash",
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode(),
    }


def test_h100_is_a_dedicated_choice_beside_a100_and_not_a_serverless_pick():
    names = dedicated_gpu_names(_SMALL, 2048, fp8=False)
    assert "A100-80GB" in names
    assert "H100" in names
    assert "H100" not in dedicated_gpu_names(_HUGE, 2048, fp8=False)
    assert "H100" not in {tier["name"] for tier in GPU_TIERS}
    assert select_gpu(_SMALL, 2048)[0] != "H100"


def test_missing_rate_refuses_start_and_a_second_start_conflicts(fake_modal):
    refresh_rates()
    user = _user()
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    deployed = _ready(project)
    with pytest.raises(InputValidationError, match="price is unavailable"):
        dedicated.start(deployed, user, gpu_type="L4")
    fake_modal.deploy("overmind-dedicated", "H100_vllm.infer", _ok)
    row = dedicated.start(deployed, user, gpu_type="H100")
    assert row.credits_per_gpu_hour > 0
    with pytest.raises(InputValidationError, match="already has a dedicated"):
        dedicated.start(deployed, user, gpu_type="H100")


def test_stopped_deployment_can_restart_with_its_saved_configuration(fake_modal):
    refresh_rates()
    user = _user()
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    deployed = _ready(project)
    fake_modal.deploy("overmind-dedicated", "H100_vllm.infer", _ok)
    row = dedicated.start(
        deployed,
        user,
        gpu_type="H100",
        min_replicas=2,
        max_replicas=3,
        idle_shutdown_seconds=600,
    )
    dedicated.advance(str(row.id))
    dedicated.advance(str(row.id))
    dedicated.stop(row)
    fake_modal.worker_stats = {"num_total_runners": 0, "backlog": 0, "num_running_inputs": 0}
    dedicated.advance(str(row.id))
    row.refresh_from_db()
    assert row.status == DedicatedDeployment.Status.OFFLINE
    assert any(
        kind == "autoscaler" and kwargs.get("min_containers") == 0
        for kind, _, _, kwargs in fake_modal.log
    )
    restarted = dedicated.start(
        deployed,
        user,
        gpu_type=row.gpu_type,
        min_replicas=row.min_replicas,
        max_replicas=row.max_replicas,
        scale_to_zero=row.scale_to_zero,
        idle_shutdown_seconds=row.idle_shutdown_seconds,
    )
    assert restarted.min_replicas == 2
    assert restarted.max_replicas == 3
    assert restarted.idle_shutdown_seconds == 600


def test_running_replicas_are_metered_and_idle_replicas_are_not(fake_modal):
    refresh_rates()
    user = _user()
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    deployed = _ready(project)
    fake_modal.deploy("overmind-dedicated", "H100_vllm.infer", _ok)
    fake_modal.worker_stats = {"num_total_runners": 2, "backlog": 0, "num_running_inputs": 0}
    row = dedicated.start(deployed, user, gpu_type="H100", min_replicas=1, max_replicas=2)
    dedicated.advance(str(row.id))
    dedicated.advance(str(row.id))
    row.refresh_from_db()
    assert row.status == DedicatedDeployment.Status.ONLINE
    DedicatedDeployment.objects.filter(pk=row.pk).update(
        last_billed_at=timezone.now() - timedelta(minutes=2)
    )
    dedicated.advance(str(row.id))
    charges = BillingTelemetry.objects.filter(service=BillingService.DEDICATED_INFERENCE)
    assert charges.count() == 1
    assert charges.get().amount < 0
    row.refresh_from_db()
    assert row.accrued_credits > 0
    assert row.running_replicas == 2

    fake_modal.worker_stats = {"num_total_runners": 0, "backlog": 0, "num_running_inputs": 0}
    DedicatedDeployment.objects.filter(pk=row.pk).update(
        last_billed_at=timezone.now() - timedelta(minutes=3),
        next_poll_at=timezone.now(),
    )
    dedicated.advance(str(row.id))
    assert BillingTelemetry.objects.filter(service=BillingService.DEDICATED_INFERENCE).count() == 1
    row.refresh_from_db()
    assert row.running_replicas == 0


def test_gpu_change_scales_the_previous_gpu_to_zero(fake_modal):
    refresh_rates()
    user = _user()
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    deployed = _ready(project)
    fake_modal.deploy("overmind-dedicated", "H100_vllm.infer", _ok)
    row = dedicated.start(deployed, user, gpu_type="H100", min_replicas=1, max_replicas=1)
    dedicated.advance(str(row.id))
    dedicated.advance(str(row.id))
    dedicated.update(row, gpu_type="H200")
    drained = [
        kwargs
        for kind, name, _, kwargs in fake_modal.log
        if kind == "autoscaler" and name.endswith("H100_vllm") and kwargs.get("min_containers") == 0
    ]
    assert drained
    row.refresh_from_db()
    assert row.gpu_type == "H200"
    assert row.status == DedicatedDeployment.Status.DEPLOYING


def test_dedicated_chat_skips_per_request_billing_and_appears_in_the_model_list(fake_modal):
    refresh_rates()
    user = _user()
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    from overbae.models import ProjectMembership

    ProjectMembership.objects.create(user=user, project=project)
    deployed = _ready(project)
    fake_modal.deploy("overmind-dedicated", "H100_vllm.infer", _ok)
    row = dedicated.start(deployed, user, gpu_type="H100")
    dedicated.advance(str(row.id))
    dedicated.advance(str(row.id))
    row.refresh_from_db()
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    response = client.post(
        "/api/v1/chat/completions",
        {"model": row.public_model_id, "messages": [{"role": "user", "content": "Hi"}]},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["model"] == f"dedicated/{deployed.model_id}"
    assert "system_fingerprint" not in response.json()
    call = InferenceCall.objects.get(requested_model=row.public_model_id)
    assert call.cost is None
    assert BillingTelemetry.objects.filter(service=BillingService.INFERENCE_FT_MODEL).count() == 0
    listed = client.get("/api/v1/models")
    assert row.public_model_id in {item["id"] for item in listed.json()["data"]}


def test_dedicated_list_returns_the_latest_row_for_each_model():
    from overbae.models import ProjectMembership

    user = _user()
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    other = Project.objects.create(name="Other", slug=f"p-{uuid.uuid4().hex[:8]}")
    first = _ready(project)
    second = _ready(project)
    foreign = _ready(other)

    def row(deployed, gpu, status):
        return DedicatedDeployment.objects.create(
            project=deployed.project,
            deployed_model=deployed,
            gpu_type=gpu,
            serving_shape="lora_bf16",
            status=status,
            rate_usd_per_gpu_hour="1",
            credits_per_gpu_hour="100",
        )

    row(first, "L4", DedicatedDeployment.Status.OFFLINE)
    current = row(first, "H100", DedicatedDeployment.Status.ONLINE)
    row(second, "L40S", DedicatedDeployment.Status.OFFLINE)
    row(foreign, "H100", DedicatedDeployment.Status.ONLINE)
    client = APIClient()
    client.force_authenticate(user)
    response = client.get("/api/deployed-models/dedicated/", {"project": str(project.id)})
    assert response.status_code == 200
    body = response.json()["results"]
    assert {item["deployed_model"] for item in body} == {str(first.id), str(second.id)}
    listed = next(item for item in body if item["deployed_model"] == str(first.id))
    assert listed["id"] == str(current.id)
    assert listed["gpu_type"] == "H100"
    assert listed["model_name"] == first.model_id
    assert listed["model_id"] == f"dedicated/{first.model_id}"
