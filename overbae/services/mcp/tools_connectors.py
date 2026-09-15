"""Project-scoped connector inspection, configuration, and sync tools."""

from __future__ import annotations

import uuid

from asgiref.sync import sync_to_async
from django.db import connection, transaction

from overbae.api.serializers import ConnectorCapabilityMappingWriteSerializer
from overbae.models import Capability, ConnectorCredential
from overbae.services.connectors import (
    capability_source_error,
    get_adapter,
    registered_sources,
    relabel_connector_capabilities,
)
from overbae.services.connectors.feedforward import save_sync_config
from overbae.services.connectors.sync import enqueue_connector_sync
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.connectors import (
    ConfigureConnectorInput,
    ConfigureConnectorOutput,
    ConnectorDetails,
    ConnectorHumanAction,
    ConnectorJobReference,
    ConnectorPollHint,
    InspectConnectorsInput,
    InspectConnectorsOutput,
    SyncConnectorInput,
    SyncConnectorOutput,
)
from overbae.services.mcp.errors import MCPError
from overbae.services.mcp.resources import (
    connector_resource_payload,
    resource_link,
)

_MAX_SOURCE_PROJECTS = 50


def _uuid_ref(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return None


def _connector_ref(value: str) -> str:
    value = value.strip()
    if value.startswith("connectors:"):
        return value.split(":", 1)[1]
    if value.startswith("overmind://connectors/"):
        return value.rsplit("/", 1)[-1]
    return value


def _resolve_connector(context: MCPContext, reference: str) -> ConnectorCredential:
    reference = _connector_ref(reference)
    query = ConnectorCredential.objects.filter(project=context.project)
    normalized_id = _uuid_ref(reference)
    connector = query.filter(id=normalized_id).first() if normalized_id else None
    if connector is None:
        connector = query.filter(name__iexact=reference).order_by("-created_at").first()
    if connector is None:
        raise MCPError("connector_not_found", "The connector was not found in this project.")
    return connector


def _setup_action() -> ConnectorHumanAction:
    return ConnectorHumanAction(
        code="connector_setup_required",
        message="Open Integrations and complete the connector setup form, including credentials.",
    )


def _needs_setup(connector: ConnectorCredential) -> bool:
    return not connector.is_active or not connector.api_key or connector.active_config() is None


def _safe_provider_data(
    connector: ConnectorCredential,
    *,
    include_source_projects: bool,
    preview: bool,
    preview_source_project_id: str | None,
    preview_lookback_days: int,
) -> tuple[list[dict[str, str]], bool, int | None, str]:
    if not (include_source_projects or preview):
        return [], False, None, "not_requested"
    if _needs_setup(connector):
        return [], False, None, "setup_required"

    try:
        adapter = get_adapter(connector)
        projects = adapter.list_source_projects() if include_source_projects else []
        source_projects = [
            {"id": str(project.id)[:128], "name": str(project.name)[:255]}
            for project in projects[: _MAX_SOURCE_PROJECTS + 1]
        ]
        truncated = len(source_projects) > _MAX_SOURCE_PROJECTS
        source_projects = source_projects[:_MAX_SOURCE_PROJECTS]
        count = None
        if preview:
            config = connector.active_config()
            source_project_id = preview_source_project_id or (
                config.source_project_id if config is not None else ""
            )
            count = adapter.count(
                lookback_days=preview_lookback_days,
                source_project_id=source_project_id,
            )
        return source_projects, truncated, count, "available"
    except Exception:  # noqa: BLE001 — provider details must never reach MCP
        return [], False, None, "unavailable"


def _details(
    context: MCPContext,
    connector: ConnectorCredential,
    *,
    max_runs: int = 50,
    include_source_projects: bool = False,
    preview: bool = False,
    preview_source_project_id: str | None = None,
    preview_lookback_days: int = 30,
) -> ConnectorDetails:
    uri = resource_link("connectors", str(connector.id), connector.name)["uri"]
    payload = connector_resource_payload(context.project, connector, uri)
    payload.pop("uri", None)
    payload.pop("kind", None)
    payload["resource"] = resource_link("connectors", str(connector.id), connector.name)
    source_projects, projects_truncated, preview_count, provider_status = _safe_provider_data(
        connector,
        include_source_projects=include_source_projects,
        preview=preview,
        preview_source_project_id=preview_source_project_id,
        preview_lookback_days=preview_lookback_days,
    )
    runs = payload["sync_runs"]
    payload["sync_runs"] = runs[:max_runs]
    payload["sync_runs_truncated"] = payload["sync_runs_truncated"] or len(runs) > max_runs
    payload["source_projects"] = source_projects
    payload["source_projects_truncated"] = projects_truncated
    payload["preview_count"] = preview_count
    payload["provider_status"] = provider_status
    return ConnectorDetails.model_validate(payload)


def _mapping_payload(mapping) -> dict:
    return mapping.model_dump(exclude_none=True, exclude_unset=True)


def _resolve_mapping_capabilities(
    context: MCPContext, connector_type: str, mapping
) -> tuple[dict, str | None]:
    payload = _mapping_payload(mapping)
    assignments = payload.get("assignments") or {}
    resolved: dict[str, str] = {}
    for source_value, reference in assignments.items():
        capability = _resolve_capability(context, reference)
        if capability is None:
            raise MCPError(
                "capability_not_found",
                "A capability mapping target was not found in this project.",
            )
        resolved[str(source_value)] = str(capability.id)
    if "assignments" in payload:
        payload["assignments"] = resolved

    fallback = payload.get("fallback_capability_id")
    if fallback:
        capability = _resolve_capability(context, fallback)
        if capability is None:
            raise MCPError(
                "capability_not_found",
                "The fallback capability was not found in this project.",
            )
        payload["fallback_capability_id"] = str(capability.id)

    serializer = ConnectorCapabilityMappingWriteSerializer(data=payload)
    if not serializer.is_valid():
        raise MCPError("connector_mapping_invalid", "The connector capability mapping is invalid.")
    normalized = {
        key: value for key, value in serializer.validated_data.items() if value is not None
    }
    unsupported = capability_source_error(connector_type, normalized.get("source"))
    return normalized, unsupported


def _resolve_capability(context: MCPContext, reference: str) -> Capability | None:
    from overbae.services.capabilities import identity

    return identity.lookup(context.project.id, str(reference))


def _inspect_connectors_sync(
    payload: InspectConnectorsInput, context: MCPContext
) -> InspectConnectorsOutput:
    if payload.connector:
        connector = _resolve_connector(context, payload.connector)
        details = _details(
            context,
            connector,
            max_runs=payload.max_runs,
            include_source_projects=payload.include_source_projects,
            preview=payload.preview,
            preview_source_project_id=payload.preview_source_project_id,
            preview_lookback_days=payload.preview_lookback_days,
        )
        action = _setup_action() if _needs_setup(connector) else None
        link = resource_link("connectors", str(connector.id), connector.name)
        return InspectConnectorsOutput(
            summary="Connector inspected.",
            connectors=[details],
            connector=details,
            human_action=action,
            resource_links=[link],
        )

    connectors = list(
        ConnectorCredential.objects.filter(
            project=context.project, is_active=True, configs__isnull=False
        )
        .distinct()
        .order_by("-created_at")[:50]
    )
    details = [_details(context, connector, max_runs=payload.max_runs) for connector in connectors]
    links = [
        resource_link("connectors", str(connector.id), connector.name) for connector in connectors
    ]
    action = _setup_action() if any(_needs_setup(connector) for connector in connectors) else None
    if not connectors:
        action = _setup_action()
    return InspectConnectorsOutput(
        summary="Connectors inspected.",
        connectors=details,
        human_action=action,
        resource_links=links,
    )


def _configure_connector_sync(
    payload: ConfigureConnectorInput, context: MCPContext
) -> ConfigureConnectorOutput:
    connector = _resolve_connector(context, payload.connector)
    if _needs_setup(connector):
        details = _details(context, connector)
        return ConfigureConnectorOutput(
            summary="Connector setup is required.",
            configured=False,
            connector=details,
            human_action=_setup_action(),
            resource=resource_link("connectors", str(connector.id), connector.name),
            resource_links=[resource_link("connectors", str(connector.id), connector.name)],
        )
    if connector.connector_type not in registered_sources():
        raise MCPError("connector_unsupported", "This connector type cannot be synced.")

    normalized_mapping = None
    if payload.capability_mapping is not None:
        normalized_mapping, unsupported = _resolve_mapping_capabilities(
            context, connector.connector_type, payload.capability_mapping
        )
        if unsupported:
            raise MCPError(
                "connector_mapping_invalid", "The connector mapping source is unsupported."
            )

    config_fields = {
        "source_project_id",
        "lookback_days",
        "backfill_from",
        "backfill_to",
    }
    has_config_input = bool(payload.model_fields_set & config_fields)
    config = connector.active_config()
    with transaction.atomic():
        if payload.auto_sync_enabled is not None:
            connector.auto_sync_enabled = payload.auto_sync_enabled
        if payload.poll_interval_seconds is not None:
            connector.poll_interval_seconds = payload.poll_interval_seconds
        if payload.auto_sync_enabled is not None or payload.poll_interval_seconds is not None:
            connector.save(
                update_fields=["auto_sync_enabled", "poll_interval_seconds", "updated_at"]
            )
        if has_config_input or config is None:
            config_kwargs = {"target_project": context.project}
            for field in config_fields:
                if field in payload.model_fields_set:
                    config_kwargs[field] = getattr(payload, field)
            config = save_sync_config(connector, **config_kwargs)
            ConnectorCredential.objects.filter(pk=connector.pk).update(
                sync_cursor={},
                sync_status=ConnectorCredential.SyncStatus.IDLE,
                sync_error="",
                next_poll_at=None,
            )
        if normalized_mapping is not None:
            connector.capability_mapping = normalized_mapping
            connector.save(update_fields=["capability_mapping", "updated_at"])
            if connection.features.supports_json_field_contains:
                relabel_connector_capabilities(connector)
    connector.refresh_from_db()
    details = _details(context, connector)
    link = resource_link("connectors", str(connector.id), connector.name)
    return ConfigureConnectorOutput(
        summary="Connector configuration saved.",
        configured=True,
        connector=details,
        resource=link,
        resource_links=[link],
    )


def _sync_connector_sync(payload: SyncConnectorInput, context: MCPContext) -> SyncConnectorOutput:
    connector = _resolve_connector(context, payload.connector)
    link = resource_link("connectors", str(connector.id), connector.name)
    if _needs_setup(connector) or connector.active_config() is None:
        return SyncConnectorOutput(
            summary="Connector setup is required.",
            queued=False,
            connector=_details(context, connector),
            resource=link,
            human_action=_setup_action(),
            resource_links=[link],
        )
    if connector.connector_type not in registered_sources():
        raise MCPError("connector_unsupported", "This connector type cannot be synced.")
    try:
        enqueue_connector_sync(connector)
    except Exception as exc:
        raise MCPError(
            "connector_sync_dispatch_failed",
            "The connector sync could not be queued.",
            retryable=True,
        ) from exc
    connector.refresh_from_db()
    job = ConnectorJobReference(
        id=str(connector.id),
        resource=link,
    )
    return SyncConnectorOutput(
        summary="Connector sync queued.",
        queued=True,
        connector=_details(context, connector),
        resource=link,
        job=job,
        poll_hint=ConnectorPollHint(resource=link),
        resource_links=[link],
    )


def _async_handler(function):
    async def handler(payload, context):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


def register_connector_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "inspect_connectors",
            "Inspect connectors",
            "List or inspect project connectors: configuration, mappings, provider capabilities and bounded sync status.",
            InspectConnectorsInput,
            InspectConnectorsOutput,
            _inspect_connectors_sync,
            True,
            "sync",
        ),
        (
            "configure_connector",
            "Configure connector",
            "Save a project connector's source project, lookback, capability mapping and polling settings.",
            ConfigureConnectorInput,
            ConfigureConnectorOutput,
            _configure_connector_sync,
            False,
            "sync",
        ),
        (
            "sync_connector",
            "Sync connector",
            "Queue a project connector's configured import and return its polling resource.",
            SyncConnectorInput,
            SyncConnectorOutput,
            _sync_connector_sync,
            False,
            "task",
        ),
    ]
    for (
        name,
        title,
        description,
        input_model,
        output_model,
        function,
        read_only,
        async_mode,
    ) in definitions:
        catalog.register(
            ToolDefinition(
                name=name,
                title=title,
                description=description,
                input_model=input_model,
                output_model=output_model,
                read_only=read_only,
                idempotent=read_only,
                open_world=name in {"inspect_connectors", "sync_connector"},
                required_scopes=frozenset(
                    {"overmind:read", "overmind:connectors"}
                    if read_only
                    else {"overmind:connectors"}
                ),
                cost_class="compute" if name == "inspect_connectors" else "free",
                async_mode=async_mode,
            ),
            _async_handler(function),
        )
