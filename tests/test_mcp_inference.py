from __future__ import annotations

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient
from starlette.testclient import TestClient

from modal_shared.context_budget import DEFAULT_OUTPUT_TOKENS
from overbae.models import (
    APIToken,
    Capability,
    DeployedModel,
    InferenceCall,
    InferenceRequest,
    ModelActivation,
    Project,
    ProjectMembership,
    User,
)
from overbae.services import inference_live, inference_requests
from overbae.services.mcp.server import create_mcp_application

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def serving(settings):
    settings.STRIPE_SECRET_KEY = ""
    user = User.objects.create_user(email=f"serving-{uuid.uuid4().hex}@test.com", password="pw")
    project = Project.objects.create(name="Serving", slug=f"serving-{uuid.uuid4().hex}")
    ProjectMembership.objects.create(user=user, project=project)
    key, token = APIToken.create_for_user(user, project=project)
    model = DeployedModel.objects.create(
        project=project, model_id="ft-mcp-serving", status="ready", max_model_len=16384
    )
    cache.clear()
    yield SimpleNamespace(user=user, project=project, key=key, token=token, model=model)
    cache.clear()


def rpc(serving, method, params):
    with TestClient(create_mcp_application()) as client:
        response = client.post(
            "/api/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"X-Api-Key": serving.key, "Accept": "application/json"},
        )
    assert response.status_code == 200
    return response.json()


def infer(serving, **overrides):
    result = rpc(
        serving,
        "tools/call",
        {
            "name": "run_inference",
            "arguments": {
                "deployment": str(serving.model.pk),
                "request_key": "test-" + uuid.uuid4().hex,
                "messages": [{"role": "user", "content": "Hello"}],
                **overrides,
            },
        },
    )["result"]
    text = next(item["text"] for item in result["content"] if item["type"] == "text")
    assert json.loads(text) == result["structuredContent"]
    return result


def provider_response(content="Hello", finish_reason="stop"):
    return Mock(
        ok=True,
        json=lambda: {
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 12, "total_tokens": 20},
        },
    )


