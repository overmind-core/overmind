from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from starlette.testclient import TestClient

from overbae.models import MCPOAuthGrant, MCPOAuthToken, Project, ProjectMembership, User
from overbae.services.mcp.server import create_mcp_application

pytestmark = pytest.mark.django_db(transaction=True)
RESOURCE = "http://localhost:8000/api/mcp/"
CALLBACK = "https://client.example.test/callback"


@pytest.fixture
def flow(settings):
    settings.MCP_SERVER_URL = RESOURCE
    user = User.objects.create_user(email="oauth@test.com", password="pw", clerk_user_id="oauth")
    for name in ["Research", "Production"]:
        project = Project.objects.create(name=name, slug=name.lower())
        ProjectMembership.objects.create(project=project, user=user)
    api = APIClient()
    api.force_authenticate(user)
    with TestClient(create_mcp_application(), follow_redirects=False) as client:
        registered = client.post(
            "/register",
            json={
                "redirect_uris": [CALLBACK],
                "client_name": "Review client",
                "token_endpoint_auth_method": "none",
                "scope": "overmind:read overmind:write",
            },
        )
        assert registered.status_code == 201, registered.text
        yield client, api, user, registered.json()["client_id"]


def authorize(flow, *, scope="overmind:read", **overrides):
    client, api, _, client_id = flow
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    response = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": CALLBACK,
            "scope": scope,
            "state": "review-state",
            "resource": RESOURCE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **overrides,
        },
    )
    assert response.status_code == 302, response.text
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["request"][0]
    details = api.get("/api/mcp-oauth/consent/", {"request": request_id})
    assert details.status_code == 200, details.data
    approved = api.post(
        "/api/mcp-oauth/consent/", {"request": request_id, "approve": True}, format="json"
    )
    assert approved.status_code == 200, approved.data
    query = parse_qs(urlsplit(approved.data["redirect_url"]).query)
    assert query["state"] == ["review-state"]
    return {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": query["code"][0],
        "code_verifier": verifier,
        "redirect_uri": CALLBACK,
        "resource": RESOURCE,
    }


def mcp(client, access_token, name="list_projects", arguments=None):
    return client.post(
        "/api/mcp/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )


def test_oauth_discovery_consent_project_access_refresh_and_revoke(flow):
    client, _, _, client_id = flow
    challenge = client.post("/api/mcp/")
    assert challenge.status_code == 401
    assert "resource_metadata=" in challenge.headers["www-authenticate"]
    metadata = client.get("/.well-known/oauth-protected-resource/api/mcp/").json()
    assert metadata["resource"] == RESOURCE
    assert client.get("/.well-known/oauth-authorization-server").json()[
        "token_endpoint_auth_methods_supported"
    ] == ["none"]
    exchange = client.post("/token", data=authorize(flow))
    assert exchange.status_code == 200, exchange.text
    tokens = exchange.json()
    listed = mcp(client, tokens["access_token"]).json()["result"]["structuredContent"]
    assert {p["name"] for p in listed["projects"]} == {"Research", "Production"}
    denied = mcp(
        client,
        tokens["access_token"],
        "run_evaluation",
        {"project_id": listed["projects"][0]["id"]},
    )
    assert denied.json()["result"]["structuredContent"]["error"]["code"] == "permission_denied"
    refreshed = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
            "resource": RESOURCE,
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    tokens = refreshed.json()
    revoked = client.post(
        "/revoke",
        data={
            "client_id": client_id,
            "token": tokens["refresh_token"],
            "client_secret": "",
        },
    )
    assert revoked.status_code == 200, revoked.text
    assert mcp(client, tokens["access_token"]).status_code == 401


@pytest.mark.parametrize(
    "tampering",
    [
        {"resource": "https://another.example.test/api/mcp/"},
        {"code_verifier": "x" * 64},
        {"redirect_uri": "https://attacker.example.test/callback"},
    ],
)
def test_oauth_rejects_invalid_code_exchange_without_consuming_code(flow, tampering):
    client, _, _, _ = flow
    payload = authorize(flow)
    assert client.post("/token", data={**payload, **tampering}).status_code == 400
    assert client.post("/token", data=payload).status_code == 200
    assert client.post("/token", data=payload).status_code == 400


def test_refresh_replay_revokes_family_and_disabled_owner_loses_access(flow):
    client, _, user, client_id = flow
    tokens = client.post("/token", data=authorize(flow)).json()
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": tokens["refresh_token"],
        "resource": RESOURCE,
    }
    refreshed = client.post("/token", data=payload).json()
    assert client.post("/token", data=payload).status_code == 400
    assert mcp(client, refreshed["access_token"]).status_code == 401
    tokens = client.post("/token", data=authorize(flow)).json()
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert mcp(client, tokens["access_token"]).status_code == 401


