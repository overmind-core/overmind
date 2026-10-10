import json
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone
from starlette.testclient import TestClient

from overbae.models import APIToken, DeployedModel, InferenceCall, Project, ProjectMembership, User
from overbae.services import inference_requests
from overbae.services.mcp.server import create_mcp_application

pytestmark = pytest.mark.django_db(transaction=True)


def test_disconnect_reconnect_reuses_inference_and_rejects_changed_payload(monkeypatch, settings):
    settings.STRIPE_SECRET_KEY = ""
    user = User.objects.create_user(email="receipt@test.com", password="pw")
    project = Project.objects.create(name="Receipt", slug="receipt")
    ProjectMembership.objects.create(user=user, project=project)
    key, _ = APIToken.create_for_user(user, project=project)
    model = DeployedModel.objects.create(project=project, model_id="receipt-model", status="ready")
    spawn = Mock(return_value="fc-recoverable")
    monkeypatch.setattr(inference_requests, "spawn", spawn)
    monkeypatch.setattr(inference_requests, "collect", lambda *args, **kwargs: None)

    def rpc(name, arguments):
        with TestClient(create_mcp_application()) as client:
            response = client.post(
                "/api/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                headers={"X-Api-Key": key, "Accept": "application/json"},
            )
        result = response.json()["result"]
        assert (
            json.loads(next(x["text"] for x in result["content"] if x["type"] == "text"))
            == result["structuredContent"]
        )
        return result

    arguments = {
        "deployment": str(model.pk),
        "request_key": "disconnect-test",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 32,
    }
    receipt = rpc("run_inference", arguments)["structuredContent"]["job"]
    assert receipt["kind"] == "inference_request"
    assert spawn.call_count == 0
    inference_requests.advance(receipt["id"])
    again = rpc("run_inference", arguments)["structuredContent"]["job"]
    assert again["id"] == receipt["id"]
    conflict = rpc("run_inference", {**arguments, "max_tokens": 64})
    assert conflict["isError"]
    assert conflict["structuredContent"]["error"]["code"] == "request_conflict"
    monkeypatch.setattr(
        inference_requests,
        "poll",
        lambda call_id: (
            "complete",
            {
                "content": "Hello",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "finish_reason": "stop",
                "latency_ms": 20,
            },
        ),
    )
    inference_requests.advance(receipt["id"])
    inference_requests.advance(receipt["id"])
    finished = rpc("get_job", {"kind": receipt["kind"], "id": receipt["id"]})["structuredContent"]
    assert finished["status"] == "succeeded"
    assert finished["details"]["result"]["content"] == "Hello"
    model.status = "failed"
    model.weights_path = "replacement-path"
    model.max_model_len = 16
    model.save()
    settings.STRIPE_SECRET_KEY = "test-billing-enabled"
    recovered = rpc("run_inference", arguments)
    assert not recovered.get("isError"), recovered
    assert recovered["structuredContent"]["job"]["id"] == receipt["id"]
    assert spawn.call_count == 1


def test_unknown_provider_acknowledgement_never_resubmits(monkeypatch):
    user = User.objects.create_user(email="unknown@test.com", password="pw")
    project = Project.objects.create(name="Unknown", slug="unknown")
    model = DeployedModel.objects.create(project=project, model_id="unknown-model", status="ready")
    request = inference_requests.submit(
        model,
        user,
        "unknown",
        {
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0,
            "max_tokens": 32,
        },
    )
    spawn = Mock(side_effect=TimeoutError)
    monkeypatch.setattr(inference_requests, "spawn", spawn)
    monkeypatch.setattr(inference_requests, "recover", lambda request: None)
    monkeypatch.setattr(inference_requests, "collect", lambda *args, **kwargs: None)
    inference_requests.advance(request.pk)
    inference_requests.advance(request.pk)
    request.refresh_from_db()
    assert request.state == "submission_unknown"
    assert spawn.call_count == 1


def test_expired_observation_can_resolve_without_resubmission(monkeypatch):
    project = Project.objects.create(name="Delayed", slug="delayed")
    model = DeployedModel.objects.create(project=project, model_id="delayed", status="ready")
    request = inference_requests.submit(model, None, "delayed", {"max_tokens": 32})
    request.state, request.remote_id = "running", "fc-delayed"
    request.deadline = timezone.now() - timedelta(seconds=1)
    request.save()
    monkeypatch.setattr(inference_requests, "collect", lambda *args, **kwargs: None)
    spawn = Mock(side_effect=AssertionError("Must not replay"))
    monkeypatch.setattr(inference_requests, "spawn", spawn)
    monkeypatch.setattr(inference_requests, "poll", lambda _: ("pending", None))
    inference_requests.advance(request.pk)
    request.refresh_from_db()
    assert request.state == "unresolved"
    assert request.completed_at is None
    monkeypatch.setattr(
        inference_requests,
        "poll",
        lambda _: (
            "complete",
            {
                "content": "late result",
                "finish_reason": "stop",
                "usage": {},
                "latency_ms": 20,
            },
        ),
    )
    inference_requests.advance(request.pk)
    request.refresh_from_db()
    assert request.state == "succeeded"
    assert not request.error_code
    assert spawn.call_count == 0


def test_terminal_receipt_recovers_missing_usage_without_repeating_provider_work(monkeypatch):
    project = Project.objects.create(name="Receipt recovery", slug="receipt-recovery")
    model = DeployedModel.objects.create(
        project=project, model_id="receipt-recovery", status="ready"
    )
    request = inference_requests.submit(model, None, "receipt-recovery", {"max_tokens": 32})
    request.state, request.completed_at = "succeeded", timezone.now()
    request.result = {"content": "ready", "usage": {"completion_tokens": 3}, "latency_ms": 20}
    request.save()
    monkeypatch.setattr(
        inference_requests, "spawn", Mock(side_effect=AssertionError("Must not replay"))
    )
    inference_requests.advance(request.pk)
    inference_requests.advance(request.pk)
    assert InferenceCall.objects.filter(pk=request.pk, completion_tokens=3).count() == 1
