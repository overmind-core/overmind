from __future__ import annotations

import gzip
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from mcp.server.lowlevel import Server
from posthog import Posthog
from posthog.mcp import instrument
from starlette.testclient import TestClient

from overbae.models import APIToken, Project, ProjectMembership, User
from overbae.services.mcp import server

pytestmark = pytest.mark.django_db(transaction=True)

MCP_URL = "/api/mcp/"
INJECTED_ARGUMENTS = {"context", "conversation_id", "llm_model"}
PAYLOAD_PROPERTIES = {"$mcp_parameters", "$mcp_response", "$mcp_error_message"}


class _Ingest(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        self.server.events.extend(json.loads(body)["batch"])
        self.send_response(self.server.status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


@pytest.fixture
def ingest():
    httpd = HTTPServer(("127.0.0.1", 0), _Ingest)
    httpd.events = []
    httpd.status = 200
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


@pytest.fixture
def instrumented(ingest, monkeypatch):
    client = Posthog("phc_test", host=f"http://127.0.0.1:{ingest.server_port}", max_retries=0)
    # instrument() wraps a server object once per process, so each test gets a copy.
    copy = Server(
        server.SERVER_NAME,
        version=server.SERVER_VERSION,
        instructions=server.mcp_server.instructions,
    )
    copy.request_handlers = dict(server.mcp_server.request_handlers)
    copy.notification_handlers = dict(server.mcp_server.notification_handlers)
    monkeypatch.setattr(server, "mcp_server", copy)
    monkeypatch.setattr(server, "posthog_client", client)
    monkeypatch.setattr(server, "mcp_analytics", instrument(copy, client, server.ANALYTICS_OPTIONS))
    return ingest


def _member(*, project_key: bool):
    user = User.objects.create_user(
        email=f"mcp-analytics-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"user_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="Analytics", slug=f"analytics-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    raw, _ = APIToken.create_for_user(
        user, project=project if project_key else None, permission=["read", "write"]
    )
    return user, project, {"X-Api-Key": raw, "Accept": "application/json"}


def _rpc(client, headers, method, params=None, request_id=1):
    response = client.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response


def test_every_tool_call_reaches_posthog_as_metadata_only(instrumented):
    user, project, headers = _member(project_key=False)
    expected = {}

    with TestClient(server.create_mcp_application()) as client:
        initialize = _rpc(
            client,
            headers,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "analytics-e2e", "version": "1"},
            },
        )
        headers["Mcp-Session-Id"] = initialize.headers["mcp-session-id"]
        tools = _rpc(client, headers, "tools/list").json()["result"]["tools"]
        for tool in tools:
            assert not INJECTED_ARGUMENTS & set(tool["inputSchema"].get("properties", {}))

        listed = _rpc(client, headers, "tools/call", {"name": "list_projects", "arguments": {}})
        assert listed.json()["result"].get("isError") is not True
        expected["list_projects"] = [(False, None, None)]
        # An unknown field fails contract validation before any handler runs.
        for index, tool in enumerate(tools):
            result = _rpc(
                client,
                headers,
                "tools/call",
                {
                    "name": tool["name"],
                    "arguments": {"project_id": str(project.id), "probe": "secret text"},
                },
                request_id=10 + index,
            ).json()["result"]
            assert result["isError"] is True
            expected.setdefault(tool["name"], []).append((True, str(project.id), "invalid_input"))
        _rpc(
            client,
            headers,
            "resources/read",
            {"uri": f"overmind://project/current?project_id={project.id}"},
        )

    for event in instrumented.events:
        assert not PAYLOAD_PROPERTIES & set(event["properties"]), event["event"]
        assert "secret text" not in json.dumps(event)
    captured = {}
    for event in instrumented.events:
        if event["event"] != "$mcp_tool_call":
            continue
        properties = event["properties"]
        assert properties["$mcp_server_name"] == "overmind-platform"
        assert properties["$mcp_client_name"] == "analytics-e2e"
        assert event["distinct_id"] == user.clerk_user_id
        captured.setdefault(properties["$mcp_tool_name"], []).append(
            (
                properties["$mcp_is_error"],
                properties.get("project_id"),
                properties.get("error_code"),
            )
        )
    assert {name: sorted(calls, key=str) for name, calls in captured.items()} == {
        name: sorted(calls, key=str) for name, calls in expected.items()
    }
    session_ids = {event["properties"]["$session_id"] for event in instrumented.events}
    assert len(session_ids) == 1
    events = {event["event"]: event for event in instrumented.events}
    assert {"$mcp_initialize", "$mcp_tools_list", "$mcp_resource_read"} <= set(events)
    assert "$exception" not in events
    assert events["$mcp_resource_read"]["properties"]["project_id"] == str(project.id)


def test_project_key_events_carry_the_key_project(instrumented):
    _, project, headers = _member(project_key=True)

    with TestClient(server.create_mcp_application()) as client:
        _rpc(client, headers, "tools/call", {"name": "list_projects", "arguments": {}})

    (event,) = [event for event in instrumented.events if event["event"] == "$mcp_tool_call"]
    assert event["properties"]["project_id"] == str(project.id)


def test_failed_ingest_does_not_fail_tool_calls(instrumented):
    instrumented.status = 500
    _, _, headers = _member(project_key=True)

    with TestClient(server.create_mcp_application()) as client:
        result = _rpc(
            client, headers, "tools/call", {"name": "list_projects", "arguments": {}}
        ).json()["result"]

    assert result.get("isError") is not True
    assert result["structuredContent"]["total"] == 1