def test_oauth_registration_rejects_unsafe_redirects(flow):
    client, _, _, _ = flow
    for uri in [
        "http://remote.example.test/callback",
        "https://client.test/#fragment",
        "https://user:pw@client.test/callback",
    ]:
        result = client.post(
            "/register", json={"redirect_uris": [uri], "token_endpoint_auth_method": "none"}
        )
        assert result.status_code == 400


def test_consent_requires_account_sign_in_and_cannot_be_replayed(flow):
    client, api, user, client_id = flow
    response = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": CALLBACK,
            "state": "denied-state",
            "resource": RESOURCE,
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
        },
    )
    raw = parse_qs(urlsplit(response.headers["location"]).query)["request"][0]
    anonymous = APIClient()
    assert anonymous.post(
        "/api/mcp-oauth/consent/", {"request": raw, "approve": True}
    ).status_code in {401, 403}
    user.is_guest = True
    user.save(update_fields=["is_guest"])
    assert api.get("/api/mcp-oauth/consent/", {"request": raw}).status_code == 403
    user.is_guest = False
    user.save(update_fields=["is_guest"])
    assert api.get("/api/mcp-oauth/consent/", {"request": raw}).status_code == 200
    other = User.objects.create_user(
        email="other-oauth@test.com", password="pw", clerk_user_id="other"
    )
    anonymous.force_authenticate(other)
    assert (
        anonymous.post("/api/mcp-oauth/consent/", {"request": raw, "approve": True}).status_code
        == 400
    )
    denied = api.post("/api/mcp-oauth/consent/", {"request": raw, "approve": False})
    query = parse_qs(urlsplit(denied.data["redirect_url"]).query)
    assert query == {"error": ["access_denied"], "state": ["denied-state"]}
    assert api.post("/api/mcp-oauth/consent/", {"request": raw, "approve": True}).status_code == 400


def test_expired_codes_tokens_and_wrong_resource_are_rejected(flow):
    client, _, _, _ = flow
    payload = authorize(flow)
    MCPOAuthGrant.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.post("/token", data=payload).status_code == 400
    tokens = client.post("/token", data=authorize(flow)).json()
    rest = APIClient()
    rest.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access_token']}")
    assert rest.get("/api/projects/").status_code in {401, 403}
    MCPOAuthToken.objects.update(access_expires_at=timezone.now() - timedelta(seconds=1))
    assert mcp(client, tokens["access_token"]).status_code == 401


def test_refresh_cannot_expand_scopes_or_change_client(flow):
    client, _, _, client_id = flow
    tokens = client.post("/token", data=authorize(flow)).json()
    second = client.post(
        "/register", json={"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"}
    ).json()
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": tokens["refresh_token"],
        "resource": RESOURCE,
    }
    assert client.post("/token", data={**payload, "scope": "overmind:write"}).status_code == 400
    assert (
        client.post("/token", data={**payload, "client_id": second["client_id"]}).status_code == 400
    )
    assert client.post("/token", data=payload).status_code == 200


def test_connection_refreshes_after_years_until_revoked(flow, monkeypatch):
    client, _, _, client_id = flow
    tokens = client.post("/token", data=authorize(flow)).json()
    approved_at = timezone.now()

    for days in [31, 3650]:
        future = approved_at + timedelta(days=days)
        with monkeypatch.context() as clock:
            clock.setattr("django.utils.timezone.now", lambda current=future: current)
            clock.setattr(
                "mcp.server.auth.handlers.token.time.time",
                lambda current=future: current.timestamp(),
            )
            assert mcp(client, tokens["access_token"]).status_code == 401
            refreshed = client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": tokens["refresh_token"],
                    "resource": RESOURCE,
                },
            )
            assert refreshed.status_code == 200, refreshed.text
            replacement = refreshed.json()
            assert replacement["refresh_token"] != tokens["refresh_token"]
            assert replacement["expires_in"] == 3600
            assert mcp(client, replacement["access_token"]).status_code == 200
            tokens = replacement

    assert (
        client.post(
            "/revoke",
            data={
                "client_id": client_id,
                "token": tokens["refresh_token"],
            },
        ).status_code
        == 200
    )
    assert mcp(client, tokens["access_token"]).status_code == 401
    assert (
        client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": tokens["refresh_token"],
                "resource": RESOURCE,
            },
        ).status_code
        == 400
    )