@pytest.mark.parametrize(
    "budget", [{}, {"max_tokens": None}, {"max_tokens": 4096}, {"max_tokens": 12000}]
)
def test_mcp_and_production_preserve_the_same_output_reservation(serving, monkeypatch, budget):
    post = Mock(side_effect=lambda *args, **kwargs: provider_response())
    monkeypatch.setattr("overbae.services.inference_client.requests.post", post)
    result = infer(serving, **budget)
    assert not result.get("isError"), result
    request = InferenceRequest.objects.get(pk=result["structuredContent"]["job"]["id"])
    mcp_budget = request.payload["max_tokens"]
    post.assert_not_called()

    api = APIClient()
    api.force_authenticate(user=serving.user, token=serving.token)
    response = api.post(
        "/api/v1/chat/completions",
        {
            "model": serving.model.model_id,
            "messages": [{"role": "user", "content": "Hello"}],
            **budget,
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert mcp_budget == post.call_args.kwargs["json"]["max_tokens"]
    assert mcp_budget == (budget.get("max_tokens") or DEFAULT_OUTPUT_TOKENS)
    assert post.call_count == 1
    assert (
        InferenceCall.objects.filter(deployed_model=serving.model, outcome="succeeded").count() == 1
    )


@pytest.mark.parametrize("budget", [0, -1, True, "8192", 1.5])
def test_mcp_rejects_invalid_output_reservations_before_inference(serving, monkeypatch, budget):
    post = Mock()
    monkeypatch.setattr("overbae.services.inference_client.requests.post", post)
    result = infer(serving, max_tokens=budget)
    assert result["isError"]
    assert result["structuredContent"]["error"]["code"] == "invalid_input"
    post.assert_not_called()
    assert not InferenceCall.objects.filter(deployed_model=serving.model).exists()


def test_mcp_context_rejection_is_non_retryable_and_preserves_the_requested_budget(
    serving, monkeypatch
):
    post = Mock(
        return_value=Mock(
            ok=False, status_code=400, text="maximum context length exceeded; secret provider body"
        )
    )
    monkeypatch.setattr("overbae.services.inference_client.requests.post", post)
    result = infer(
        serving,
        max_tokens=16000,
        messages=[{"role": "user", "content": "context " * 1000}],
    )
    request_id = result["structuredContent"]["job"]["id"]
    monkeypatch.setattr(inference_requests, "spawn", lambda request: "fc-test")
    monkeypatch.setattr(
        inference_requests,
        "poll",
        lambda call_id: ("complete", {"error_code": "context_length_exceeded"}),
    )
    inference_requests.advance(request_id)
    inference_requests.advance(request_id)
    job = rpc(
        serving,
        "tools/call",
        {"name": "get_job", "arguments": {"kind": "inference_request", "id": request_id}},
    )["result"]["structuredContent"]
    assert job["status"] == "failed"
    assert job["job_error"] == "context_length_exceeded"
    assert job["details"]["retry_safe"] is False
    assert "secret provider body" not in json.dumps(job)
    assert InferenceRequest.objects.get(pk=request_id).payload["max_tokens"] == 16000


@pytest.mark.parametrize("budget", [16384, 20000])
def test_mcp_rejects_impossible_output_budget_without_waking_a_worker(serving, monkeypatch, budget):
    post = Mock()
    monkeypatch.setattr("overbae.services.inference_client.requests.post", post)
    result = infer(serving, max_tokens=budget)
    assert result["isError"]
    error = result["structuredContent"]["error"]
    assert error["code"] == "context_length_exceeded"
    assert error["retryable"] is False
    post.assert_not_called()


@pytest.mark.parametrize(
    "content,finish_reason,truncated,clipped",
    [
        ("partial", "length", True, False),
        ("x" * 32001, "stop", False, True),
    ],
)
def test_large_budgets_keep_generation_truncation_separate_from_response_clipping(
    serving, monkeypatch, content, finish_reason, truncated, clipped
):
    monkeypatch.setattr(
        "overbae.services.inference_client.requests.post",
        Mock(return_value=provider_response(content, finish_reason)),
    )
    result = infer(serving, max_tokens=12000)
    assert not result.get("isError"), result
    request_id = result["structuredContent"]["job"]["id"]
    monkeypatch.setattr(inference_requests, "spawn", lambda request: "fc-test")
    monkeypatch.setattr(
        inference_requests,
        "poll",
        lambda call_id: (
            "complete",
            {"content": content, "finish_reason": finish_reason, "latency_ms": 10},
        ),
    )
    inference_requests.advance(request_id)
    inference_requests.advance(request_id)
    output = rpc(
        serving,
        "tools/call",
        {"name": "get_job", "arguments": {"kind": "inference_request", "id": request_id}},
    )["result"]["structuredContent"]["details"]["result"]
    assert output["finish_reason"] == finish_reason
    assert output["truncated"] is truncated
    assert output["content_clipped"] is clipped
    assert output["content"] == content[:32000]


@pytest.mark.parametrize(
    "runners,recent,warming,expected",
    [
        (1, False, False, "warm"),
        (0, False, False, "asleep"),
        (0, False, True, "warming"),
        (None, False, False, "unknown"),
        (None, True, False, "warm"),
        (None, False, True, "warming"),
    ],
)
def test_read_only_mcp_deployment_exposes_current_worker_state(
    serving, monkeypatch, runners, recent, warming, expected
):
    serving.key, _ = APIToken.create_for_user(
        serving.user, project=serving.project, permission=["read"]
    )
    serving.model.gpu_type = "A100-80GB"
    serving.model.weights_path = "/weights/mcp-serving"
    serving.model.warming_started_at = timezone.now() if warming else None
    serving.model.save()
    if recent:
        InferenceCall.objects.create(deployed_model=serving.model, project=serving.project)
    stats = AsyncMock(
        return_value={
            "backlog": 0,
            "num_running_inputs": 0,
            "num_total_runners": runners,
            "available": True,
        }
    )
    if runners is None:
        stats.side_effect = RuntimeError("provider secret body")
    monkeypatch.setattr(inference_live, "_bounded_worker_stats", stats)
    result = rpc(
        serving,
        "resources/read",
        {"uri": f"overmind://deployments/{serving.model.pk}?period=24h&source=application"},
    )["result"]
    payload = json.loads(result["contents"][0]["text"])
    assert payload["status"] == "ready"
    assert payload["worker"]["state"] == expected
    assert payload["worker"]["available"] is (runners is not None)
    assert payload["worker"]["num_total_runners"] == runners
    assert payload["worker"]["recently_active"] is recent
    assert payload["metrics"]["request_count"] == 0
    assert "provider secret" not in json.dumps(payload)
    assert stats.await_count == 1


def test_worker_resource_checks_project_before_remote_measurements(serving, monkeypatch):
    foreign = Project.objects.create(name="Other", slug="other-serving")
    model = DeployedModel.objects.create(
        project=foreign, model_id="ft-foreign-serving", status="ready"
    )
    stats = AsyncMock()
    monkeypatch.setattr(inference_live, "_bounded_worker_stats", stats)
    result = rpc(serving, "resources/read", {"uri": f"overmind://deployments/{model.pk}"})
    assert result["error"]["code"] == 404
    stats.assert_not_awaited()


def test_activation_metadata_declares_external_verification(serving):
    result = rpc(serving, "tools/list", {})["result"]
    tool = next(tool for tool in result["tools"] if tool["name"] == "set_active_model")
    assert tool["annotations"]["openWorldHint"] is True


def test_job_poll_exposes_deployment_stage_and_status_change_time(serving):
    now = timezone.now()
    serving.model.status = "warming"
    serving.model.status_changed_at = now
    serving.model.deployment_stage = "warm"
    serving.model.deployment_deadline = now + timedelta(hours=4)
    serving.model.save()
    result = rpc(
        serving,
        "tools/call",
        {"name": "get_job", "arguments": {"kind": "deployment", "id": str(serving.model.id)}},
    )["result"]["structuredContent"]
    assert result["progress"]["stage"] == "warm"
    assert result["progress"]["deadline"] == serving.model.deployment_deadline.isoformat()
    assert result["updated_at"] == now.isoformat().replace("+00:00", "Z")


def test_activation_poll_exposes_deadline_without_claiming_worker_progress(serving):
    now = timezone.now()
    capability = Capability.objects.create(project=serving.project, name="Test", slug="test")
    activation = ModelActivation.objects.create(
        capability=capability,
        target=serving.model,
        stage="verifying",
        started_at=now,
        next_poll_at=now + timedelta(seconds=15),
        deadline=now + timedelta(minutes=50),
    )
    result = rpc(
        serving,
        "tools/call",
        {"name": "get_job", "arguments": {"kind": "model_activation", "id": str(activation.id)}},
    )["result"]["structuredContent"]
    assert result["progress"]["deadline"] == activation.deadline.isoformat()
    assert result["progress"]["next_poll_at"] == activation.next_poll_at.isoformat()
    assert result["updated_at"] is None
