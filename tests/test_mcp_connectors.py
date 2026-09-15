from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from django.utils import timezone

from overbae.models import (
    APIToken,
    Capability,
    ConnectorCredential,
    ConnectorSyncConfig,
    ConnectorSyncRun,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.mcp import tools_connectors
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.resources import read_resource, resource_templates

pytestmark = pytest.mark.django_db(transaction=True)


def _context(*, permission: str | list[str] = "read") -> MCPContext:
    user = User.objects.create_user(
        email=f"mcp-connectors-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="Connectors", slug=f"connectors-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    permissions = [permission] if isinstance(permission, str) else permission
    token = APIToken(
        scope={
            "scope": "project",
            "resourceIds": [str(project.id)],
            "permission": permissions,
        }
    )
    return MCPContext(user=user, token=token, project=project)


def _call(name: str, arguments: dict, context: MCPContext):
    return asyncio.run(CATALOG.call(name, arguments, context))


def _connector(context: MCPContext, *, configured: bool = True) -> ConnectorCredential:
    connector = ConnectorCredential.objects.create(
        project=context.project,
        name=f"Langfuse {uuid.uuid4().hex[:6]}",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key="provider-key",
        api_secret="provider-secret",
        verified_at=timezone.now(),
    )
    if configured:
        ConnectorSyncConfig.objects.create(
            credential=connector,
            version=1,
            source_project_id="source-project",
            lookback_days=7,
            effective_from=timezone.now(),
        )
    return connector


def test_catalog_has_three_connector_tools_without_secret_schema_fields():
    names = {definition.name for definition in CATALOG.definitions()}
    connector_names = {name for name in names if "connector" in name}
    assert connector_names == {"inspect_connectors", "configure_connector", "sync_connector"}
    forbidden = ("api_key", "api_secret", "password", "access_token", "provider_secret")
    for definition in CATALOG.definitions()[-3:]:
        schema = json.dumps(definition.as_mcp_tool().model_dump(mode="json")).lower()
        assert not any(field in schema for field in forbidden)


def test_inspect_without_configured_connectors_returns_integrations_action():
    context = _context()

    result = _call("inspect_connectors", {}, context)

    assert result.isError is False
    assert result.structuredContent["connectors"] == []
    assert result.structuredContent["human_action"] == {
        "code": "connector_setup_required",
        "message": "Open Integrations and complete the connector setup form, including credentials.",
        "frontend_route": "/integrations",
        "action": "complete_setup_form",
    }
    assert "authorization_url" not in result.structuredContent["human_action"]
    assert "state" not in result.structuredContent["human_action"]


def test_read_only_key_hides_and_denies_connector_writes():
    context = _context()
    connector = _connector(context)

    visible = {tool.name for tool in CATALOG.tools(frozenset({"read"}))}
    assert "inspect_connectors" in visible
    assert {"configure_connector", "sync_connector"}.isdisjoint(visible)
    for name in ("configure_connector", "sync_connector"):
        result = _call(name, {"connector": str(connector.id)}, context)
        assert result.isError is True
        assert result.structuredContent["error"]["code"] == "permission_denied"


def test_connector_lookup_is_project_scoped():
    context = _context(permission=["read", "write"])
    other = Project.objects.create(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    connector = ConnectorCredential.objects.create(
        project=other,
        name="Other connector",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key="provider-key",
    )

    result = _call("inspect_connectors", {"connector": str(connector.id)}, context)

    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "connector_not_found"


def test_configure_uses_existing_capabilities_and_rejects_unknown_targets():
    context = _context(permission=["read", "write"])
    connector = _connector(context)
    capability = Capability.objects.create(
        project=context.project,
        name="Support",
        slug=f"support-{uuid.uuid4().hex[:6]}",
    )

    result = _call(
        "configure_connector",
        {
            "connector": str(connector.id),
            "source_project_id": "provider-project",
            "lookback_days": 14,
            "capability_mapping": {
                "source": "observation_name",
                "names": ["support"],
                "assignments": {"support": str(capability.id)},
            },
        },
        context,
    )

    assert result.isError is False
    connector.refresh_from_db()
    assert connector.active_config().source_project_id == "provider-project"
    assert connector.capability_mapping["assignments"] == {"support": str(capability.id)}
    assert result.structuredContent["connector"]["capability_assignments"] == [
        {
            "source_value": "support",
            "capability_id": str(capability.id),
            "capability_name": "Support",
        }
    ]

    unknown = _call(
        "configure_connector",
        {
            "connector": str(connector.id),
            "capability_mapping": {
                "assignments": {"unknown": str(uuid.uuid4())},
            },
        },
        context,
    )
    auto_create = _call(
        "configure_connector",
        {
            "connector": str(connector.id),
            "capability_mapping": {"auto_create": True},
        },
        context,
    )
    assert unknown.structuredContent["error"]["code"] == "capability_not_found"
    assert auto_create.structuredContent["error"]["code"] == "invalid_input"


def test_sync_uses_neutral_dispatcher_and_returns_poll_resource(monkeypatch):
    context = _context(permission=["read", "write"])
    connector = _connector(context)
    dispatched = []
    monkeypatch.setattr(
        tools_connectors,
        "enqueue_connector_sync",
        lambda value: dispatched.append(value.id),
    )

    result = _call("sync_connector", {"connector": str(connector.id)}, context)

    assert result.isError is False
    assert dispatched == [connector.id]
    assert result.structuredContent["queued"] is True
    assert result.structuredContent["job"]["kind"] == "connector_sync"
    assert result.structuredContent["poll_hint"]["resource"]["uri"] == (
        f"overmind://connectors/{connector.id}"
    )


def test_provider_exceptions_are_redacted(monkeypatch):
    context = _context(permission=["read", "write"])
    connector = _connector(context)
    monkeypatch.setattr(
        tools_connectors,
        "get_adapter",
        lambda value: (_ for _ in ()).throw(RuntimeError("provider-secret-value")),
    )

    result = _call(
        "inspect_connectors",
        {
            "connector": str(connector.id),
            "include_source_projects": True,
            "preview": True,
        },
        context,
    )

    encoded = json.dumps(result.structuredContent)
    assert result.isError is False
    assert result.structuredContent["connector"]["provider_status"] == "unavailable"
    assert "provider-secret-value" not in encoded
    assert "provider-key" not in encoded
    assert "provider-secret" not in encoded


def test_connector_resource_is_project_scoped_and_secret_free():
    context = _context()
    connector = _connector(context)
    ConnectorSyncRun.objects.create(
        credential=connector,
        mode=ConnectorSyncRun.Mode.BACKFILL,
        status=ConnectorSyncRun.Status.FAILED,
        error="provider-secret-value",
    )

    async def read():
        with bind_context(context):
            contents = list(await read_resource(f"overmind://connectors/{connector.id}"))
        return json.loads(contents[0].content)

    resource = asyncio.run(read())
    encoded = json.dumps(resource)
    assert resource["kind"] == "connector"
    assert resource["sync_runs"][0]["has_error"] is True
    assert "provider-secret-value" not in encoded
    assert "provider-key" not in encoded
    assert "overmind://connectors/{connector}" in {
        template.uriTemplate for template in resource_templates()
    }
