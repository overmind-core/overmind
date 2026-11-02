from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone
from fakes.vendors import LangfuseAPI, Step
from mcp_fixtures import call_tool, mcp_context
from rest_framework.test import APIClient

from overbae.models import (
    APIToken,
    Capability,
    ConnectorCredential,
    ConnectorSyncConfig,
    ConnectorSyncRun,
    Project,
    Span,
)
from overbae.services.connectors.schema import CONNECTOR_CREDENTIAL_ID_ATTR
from overbae.services.connectors.sync import (
    boundary_import_key,
    prepare_connector_sync,
)
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.resources import read_resource, resource_templates

pytestmark = [pytest.mark.django_db, pytest.mark.covers("vendor-connectors")]


@pytest.fixture(autouse=True)
def langfuse(fake_llm) -> LangfuseAPI:
    api = LangfuseAPI(host="https://cloud.langfuse.com")
    fake_llm.network.vendors.append(api)
    return api


def _call(name: str, arguments: dict, context: MCPContext):
    return call_tool(name, arguments, context)


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


@pytest.mark.covers("privacy")
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


@pytest.fixture
def queued(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(
        "overbae.tasks.connector_sync.sync_connector_chunk.apply_async",
        lambda args, **_: sent.append(args[0]),
    )
    return sent


def _record(langfuse: LangfuseAPI, *names: str) -> None:
    start = timezone.now() - timedelta(hours=1)
    steps, parent = [], None
    for offset, name in enumerate(names):
        step = Step(
            name,
            "tool" if name.startswith("get_") else "span",
            parent,
            start=start + timedelta(seconds=offset),
            end=start + timedelta(seconds=offset + 1),
        )
        steps.append(step)
        parent = step.id
    langfuse.record(uuid.uuid4().hex, steps)


def _nested_agent(langfuse: LangfuseAPI) -> None:
    _record(langfuse, "run_ledgerline", "adjudicate_claim", "adjudicate_tool_loop", "get_fx_rate")


def test_inspect_without_configured_connectors_returns_cli_action():
    context = mcp_context()

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


@pytest.mark.covers("authentication")
def test_read_only_key_hides_and_denies_connector_writes():
    context = mcp_context()
    connector = _connector(context)

    visible = {tool.name for tool in CATALOG.tools(frozenset({"read"}))}
    assert "inspect_connectors" in visible
    assert {"configure_connector", "sync_connector"}.isdisjoint(visible)
    for name in ("configure_connector", "sync_connector"):
        result = _call(name, {"connector": str(connector.id)}, context)
        assert result.isError is True
        assert result.structuredContent["error"]["code"] == "permission_denied"


@pytest.mark.covers("tenancy")
def test_connector_lookup_is_project_scoped():
    context = mcp_context(permission=["read", "write"])
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
    context = mcp_context(permission=["read", "write"])
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


@pytest.mark.covers("privacy")
def test_provider_exceptions_are_redacted(langfuse):
    context = mcp_context(permission=["read", "write"])
    connector = _connector(context)
    langfuse.status = 500
    langfuse.outage = "provider-secret-value"

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


@pytest.mark.covers("privacy")
def test_connector_resource_is_project_scoped_and_secret_free():
    context = mcp_context()
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

    resource = async_to_sync(read)()
    encoded = json.dumps(resource)
    assert resource["kind"] == "connector"
    assert resource["sync_runs"][0]["has_error"] is True
    assert "provider-secret-value" not in encoded
    assert "provider-key" not in encoded
    assert "overmind://connectors/{connector}{?project_id}" in {
        template.uriTemplate for template in resource_templates()
    }


def test_list_shows_keyed_drafts_and_hides_keyless_wizard_rows():
    context = mcp_context()
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


def test_configure_first_config_defaults_lookback_and_leaves_auto_sync_off():
    context = mcp_context(permission=["read", "write"])
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


def test_sync_without_config_returns_config_required():
    context = mcp_context(permission=["read", "write"])
    connector = _connector(context, configured=False)

    result = _call("sync_connector", {"connector": str(connector.id)}, context)
    action = result.structuredContent["human_action"]

    assert result.isError is False
    assert result.structuredContent["queued"] is False
    assert action["code"] == "connector_config_required"
    assert action["action"] == "configure_connector"
    assert not action.get("command")


def test_sync_with_empty_source_returns_source_required():
    context = mcp_context(permission=["read", "write"])
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
    context = mcp_context(permission=["read", "write"])
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


@pytest.mark.covers("tenancy")
def test_create_rejects_project_outside_api_key_scope():
    context = mcp_context()
    other = Project.objects.create(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    raw, _ = APIToken.create_for_user(context.user, project=context.project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw)

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


def test_sync_waits_for_mapping_approval(queued):
    context = mcp_context(permission=["read", "write"])
    connector = _connector(context, mapping_confirmed=False)

    result = _call("sync_connector", {"connector": str(connector.id)}, context)

    assert result.isError is False
    assert result.structuredContent["queued"] is False
    assert result.structuredContent["human_action"]["code"] == (
        "connector_mapping_approval_required"
    )
    assert queued == []


def test_empty_mapping_can_be_approved_then_synced(queued):
    context = mcp_context(permission=["read", "write"])
    connector = _connector(context, mapping_confirmed=False)

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
    assert queued == [str(connector.id)]


def test_configure_drops_nested_names_from_the_staged_mapping(langfuse):
    context = mcp_context(permission=["read", "write"])
    connector = _connector(context, mapping_confirmed=False)
    capability = Capability.objects.create(
        project=context.project,
        name="Ledgerline Adjudicator",
        slug="ledgerline-adjudicator",
        entrypoint_fn="adjudicate_claim",
        improvement_metadata={"tool_spec": [{"name": "get_fx_rate"}]},
    )
    _nested_agent(langfuse)

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


def test_configure_without_mapping_stages_suggested_parents(langfuse):
    context = mcp_context(permission=["read", "write"])
    connector = _connector(context, mapping_confirmed=False)
    capability = Capability.objects.create(
        project=context.project,
        name="Ledgerline Adjudicator",
        slug="ledgerline-adjudicator",
        entrypoint_fn="adjudicate_claim",
    )
    _nested_agent(langfuse)

    result = _call(
        "configure_connector",
        {"connector": str(connector.id), "source_project_id": "lf-project"},
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


def test_configure_keeps_fallback_and_does_not_store_auto_create():
    context = mcp_context(permission=["read", "write"])
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


def test_sync_reports_recarving_when_boundary_names_changed(queued):
    context = mcp_context(permission=["read", "write"])
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
    result = _call("sync_connector", {"connector": str(connector.id)}, context)

    assert result.isError is False
    assert result.structuredContent["queued"] is True
    assert result.structuredContent["recarving"] is True
    assert "recarved" in result.structuredContent["summary"]
    assert result.structuredContent["console_traces_url"].endswith(
        f"/observability?projectId={context.project.id}"
    )
    assert queued == [str(connector.id)]


def test_prepare_connector_sync_adopts_empty_key_and_wipes_on_name_change():
    context = mcp_context()
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
