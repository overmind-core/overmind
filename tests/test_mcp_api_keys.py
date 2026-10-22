"""create_api_key failure modes: a project credential minting an account key,
a guest minting any key, a read-only credential, an expiry outside the Console
choices, and a plaintext secret that is stored or returned twice."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from factories import make_project, make_user

from overbae.models import APIToken
from overbae.models.iam import API_KEY_EXPIRY_DAYS
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.projects import CreateApiKeyInput

pytestmark = pytest.mark.django_db(transaction=True)


def _context(*, account=False, permission=("read", "write"), guest=False):
    user = make_user(is_guest=guest)
    project = make_project(member=user)
    scope = {
        "scope": "account" if account else "project",
        "permission": list(permission),
    }
    if not account:
        scope["resourceIds"] = [str(project.id)]
    token = APIToken(user=user, project=None if account else project, scope=scope)
    return MCPContext(user=user, token=token, project=project)


def _call(context, **arguments):
    return asyncio.run(CATALOG.call("create_api_key", arguments, context))


def test_expiry_choices_match_the_console_dialog():
    schema = CreateApiKeyInput.model_json_schema()
    assert set(schema["properties"]["expires_in_days"]["enum"]) == set(API_KEY_EXPIRY_DAYS)


def test_create_api_key_returns_the_secret_once_and_stores_the_dialog_fields():
    context = _context()
    result = _call(
        context,
        name="CLI",
        description="api key for overmind",
        expires_in_days=30,
        project_id=str(context.project.id),
    )

    assert result.isError is False
    body = result.structuredContent
    assert json.loads(result.content[0].text) == body
    token = APIToken.objects.get(id=body["id"])
    assert body["key"].startswith("ovr_")
    assert token.token_hash == APIToken.hash_raw_key(body["key"])
    assert token.token_hash != body["key"]
    assert token.name == "CLI"
    assert token.description == "api key for overmind"
    assert token.project_id == context.project.id
    assert token.scope["scope"] == "project"
    assert token.scope["permission"] == ["read", "write"]
    assert timedelta(days=29) < token.expires_at - token.created_at < timedelta(days=31)
    assert body["prefix"] == token.prefix
    assert "key" not in token.scope


def test_account_connection_can_mint_an_account_key_that_does_not_expire():
    context = _context(account=True)
    result = _call(context, name="root", expires_in_days=0)
    pinned = _call(context, name="pinned", project_id=str(context.project.id))
    assert pinned.isError is False
    pinned_token = APIToken.objects.get(id=pinned.structuredContent["id"])
    assert pinned_token.project_id == context.project.id
    assert pinned_token.scope["scope"] == "project"

    assert result.isError is False
    body = result.structuredContent
    token = APIToken.objects.get(id=body["id"])
    assert token.project_id is None
    assert token.expires_at is None
    assert token.scope["scope"] == "account"
    assert body["project_id"] is None


def test_project_key_cannot_mint_an_account_key():
    context = _context()
    result = _call(context)
    other = make_project(member=context.user)
    foreign = _call(context, project_id=str(other.id))
    assert foreign.isError is True
    assert foreign.structuredContent["error"]["code"] == "permission_denied"

    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "permission_denied"
    assert APIToken.objects.filter(name="").count() == 0


def test_guest_and_read_only_connections_cannot_create_a_key():
    guest = _call(_context(guest=True), name="guest")
    assert guest.isError is True
    assert guest.structuredContent["error"]["code"] == "permission_denied"

    readonly = _call(_context(permission=("read",)), name="reader")
    assert readonly.isError is True
    assert readonly.structuredContent["error"]["code"] == "permission_denied"
    assert APIToken.objects.count() == 0


def test_expiry_outside_the_dialog_is_rejected():
    result = _call(_context(), expires_in_days=7)

    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "invalid_input"
    assert APIToken.objects.count() == 0
