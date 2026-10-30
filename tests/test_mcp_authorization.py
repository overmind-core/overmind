from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from mcp_fixtures import asgi_db
from starlette.testclient import TestClient

from overbae.models import APIToken, Project, ProjectMembership, User
from overbae.services.mcp.server import create_mcp_application

pytestmark = pytest.mark.django_db

MCP_URL = "/api/mcp/"


def _token(*, scope: dict | None = None, allowed_ips: list[str] | None = None):
    user = User.objects.create_user(
        email=f"mcp-auth-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="MCP", slug=f"mcp-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    raw, token = APIToken.create_for_user(user, project=project)
    if scope is not None:
        token.scope = scope
    if allowed_ips is not None:
        token.allowed_ips = allowed_ips
    if scope is not None or allowed_ips is not None:
        token.save(update_fields=["scope", "allowed_ips"])
    return raw, token


def _call(raw_key: str, headers: dict[str, str] | None = None):
    with TestClient(create_mcp_application()) as client:
        return client.post(
            MCP_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
            headers={
                "X-Api-Key": raw_key,
                "Accept": "application/json",
                **(headers or {}),
            },
        )


@asgi_db
def test_requires_api_key_and_rejects_jwt():
    response = _call("not-a-key")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"


@asgi_db
def test_unconfigured_oauth_does_not_advertise_a_bearer_challenge(settings):
    settings.MCP_SERVER_URL = ""
    with TestClient(create_mcp_application()) as client:
        response = client.post(
            MCP_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
            headers={"Accept": "application/json"},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
    assert "bearer" not in response.headers.get("www-authenticate", "").casefold()


@asgi_db
def test_rejects_account_scope_with_project_binding():
    raw, _ = _token(scope={"scope": "account", "permission": ["read", "write"]})
    response = _call(raw)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "project_required"


@asgi_db
def test_rejects_inactive_and_expired_keys():
    raw, token = _token()
    token.is_active = False
    token.save(update_fields=["is_active"])
    inactive = _call(raw)
    assert inactive.status_code == 401
    assert inactive.json()["error"]["code"] == "authentication_failed"

    raw, token = _token()
    token.expires_at = timezone.now() - timedelta(minutes=1)
    token.save(update_fields=["expires_at"])
    expired = _call(raw)
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "authentication_failed"


@asgi_db
def test_enforces_allowed_ips():
    raw, _ = _token(allowed_ips=["203.0.113.10"])
    response = _call(raw)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"


@asgi_db
def test_rejects_unknown_permissions():
    raw, token = _token()
    token.scope = {
        "scope": "project",
        "resourceIds": [str(token.project_id)],
        "permission": ["admin"],
    }
    token.save(update_fields=["scope"])

    response = _call(raw)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


@asgi_db
def test_accepts_bearer_api_key():
    raw, _ = _token()
    response = _call(raw, {"Authorization": f"Bearer {raw}"})
    assert response.status_code == 200
