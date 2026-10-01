from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient
from starlette.testclient import TestClient

from modal_shared.context_budget import DEFAULT_OUTPUT_TOKENS
from overbae.models import APIToken, DeployedModel, InferenceCall, Project, ProjectMembership, User
from overbae.services.mcp.server import create_mcp_application

pytestmark = pytest.mark.django_db(transaction=True)

INFERENCE = "http://inference.test"


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
                "messages": [{"role": "user", "content": "Hello"}],
                **overrides,
            },
        },
    )["result"]
    text = next(item["text"] for item in result["content"] if item["type"] == "text")
    assert json.loads(text) == result["structuredContent"]
    return result


def budgets(fake_llm) -> list[int]:
    return [r.body["max_tokens"] for r in fake_llm.requests if r.url.startswith(INFERENCE)]


@pytest.mark.parametrize(
    "budget", [{}, {"max_tokens": None}, {"max_tokens": 4096}, {"max_tokens": 12000}]
)
def test_mcp_and_production_preserve_the_same_output_reservation(serving, fake_llm, budget):
    result = infer(serving, **budget)
    assert not result.get("isError"), result

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
    mcp_budget, api_budget = budgets(fake_llm)
    assert mcp_budget == api_budget == (budget.get("max_tokens") or DEFAULT_OUTPUT_TOKENS)
    assert (
        InferenceCall.objects.filter(deployed_model=serving.model, outcome="succeeded").count() == 2
    )


@pytest.mark.parametrize("budget", [0, -1, True, "8192", 1.5])
def test_mcp_rejects_invalid_output_reservations_before_inference(serving, fake_llm, budget):
    result = infer(serving, max_tokens=budget)
    assert result["isError"]
    assert result["structuredContent"]["error"]["code"] == "invalid_input"
    assert budgets(fake_llm) == []
    assert not InferenceCall.objects.filter(deployed_model=serving.model).exists()


def test_mcp_context_rejection_is_non_retryable_and_preserves_the_requested_budget(
    serving, scripted
):
    inference = scripted(INFERENCE).reply(
        400, text="maximum context length exceeded; secret provider body"
    )
    result = infer(serving, max_tokens=16384)
    assert result["isError"]
    error = result["structuredContent"]["error"]
    assert error["code"] == "context_length_exceeded"
    assert error["retryable"] is False
    assert "reserved output" in error["message"]
    assert "secret provider body" not in json.dumps(result)
    [call] = inference.calls
    assert call.json["max_tokens"] == 16384
    call = InferenceCall.objects.get(deployed_model=serving.model)
    assert call.outcome == "failed"
    assert call.error_code == "context_length_exceeded"


@pytest.mark.parametrize(
    "content,finish_reason,truncated,clipped",
    [
        ("partial", "length", True, False),
        ("x" * 32001, "stop", False, True),
    ],
)
def test_large_budgets_keep_generation_truncation_separate_from_response_clipping(
    serving, fake_llm, content, finish_reason, truncated, clipped
):
    fake_llm.on(
        lambda r: r.url.startswith(INFERENCE),
        {"content": content, "finish_reason": finish_reason},
    )
    result = infer(serving, max_tokens=12000)
    assert not result.get("isError"), result
    output = result["structuredContent"]
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
    serving, fake_modal, runners, recent, warming, expected
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
    fake_modal.worker_stats = (
        RuntimeError("provider secret body")
        if runners is None
        else {"backlog": 0, "num_running_inputs": 0, "num_total_runners": runners}
    )
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
    assert fake_modal.called("") == ["stats"]


def test_worker_resource_checks_project_before_remote_measurements(serving, fake_modal):
    foreign = Project.objects.create(name="Other", slug="other-serving")
    model = DeployedModel.objects.create(
        project=foreign, model_id="ft-foreign-serving", status="ready"
    )
    model.gpu_type = "A100-80GB"
    model.weights_path = "/weights/foreign"
    model.save()
    result = rpc(serving, "resources/read", {"uri": f"overmind://deployments/{model.pk}"})
    assert result["error"]["code"] == 404
    assert fake_modal.log == []


def test_activation_metadata_declares_external_verification(serving):
    result = rpc(serving, "tools/list", {})["result"]
    tool = next(tool for tool in result["tools"] if tool["name"] == "set_active_model")
    assert tool["annotations"]["openWorldHint"] is True
