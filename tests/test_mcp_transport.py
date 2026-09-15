from __future__ import annotations

import json
import uuid

import pytest
from starlette.testclient import TestClient

from overbae.models import APIToken, Capability, Project, ProjectMembership, Span, User
from overbae.services.mcp.server import create_mcp_application

pytestmark = pytest.mark.django_db(transaction=True)

MCP_URL = "/api/mcp/"


def _user() -> User:
    return User.objects.create_user(
        email=f"mcp-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )


def _token(*, permission: list[str] | None = None) -> tuple[str, APIToken]:
    user = _user()
    project = Project.objects.create(name="MCP", slug=f"mcp-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    return APIToken.create_for_user(user, project=project, permission=permission)


def _rpc(method: str, params: dict | None = None) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def _post(client: TestClient, raw_key: str, body: dict, **headers):
    return client.post(
        MCP_URL,
        json=body,
        headers={
            "X-Api-Key": raw_key,
            "Accept": "application/json",
            **headers,
        },
    )


def _application():
    return create_mcp_application()


def test_initialize_uses_official_streamable_http_transport():
    raw, _ = _token()
    with TestClient(_application()) as client:
        response = _post(
            client,
            raw,
            _rpc(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            ),
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"]["name"] == "overmind-platform"
    assert "tools" in result["capabilities"]


def test_ping_is_protocol_level_and_catalog_lists_curated_tools():
    raw, _ = _token()
    with TestClient(_application()) as client:
        response = _post(client, raw, _rpc("tools/list"))
        ping = _post(client, raw, _rpc("ping"))

    assert response.status_code == 200
    assert {tool["name"] for tool in response.json()["result"]["tools"]} == {
        "inspect_capability_health",
        "query_failures",
        "query_traces",
        "query_task_executions",
        "get_job",
        "list_datasets",
        "inspect_dataset",
        "query_dataset",
        "create_dataset_from_traces",
        "message_dataset_agent",
        "run_dataset",
        "check_evaluation_readiness",
        "upsert_evaluator",
        "run_evaluation",
        "compare_evaluations",
        "annotate_evaluation_sample",
        "check_finetune_readiness",
        "estimate_finetune",
        "start_finetune",
        "retry_deployment",
        "set_active_model",
        "run_inference",
        "get_model_swap_prompt",
        "check_optimizer_readiness",
        "start_optimizer",
        "inspect_optimizer_result",
        "inspect_connectors",
        "configure_connector",
        "sync_connector",
        "get_instrumentation_plan",
        "verify_instrumentation",
        "get_model_catalog",
    }
    assert ping.status_code == 200
    assert ping.json()["result"] == {}


def test_tools_list_is_permission_filtered_and_stays_within_manifest_budget():
    read_raw, _ = _token(permission=["read"])
    full_raw, _ = _token()
    with TestClient(_application()) as client:
        read_response = _post(client, read_raw, _rpc("tools/list"))
        full_response = _post(client, full_raw, _rpc("tools/list"))

    read_names = {tool["name"] for tool in read_response.json()["result"]["tools"]}
    full_tools = full_response.json()["result"]["tools"]

    assert read_response.status_code == 200
    assert full_response.status_code == 200
    assert read_names < {tool["name"] for tool in full_tools}
    assert len(full_tools) == 32
    assert len(full_response.content) <= 30 * 1024


def test_resources_and_prompts_list_over_streamable_http():
    raw, _ = _token()
    with TestClient(_application()) as client:
        resources = _post(client, raw, _rpc("resources/list"))
        prompts = _post(client, raw, _rpc("prompts/list"))

    assert resources.status_code == 200
    assert {item["name"] for item in resources.json()["result"]["resources"]} >= {
        "dataset-upload",
        "dataset-export",
    }
    assert prompts.status_code == 200
    assert {item["name"] for item in prompts.json()["result"]["prompts"]} >= {
        "upload-dataset-file",
    }


def test_tool_call_exposes_complete_json_and_resource_links_to_text_only_clients():
    raw, token = _token(permission=["read"])
    capability = Capability.objects.create(
        project=token.project,
        name="Support",
        slug=f"support-{uuid.uuid4().hex[:8]}",
    )
    trace_id = uuid.uuid4().hex
    root = Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        project=token.project,
        capability=capability,
        name="support request",
        start_time_ns=1,
        end_time_ns=2,
        duration_ns=1,
        status_code=1,
    )
    Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=root.span_id,
        project=token.project,
        attributes={"genai.total_tokens": 8, "genai.cost": 0.005},
    )

    with TestClient(_application()) as client:
        response = _post(
            client,
            raw,
            _rpc("tools/call", {"name": "query_traces", "arguments": {}}),
        )

    assert response.status_code == 200
    result = response.json()["result"]
    text = next(item["text"] for item in result["content"] if item["type"] == "text")
    visible = json.loads(text)
    assert visible == result["structuredContent"]
    assert visible["n"] == 1
    assert visible["traces"][0]["trace_id"] == trace_id
    assert visible["traces"][0]["total_tokens"] == 8
    assert visible["traces"][0]["total_cost"] == 0.005
    assert any(
        item["type"] == "resource_link" and item["uri"] == f"overmind://traces/{trace_id}"
        for item in result["content"]
    )


def test_initialize_accepts_an_allowed_host_with_a_port():
    raw, _ = _token()
    with TestClient(_application()) as client:
        response = _post(
            client,
            raw,
            _rpc("initialize", {"protocolVersion": "2025-03-26"}),
            Host="testserver:8123",
        )

    assert response.status_code == 200


def test_initialize_allows_any_host_when_allowed_hosts_is_star(settings):
    settings.ALLOWED_HOSTS = ["*"]
    raw, _ = _token()
    with TestClient(_application()) as client:
        response = _post(
            client,
            raw,
            _rpc(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            ),
            Host="api-staging.overmindlab.ai",
        )

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "overmind-platform"


def test_unknown_host_is_rejected():
    raw, _ = _token()
    with TestClient(_application()) as client:
        response = _post(
            client,
            raw,
            _rpc("initialize", {"protocolVersion": "2025-03-26"}),
            Host="evil.example",
        )

    assert response.status_code == 421
    assert response.json()["error"]["code"] == "invalid_request"


def test_unsupported_protocol_version_is_rejected():
    raw, _ = _token()
    with TestClient(_application()) as client:
        response = _post(
            client,
            raw,
            _rpc("initialize", {"protocolVersion": "2099-01-01"}),
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "protocol_version_unsupported"


def test_origin_is_validated_on_initialize():
    raw, _ = _token()
    with TestClient(_application()) as client:
        response = _post(
            client,
            raw,
            _rpc("initialize", {"protocolVersion": "2025-03-26"}),
            Origin="https://not-allowed.example",
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"
