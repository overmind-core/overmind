from __future__ import annotations

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from django.core.cache import cache
from django.utils import timezone
from mcp_fixtures import asgi_db
from rest_framework.test import APIClient
from starlette.testclient import TestClient

from overbae.models import (
    APIToken,
    DeployedModel,
    InferenceCall,
    InferenceRequest,
    Project,
    ProjectMembership,
    User,
)
from overbae.services import inference_live, inference_requests
from overbae.services.mcp.server import create_mcp_application

pytestmark = pytest.mark.django_db


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


@asgi_db
def test_decision_rest_and_mcp_share_typed_durable_inference(serving, fake_modal):
    model = serving.model
    model.inference_contract = "decision"
    model.serving_runtime = {"app": "decision-test", "environment": "test"}
    model.checkpoint_hash = "a" * 64
    model.save()
    decision = {
        "state": "passenger",
        "question": "Survived?",
        "kind": "choice",
        "options": ["No", "Yes"],
    }
    payload = {"request_key": "native", "decisions": [decision]}
    api = APIClient()
    api.force_authenticate(user=serving.user, token=serving.token)
    response = api.post(f"/api/deployed-models/{model.pk}/inference/", payload, format="json")
    assert response.status_code == 202, response.data
    receipt = InferenceRequest.objects.get(pk=response.data["id"])
    assert receipt.payload["serving_app"] == "decision-test"
    assert "max_tokens" not in receipt.payload
    result = rpc(
        serving,
        "tools/call",
        {"name": "run_inference", "arguments": {"deployment": str(model.pk), **payload}},
    )["result"]
    assert not result.get("isError"), result
    assert result["structuredContent"]["job"]["id"] == str(receipt.pk)
    predictions = [{"options": ["No", "Yes"], "probabilities": [0.2, 0.8]}]
    fake_modal.deploy(
        "decision-test",
        "inference_request",
        lambda request_id, payload: {
            "predictions": predictions,
            "model_identity": "a" * 64,
            "usage": {"prompt_tokens": 10, "completion_tokens": 0},
            "finish_reason": "stop",
            "latency_ms": 12,
        },
    )
    inference_requests.advance(receipt.pk)
    inference_requests.advance(receipt.pk)
    response = api.get(f"/api/inference-requests/{receipt.pk}/")
    assert response.status_code == 200
    assert response.data["status"] == "succeeded"
    assert response.data["result"]["predictions"] == predictions
    assert InferenceCall.objects.filter(pk=receipt.pk).count() == 1
    bad = api.post(
        f"/api/deployed-models/{model.pk}/inference/",
        {**payload, "decisions": [{**decision, "target_probabilities": [0, 1]}]},
        format="json",
    )
    assert bad.status_code == 400
    assert infer(serving)["isError"]
    other = Project.objects.create(name="Other decision project", slug="other-decision")
    DeployedModel.objects.filter(pk=model.pk).update(project=other)
    InferenceRequest.objects.filter(pk=receipt.pk).update(project=other)
    assert api.get(f"/api/inference-requests/{receipt.pk}/").status_code == 404
    assert (
        api.post(f"/api/deployed-models/{model.pk}/inference/", payload, format="json").status_code
        == 404
    )


@asgi_db
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


@asgi_db
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


@asgi_db
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
