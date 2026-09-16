from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from overbae.models import (
    APIToken,
    Capability,
    ConnectorCredential,
    ConnectorSyncConfig,
    ConnectorSyncRun,
    Project,
    ProjectMembership,
    Span,
    User,
)
from overbae.services.connectors.base import SourceProject, VerifyResult
from overbae.services.connectors.langfuse.mapping import LANGFUSE
from overbae.services.connectors.records import ObservationRecord
from overbae.services.connectors.schema import CONNECTOR_CREDENTIAL_ID_ATTR
from overbae.services.connectors.sync import (
    boundary_import_key,
    prepare_connector_sync,
    reset_connector_import,
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


def _connector(
    context: MCPContext, *, configured: bool = True, mapping_confirmed: bool | None = None
) -> ConnectorCredential:
    connector = ConnectorCredential.objects.create(
        project=context.project,
        name=f"Langfuse {uuid.uuid4().hex[:6]}",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key="provider-key",
        api_secret="provider-secret",
        verified_at=timezone.now(),
        capability_mapping_confirmed=configured if mapping_confirmed is None else mapping_confirmed,
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
    definitions = {definition.name: definition for definition in CATALOG.definitions()}
    for name in ("inspect_connectors", "configure_connector", "sync_connector"):
        schema = json.dumps(definitions[name].as_mcp_tool().model_dump(mode="json")).lower()
        assert not any(field in schema for field in forbidden)


def _assert_available_types(payload: dict) -> None:
    types = payload["available_types"]
    assert [item["connector_type"] for item in types[:4]] == [
        "langfuse",
        "langsmith",
        "braintrust",
        "galileo",
    ]
    assert types[0]["auth"] == "pair"
    assert types[1]["auth"] == "bearer"
    for item in types:
        assert item["command"] == f"overmind connector add {item['connector_type']} --json"
        assert "TYPE" not in item["command"]
        assert "needs_secret" not in item
        assert "api_key" not in item
        assert "frontend_route" not in item


class _ListAdapter:
    def list_source_projects(self):
        return [SourceProject(id="proj-1", name="Demo")]

    def count(self, **kwargs):
        return 3


class _VerifyAdapter:
    def verify(self):
        return VerifyResult(ok=True, api_version="v2")


def _obs(**kwargs) -> ObservationRecord:
    values = {
        "id": "obs",
        "trace_id": "trace",
        "parent_observation_id": None,
        "type": "SPAN",
        "name": "span",
        "start_time": "2026-01-02T00:00:00Z",
        "end_time": "2026-01-02T00:00:01Z",
        "is_root_observation": False,
    }
    values.update(kwargs)
    return ObservationRecord(**values)


def _nested_agent_traces() -> list[list[ObservationRecord]]:
    return [
        [
            _obs(id="scan", name="run_ledgerline", is_root_observation=True),
            _obs(id="agent", name="adjudicate_claim", parent_observation_id="scan"),
            _obs(id="loop", name="adjudicate_tool_loop", parent_observation_id="agent"),
            _obs(id="fx", name="get_fx_rate", type="TOOL", parent_observation_id="loop"),
        ]
    ]


class _ShapeAdapter:
    conventions = LANGFUSE

    def list_source_projects(self):
        return [SourceProject(id="proj-1", name="Demo")]

    def count(self, **kwargs):
        return 3

    def sample_units(self, **kwargs):
        return _nested_agent_traces()


def test_inspect_without_configured_connectors_returns_cli_action():
    context = _context()

    result = _call("inspect_connectors", {}, context)
    action = result.structuredContent["human_action"]

    assert result.isError is False
    assert result.structuredContent["connectors"] == []
    assert action["code"] == "connector_setup_required"
    assert action["action"] == "run_cli"
    assert action["command"] == "overmind connector add langfuse --json"
    assert action["resource"] == "overmind://connector-setup"
    assert "frontend_route" not in action
    assert "Open Integrations" not in action["message"]
    assert "Do not run the CLI" in action["message"]
    assert result.structuredContent["console_traces_url"].endswith(
        f"/observability?projectId={context.project.id}"
    )
    _assert_available_types(result.structuredContent)


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
    assert connector.capability_mapping == {}
    assert connector.pending_capability_mapping["assignments"] == {"support": str(capability.id)}
    assert "auto_create" not in connector.pending_capability_mapping
    assert result.structuredContent["mapping_pending"] is True
    assert result.structuredContent["human_action"]["code"] == "connector_mapping_approval_required"
    assert result.structuredContent["proposed_assignments"] == [
        {
            "source_value": "support",
            "capability_id": str(capability.id),
            "capability_name": "Support",
        }
    ]
    assert any(
        item["id"] == str(capability.id) for item in result.structuredContent["mapping_options"]
    )

    confirmed = _call(
        "configure_connector",
        {"connector": str(connector.id), "confirm_mapping": True},
        context,
    )
    connector.refresh_from_db()
    assert confirmed.structuredContent["mapping_pending"] is False
    assert connector.capability_mapping["assignments"] == {"support": str(capability.id)}
    assert connector.capability_mapping_confirmed is True
    assert connector.pending_capability_mapping == {}
    assert confirmed.structuredContent["connector"]["capability_assignments"] == [
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


def test_list_shows_keyed_drafts_and_hides_keyless_wizard_rows():
    context = _context()
    keyed = _connector(context, configured=False)
    ConnectorCredential.objects.create(
        project=context.project,
        name="wizard-draft",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key="",
        api_secret="",
    )

    result = _call("inspect_connectors", {"include_source_projects": True}, context)
    ids = {item["id"] for item in result.structuredContent["connectors"]}
    action = result.structuredContent["human_action"]

    assert ids == {str(keyed.id)}
    assert result.structuredContent["connectors"][0]["provider_status"] == "not_requested"
    assert result.structuredContent["connectors"][0]["source_projects"] == []
    assert action["code"] == "connector_config_required"
    assert action["action"] == "configure_connector"
    assert not action.get("command")
    _assert_available_types(result.structuredContent)


def test_inspect_keyed_draft_can_request_source_projects(monkeypatch):
    context = _context()
    connector = _connector(context, configured=False)
    monkeypatch.setattr(tools_connectors, "get_adapter", lambda value: _ListAdapter())

    result = _call(
        "inspect_connectors",
        {"connector": str(connector.id), "include_source_projects": True},
        context,
    )
    details = result.structuredContent["connector"]
    action = result.structuredContent["human_action"]

    assert result.isError is False
    assert details["provider_status"] == "available"
    assert details["source_projects"] == [{"id": "proj-1", "name": "Demo"}]
    assert action["code"] == "connector_config_required"
    assert action["action"] == "configure_connector"


def test_configure_first_config_defaults_lookback_and_leaves_auto_sync_off():
    context = _context(permission=["read", "write"])
    connector = _connector(context, configured=False)

    result = _call(
        "configure_connector",
        {"connector": str(connector.id), "auto_sync_enabled": True},
        context,
    )
    connector.refresh_from_db()
    config = connector.active_config()

    assert result.isError is False
    assert result.structuredContent["configured"] is True
    assert result.structuredContent["mapping_pending"] is True
    assert result.structuredContent["human_action"]["code"] == "connector_mapping_approval_required"
    assert config is not None
    assert config.lookback_days == 30
    assert connector.auto_sync_enabled is False


def test_configure_explicit_null_lookback_stays_unbounded():
    context = _context(permission=["read", "write"])
    connector = _connector(context, configured=False)

    result = _call(
        "configure_connector",
        {"connector": str(connector.id), "lookback_days": None},
        context,
    )
    connector.refresh_from_db()

    assert result.isError is False
    assert connector.active_config().lookback_days is None


def test_sync_without_config_returns_config_required():
    context = _context(permission=["read", "write"])
    connector = _connector(context, configured=False)

    result = _call("sync_connector", {"connector": str(connector.id)}, context)
    action = result.structuredContent["human_action"]

    assert result.isError is False
    assert result.structuredContent["queued"] is False
    assert action["code"] == "connector_config_required"
    assert action["action"] == "configure_connector"
    assert not action.get("command")


def test_sync_with_empty_source_returns_source_required():
    context = _context(permission=["read", "write"])
    connector = _connector(context, configured=False)
    _call(
        "configure_connector",
        {"connector": str(connector.id), "lookback_days": 7},
        context,
    )

    result = _call("sync_connector", {"connector": str(connector.id)}, context)
    action = result.structuredContent["human_action"]

    assert result.isError is False
    assert result.structuredContent["queued"] is False
    assert action["code"] == "connector_source_required"
    assert action["action"] == "configure_connector"
    assert not action.get("command")


def test_configure_and_sync_without_keys_still_require_cli():
    context = _context(permission=["read", "write"])
    connector = ConnectorCredential.objects.create(
        project=context.project,
        name="empty",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key="",
        api_secret="",
    )

    configure = _call("configure_connector", {"connector": str(connector.id)}, context)
    sync = _call("sync_connector", {"connector": str(connector.id)}, context)

    for result in (configure, sync):
        action = result.structuredContent["human_action"]
        assert result.isError is False
        assert action["code"] == "connector_setup_required"
        assert action["action"] == "run_cli"
        assert action["command"] == "overmind connector add langfuse --json"
        assert action["resource"] == "overmind://connector-setup"


def test_create_stamps_verified_at_on_saved_and_reconnected_row(monkeypatch):
    context = _context()
    raw, _ = APIToken.create_for_user(context.user, project=context.project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw)
    monkeypatch.setattr("overbae.services.connectors.get_adapter", lambda cred: _VerifyAdapter())
    body = {
        "project": str(context.project.id),
        "name": "langfuse",
        "connector_type": "langfuse",
        "api_key": "pk-test",
        "api_secret": "sk-test",
    }

    created = client.post("/api/connector-credentials/", body, format="json")
    assert created.status_code == 201, created.data
    connector = ConnectorCredential.objects.get(id=created.data["id"])
    assert connector.verified_at is not None
    assert connector.api_version == "v2"

    connector.verified_at = None
    connector.api_version = ConnectorCredential.ApiVersion.UNKNOWN
    connector.save(update_fields=["verified_at", "api_version", "updated_at"])

    again = client.post("/api/connector-credentials/", body, format="json")
    assert again.status_code == 201, again.data
    assert again.data["id"] == str(connector.id)
    connector.refresh_from_db()
    assert connector.verified_at is not None
    assert connector.api_version == "v2"


def test_create_rejects_project_outside_api_key_scope(monkeypatch):
    context = _context()
    other = Project.objects.create(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    raw, _ = APIToken.create_for_user(context.user, project=context.project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw)
    monkeypatch.setattr("overbae.services.connectors.get_adapter", lambda cred: _VerifyAdapter())

    response = client.post(
        "/api/connector-credentials/",
        {
            "project": str(other.id),
            "name": "langfuse",
            "connector_type": "langfuse",
            "api_key": "pk-test",
            "api_secret": "sk-test",
        },
        format="json",
    )

    assert response.status_code == 400
    assert "scoped to another project" in str(response.data)


def test_mapping_confirm_on_the_first_call_only_stages():
    context = _context(permission=["read", "write"])
    connector = _connector(context, mapping_confirmed=False)
    capability = Capability.objects.create(
        project=context.project,
        name="Support",
        slug=f"support-{uuid.uuid4().hex[:6]}",
    )

    result = _call(
        "configure_connector",
        {
            "connector": str(connector.id),
            "capability_mapping": {"assignments": {"support": str(capability.id)}},
            "confirm_mapping": True,
        },
        context,
    )
    connector.refresh_from_db()

    assert result.structuredContent["mapping_pending"] is True
    assert result.structuredContent["human_action"]["action"] == "approve_mapping"
    assert connector.capability_mapping == {}
    assert connector.capability_mapping_confirmed is False
    assert connector.pending_capability_mapping["assignments"] == {"support": str(capability.id)}


def test_sync_waits_for_mapping_approval(monkeypatch):
    context = _context(permission=["read", "write"])
    connector = _connector(context, mapping_confirmed=False)
    monkeypatch.setattr(tools_connectors, "enqueue_connector_sync", lambda value: None)

    result = _call("sync_connector", {"connector": str(connector.id)}, context)

    assert result.isError is False
    assert result.structuredContent["queued"] is False
    assert result.structuredContent["human_action"]["code"] == (
        "connector_mapping_approval_required"
    )


def test_empty_mapping_can_be_approved_then_synced(monkeypatch):
    context = _context(permission=["read", "write"])
    connector = _connector(context, mapping_confirmed=False)
    dispatched = []
    monkeypatch.setattr(
        tools_connectors,
        "enqueue_connector_sync",
        lambda value: dispatched.append(value.id),
    )

    preview = _call(
        "configure_connector",
        {"connector": str(connector.id), "capability_mapping": {"assignments": {}}},
        context,
    )
    confirm = _call(
        "configure_connector",
        {"connector": str(connector.id), "confirm_mapping": True},
        context,
    )
    sync = _call("sync_connector", {"connector": str(connector.id)}, context)
    connector.refresh_from_db()

    assert preview.structuredContent["mapping_pending"] is True
    assert confirm.structuredContent["mapping_pending"] is False
    assert connector.capability_mapping_confirmed is True
    assert sync.structuredContent["queued"] is True
    assert dispatched == [connector.id]


def test_inspect_returns_parent_only_suggested_boundaries(monkeypatch):
    context = _context()
    connector = _connector(context)
    capability = Capability.objects.create(
        project=context.project,
        name="Ledgerline Adjudicator",
        slug="ledgerline-adjudicator",
        entrypoint_fn="adjudicate_claim",
        improvement_metadata={"tool_spec": [{"name": "get_fx_rate"}]},
    )
    monkeypatch.setattr(tools_connectors, "get_adapter", lambda value: _ShapeAdapter())

    result = _call(
        "inspect_connectors",
        {"connector": str(connector.id), "include_source_projects": True},
        context,
    )
    suggested = result.structuredContent["suggested_boundaries"]
    nested = suggested[0]["nested_names"]

    assert result.isError is False
    assert [item["name"] for item in suggested] == ["adjudicate_claim"]
    assert suggested[0]["capability_id"] == str(capability.id)
    assert suggested[0]["alternatives"] == []
    assert "get_fx_rate" in nested
    assert "adjudicate_tool_loop" in nested
    assert "get_fx_rate" not in [item["name"] for item in suggested]
    assert "run_ledgerline" in result.structuredContent["unmapped_roots"]
    assert any(
        shape["name"] == "get_fx_rate" for shape in result.structuredContent["observation_shapes"]
    )


def test_configure_drops_nested_names_from_the_staged_mapping(monkeypatch):
    context = _context(permission=["read", "write"])
    connector = _connector(context, mapping_confirmed=False)
    capability = Capability.objects.create(
        project=context.project,
        name="Ledgerline Adjudicator",
        slug="ledgerline-adjudicator",
        entrypoint_fn="adjudicate_claim",
        improvement_metadata={"tool_spec": [{"name": "get_fx_rate"}]},
    )
    monkeypatch.setattr(tools_connectors, "get_adapter", lambda value: _ShapeAdapter())

    result = _call(
        "configure_connector",
        {
            "connector": str(connector.id),
            "capability_mapping": {
                "source": "observation_name",
                "names": ["adjudicate_claim", "get_fx_rate", "adjudicate_tool_loop"],
                "assignments": {
                    "adjudicate_claim": str(capability.id),
                    "get_fx_rate": str(capability.id),
                    "adjudicate_tool_loop": str(capability.id),
                },
            },
        },
        context,
    )
    connector.refresh_from_db()
    pending = connector.pending_capability_mapping

    assert result.isError is False
    assert pending["names"] == ["adjudicate_claim"]
    assert pending["assignments"] == {"adjudicate_claim": str(capability.id)}
    assert set(result.structuredContent["dropped_nested_names"]) == {
        "get_fx_rate",
        "adjudicate_tool_loop",
    }
    assert [item["source_value"] for item in result.structuredContent["proposed_assignments"]] == [
        "adjudicate_claim"
    ]


def test_configure_without_mapping_stages_suggested_parents(monkeypatch):
    context = _context(permission=["read", "write"])
    connector = _connector(context, mapping_confirmed=False)
    capability = Capability.objects.create(
        project=context.project,
        name="Ledgerline Adjudicator",
        slug="ledgerline-adjudicator",
        entrypoint_fn="adjudicate_claim",
    )
    monkeypatch.setattr(tools_connectors, "get_adapter", lambda value: _ShapeAdapter())

    result = _call(
        "configure_connector",
        {"connector": str(connector.id), "source_project_id": "proj-1"},
        context,
    )
    connector.refresh_from_db()

    assert result.isError is False
    assert connector.pending_capability_mapping["names"] == ["adjudicate_claim"]
    assert connector.pending_capability_mapping["assignments"] == {
        "adjudicate_claim": str(capability.id)
    }
    assert result.structuredContent["mapping_pending"] is True
    assert "Stop." in result.structuredContent["human_action"]["message"]
    assert "alternatives" in result.structuredContent["human_action"]["message"]


class _InboxAdapter:
    conventions = LANGFUSE

    def list_source_projects(self):
        return [SourceProject(id="proj-1", name="Demo")]

    def count(self, **kwargs):
        return 3

    def sample_units(self, **kwargs):
        return [
            [
                _obs(id="root", name="run_ledgerline", is_root_observation=True),
                _obs(id="agent", name="run_invoice_agent", parent_observation_id="root"),
                _obs(id="email", name="analyze_email", parent_observation_id="agent"),
            ]
        ]


def test_configure_keeps_fallback_and_does_not_store_auto_create():
    context = _context(permission=["read", "write"])
    connector = _connector(context, mapping_confirmed=False)
    capability = Capability.objects.create(
        project=context.project,
        name="Invoice triage",
        slug=f"invoice-triage-{uuid.uuid4().hex[:6]}",
    )

    result = _call(
        "configure_connector",
        {
            "connector": str(connector.id),
            "capability_mapping": {
                "source": "observation_name",
                "names": ["analyze-email"],
                "assignments": {"analyze-email": str(capability.id)},
                "fallback_capability_id": str(capability.id),
            },
        },
        context,
    )
    connector.refresh_from_db()
    pending = connector.pending_capability_mapping

    assert result.isError is False
    assert pending["fallback_capability_id"] == str(capability.id)
    assert pending["assignments"]["analyze-email"] == str(capability.id)
    assert "auto_create" not in pending


def test_configure_keeps_unmapped_root_and_nested_only_boundaries(monkeypatch):
    context = _context(permission=["read", "write"])
    connector = _connector(context, mapping_confirmed=False)
    triage = Capability.objects.create(
        project=context.project,
        name="Invoice triage",
        slug="invoice-triage",
        entrypoint_fn="run_invoice_agent",
        improvement_metadata={"modes": [{"name": "analyze_email"}]},
    )
    monkeypatch.setattr(tools_connectors, "get_adapter", lambda value: _InboxAdapter())

    nested = _call(
        "configure_connector",
        {
            "connector": str(connector.id),
            "capability_mapping": {
                "source": "observation_name",
                "names": ["analyze_email"],
                "assignments": {"analyze_email": str(triage.id)},
            },
        },
        context,
    )
    connector.refresh_from_db()

    assert nested.isError is False
    assert connector.pending_capability_mapping["names"] == ["analyze_email"]
    assert nested.structuredContent["dropped_nested_names"] == []

    root = _call(
        "configure_connector",
        {
            "connector": str(connector.id),
            "capability_mapping": {
                "source": "observation_name",
                "names": ["run_ledgerline"],
                "assignments": {"run_ledgerline": str(triage.id)},
            },
        },
        context,
    )
    connector.refresh_from_db()

    assert root.isError is False
    assert connector.pending_capability_mapping["names"] == ["run_ledgerline"]


def test_inspect_lists_nested_capability_matches_as_alternatives(monkeypatch):
    context = _context()
    connector = _connector(context)
    capability = Capability.objects.create(
        project=context.project,
        name="Invoice triage",
        slug="invoice-triage",
        entrypoint_fn="run_invoice_agent",
        improvement_metadata={"modes": [{"name": "analyze_email"}]},
    )
    monkeypatch.setattr(tools_connectors, "get_adapter", lambda value: _InboxAdapter())

    result = _call(
        "inspect_connectors",
        {"connector": str(connector.id), "include_source_projects": True},
        context,
    )
    suggested = result.structuredContent["suggested_boundaries"]

    assert result.isError is False
    assert [item["name"] for item in suggested] == ["run_invoice_agent"]
    assert suggested[0]["capability_id"] == str(capability.id)
    assert suggested[0]["alternatives"] == ["analyze_email"]
    assert "analyze_email" in suggested[0]["nested_names"]
    assert "run_ledgerline" in result.structuredContent["unmapped_roots"]


def test_sync_reports_recarving_when_boundary_names_changed(monkeypatch):
    context = _context(permission=["read", "write"])
    connector = _connector(context)
    connector.capability_mapping = {
        "source": "observation_name",
        "names": ["analyze_email"],
        "assignments": {},
    }
    connector.imported_boundary_key = boundary_import_key(
        {"source": "observation_name", "names": ["run_invoice_agent"]}
    )
    connector.save(update_fields=["capability_mapping", "imported_boundary_key", "updated_at"])
    dispatched = []
    monkeypatch.setattr(
        tools_connectors,
        "enqueue_connector_sync",
        lambda value: dispatched.append(value.id),
    )

    result = _call("sync_connector", {"connector": str(connector.id)}, context)

    assert result.isError is False
    assert result.structuredContent["queued"] is True
    assert result.structuredContent["recarving"] is True
    assert "recarved" in result.structuredContent["summary"]
    assert result.structuredContent["console_traces_url"].endswith(
        f"/observability?projectId={context.project.id}"
    )
    assert dispatched == [connector.id]


def test_prepare_connector_sync_adopts_empty_key_and_wipes_on_name_change():
    context = _context()
    connector = _connector(context)
    mapping = {"source": "observation_name", "names": ["analyze_email"]}
    connector.capability_mapping = mapping
    connector.save(update_fields=["capability_mapping", "updated_at"])
    Span.objects.create(
        span_id="a" * 16,
        trace_id="b" * 32,
        project=context.project,
        name="analyze_email",
        resource_attrs={CONNECTOR_CREDENTIAL_ID_ATTR: str(connector.id)},
    )

    assert prepare_connector_sync(connector) is False
    connector.refresh_from_db()
    assert connector.imported_boundary_key == boundary_import_key(mapping)
    assert Span.objects.filter(span_id="a" * 16).exists()

    connector.capability_mapping = {"source": "observation_name", "names": ["run_invoice_agent"]}
    connector.save(update_fields=["capability_mapping", "updated_at"])
    assert prepare_connector_sync(connector) is True
    assert not Span.objects.filter(span_id="a" * 16).exists()
    connector.refresh_from_db()
    assert connector.sync_cursor == {}
    assert connector.total_traces_imported == 0


def test_prepare_connector_sync_skips_wipe_when_only_assignments_change():
    context = _context()
    connector = _connector(context)
    mapping = {
        "source": "observation_name",
        "names": ["analyze_email"],
        "assignments": {"analyze_email": str(uuid.uuid4())},
    }
    connector.capability_mapping = mapping
    connector.imported_boundary_key = boundary_import_key(
        {"source": "observation_name", "names": ["analyze_email"]}
    )
    connector.save(update_fields=["capability_mapping", "imported_boundary_key", "updated_at"])
    Span.objects.create(
        span_id="c" * 16,
        trace_id="d" * 32,
        project=context.project,
        name="analyze_email",
        resource_attrs={CONNECTOR_CREDENTIAL_ID_ATTR: str(connector.id)},
    )

    assert prepare_connector_sync(connector) is False
    assert Span.objects.filter(span_id="c" * 16).exists()


def test_reset_connector_import_deletes_only_that_credential_spans():
    context = _context()
    connector = _connector(context)
    other = _connector(context)
    Span.objects.create(
        span_id="e" * 16,
        trace_id="f" * 32,
        project=context.project,
        resource_attrs={CONNECTOR_CREDENTIAL_ID_ATTR: str(connector.id)},
    )
    Span.objects.create(
        span_id="g" * 16,
        trace_id="h" * 32,
        project=context.project,
        resource_attrs={CONNECTOR_CREDENTIAL_ID_ATTR: str(other.id)},
    )

    assert reset_connector_import(connector) == 1
    assert not Span.objects.filter(span_id="e" * 16).exists()
    assert Span.objects.filter(span_id="g" * 16).exists()
