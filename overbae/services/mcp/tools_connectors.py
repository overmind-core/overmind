"""Project-scoped connector inspection, configuration, and sync tools."""

from __future__ import annotations

import uuid

from asgiref.sync import sync_to_async
from django.db import connection, transaction

from overbae.api.serializers import ConnectorCapabilityMappingWriteSerializer
from overbae.models import Capability, ConnectorCredential
from overbae.services.connectors import (
    capabilities_for,
    capability_source_error,
    get_adapter,
    registered_sources,
    relabel_connector_capabilities,
)
from overbae.services.connectors.capability_resolution import (
    drop_nested_mapping_names,
    mapping_from_suggested,
    suggest_parent_boundaries,
    union_custom_mapping,
    unmapped_root_names,
)
from overbae.services.connectors.feedforward import save_sync_config
from overbae.services.connectors.profiling import profile_capability_candidates
from overbae.services.connectors.sync import enqueue_connector_sync, will_recarve
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.connectors import (
    AvailableConnectorType,
    ConfigureConnectorInput,
    ConfigureConnectorOutput,
    ConnectorCapabilityAssignment,
    ConnectorDetails,
    ConnectorHumanAction,
    ConnectorJobReference,
    ConnectorMappingOption,
    ConnectorObservationShape,
    ConnectorPollHint,
    ConnectorSuggestedBoundary,
    InspectConnectorsInput,
    InspectConnectorsOutput,
    SyncConnectorInput,
    SyncConnectorOutput,
)
from overbae.services.mcp.errors import MCPError
from overbae.services.mcp.resources import (
    connector_mapping_assignments,
    connector_resource_payload,
    resource_link,
)

_MAX_SOURCE_PROJECTS = 50
_MAX_SHAPES = 50
_SAMPLE_LIMIT = 200
_SUPPORTED_MAPPING_SOURCES = frozenset({"observation_name", "metadata", "tag", "trace_name"})
_PREFERRED_TYPES = ("langfuse", "langsmith", "braintrust", "galileo")
_FIRST_CONFIG_LOOKBACK_DAYS = 30
_CLI_SETUP_MESSAGE = (
    "Present the command and wait for the human to run it. Do not run the CLI, "
    "export keys, or paste provider keys in chat."
)
_CONFIG_MESSAGE = (
    "Save source project, lookback, and a proposed capability mapping with configure_connector."
)
_SOURCE_MESSAGE = (
    "Set source_project_id with configure_connector using an id from inspect_connectors."
)
_MAPPING_MESSAGE = (
    "Stop. Present suggested_boundaries, alternatives, unmapped_roots, and "
    "mapping_options. Do not call confirm_mapping until the human replies."
)


def _console_traces_url(project) -> str:
    from django.conf import settings

    base = (getattr(settings, "FRONTEND_URL", "") or "http://localhost:5173").rstrip("/")
    return f"{base}/observability?projectId={project.id}"


def _json_safe_mapping(mapping: dict | None) -> dict:
    payload = mapping if isinstance(mapping, dict) else {}
    result: dict = {
        "names": [str(name) for name in (payload.get("names") or [])],
        "assignments": {
            str(key): str(value) for key, value in (payload.get("assignments") or {}).items()
        },
    }
    source = payload.get("source")
    if source:
        result["source"] = source
    key = payload.get("key")
    if key:
        result["key"] = str(key)
    fallback = payload.get("fallback_capability_id")
    if fallback:
        result["fallback_capability_id"] = str(fallback)
    return result


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


def _cli_command(connector_type: str | None = None) -> str:
    registered = registered_sources()
    if connector_type in registered:
        return f"overmind connector add {connector_type} --json"
    for source in _PREFERRED_TYPES:
        if source in registered:
            return f"overmind connector add {source} --json"
    source = next(iter(sorted(registered)), "langfuse")
    return f"overmind connector add {source} --json"


def _cli_setup_action(connector_type: str | None = None) -> ConnectorHumanAction:
    return ConnectorHumanAction(
        code="connector_setup_required",
        message=_CLI_SETUP_MESSAGE,
        action="run_cli",
        command=_cli_command(connector_type),
        resource="overmind://connector-setup",
    )


def _config_action() -> ConnectorHumanAction:
    return ConnectorHumanAction(
        code="connector_config_required",
        message=_CONFIG_MESSAGE,
        action="configure_connector",
    )


def _source_action() -> ConnectorHumanAction:
    return ConnectorHumanAction(
        code="connector_source_required",
        message=_SOURCE_MESSAGE,
        action="configure_connector",
    )


def _mapping_action() -> ConnectorHumanAction:
    return ConnectorHumanAction(
        code="connector_mapping_approval_required",
        message=_MAPPING_MESSAGE,
        action="approve_mapping",
    )


def _needs_mapping_approval(connector: ConnectorCredential) -> bool:
    return bool(connector.pending_capability_mapping) or not connector.capability_mapping_confirmed


def _needs_credentials(connector: ConnectorCredential) -> bool:
    if not connector.is_active or not connector.api_key:
        return True
    capabilities = capabilities_for(connector.connector_type)
    return bool(capabilities and capabilities.needs_secret and not connector.api_secret)


def _needs_source_project(connector: ConnectorCredential) -> bool:
    capabilities = capabilities_for(connector.connector_type)
    if not capabilities or not capabilities.needs_source_project:
        return False
    config = connector.active_config()
    return config is None or not (config.source_project_id or "").strip()


def _inspect_action(connector: ConnectorCredential) -> ConnectorHumanAction | None:
    if _needs_credentials(connector):
        return _cli_setup_action(connector.connector_type)
    if connector.active_config() is None:
        return _config_action()
    if _needs_mapping_approval(connector):
        return _mapping_action()
    return None


def _available_types() -> list[AvailableConnectorType]:
    registered = registered_sources()
    ordered = [source for source in _PREFERRED_TYPES if source in registered]
    ordered.extend(sorted(registered.difference(ordered)))
    types: list[AvailableConnectorType] = []
    for source in ordered[:8]:
        capabilities = capabilities_for(source)
        sources = [
            item
            for item in (getattr(capabilities, "capability_sources", ()) or ())
            if item in _SUPPORTED_MAPPING_SOURCES
        ]
        types.append(
            AvailableConnectorType(
                connector_type=source,
                auth="pair" if capabilities and capabilities.needs_secret else "bearer",
                needs_source_project=bool(
                    getattr(capabilities, "needs_source_project", True) if capabilities else True
                ),
                capability_sources=sources,
                command=f"overmind connector add {source} --json",
            )
        )
    return types


def _mapping_options(context: MCPContext) -> list[ConnectorMappingOption]:
    capabilities = (
        Capability.objects.filter(project=context.project).current().order_by("name")[:100]
    )
    return [
        ConnectorMappingOption(id=str(capability.id), name=capability.name, slug=capability.slug)
        for capability in capabilities
    ]


def _proposed_assignments(
    context: MCPContext, mapping: dict | None
) -> list[ConnectorCapabilityAssignment]:
    if not mapping:
        return []
    _, details = connector_mapping_assignments(context.project, mapping)
    return [ConnectorCapabilityAssignment.model_validate(item) for item in details]


def _observation_shapes(shapes: list[dict]) -> list[ConnectorObservationShape]:
    models: list[ConnectorObservationShape] = []
    for shape in shapes[:_MAX_SHAPES]:
        name = str(shape.get("name") or "").strip()
        if not name:
            continue
        parent = shape.get("parent_name")
        models.append(
            ConnectorObservationShape(
                name=name[:255],
                type=str(shape.get("type") or "")[:64],
                parent_name=str(parent)[:255] if parent else None,
                traces=int(shape.get("traces") or 0),
                score=int(shape.get("score") or 0),
                reasons=[str(reason)[:200] for reason in (shape.get("reasons") or [])[:8]],
                is_root=bool(shape.get("is_root")),
            )
        )
    return models


def _suggested_boundaries(suggested: list[dict]) -> list[ConnectorSuggestedBoundary]:
    return [
        ConnectorSuggestedBoundary(
            name=str(item["name"])[:255],
            capability_id=str(item["capability_id"]),
            capability_name=str(item["capability_name"])[:255],
            nested_names=[str(name)[:255] for name in (item.get("nested_names") or [])[:50]],
            alternatives=[str(name)[:255] for name in (item.get("alternatives") or [])[:50]],
        )
        for item in suggested[:_MAX_SHAPES]
    ]


def _preview_source_and_lookback(
    connector: ConnectorCredential,
    *,
    source_project_id: str | None = None,
    lookback_days: int | None = None,
) -> tuple[str, int]:
    config = connector.active_config()
    source = source_project_id
    if source is None:
        source = config.source_project_id if config is not None else ""
    lookback = lookback_days
    if not lookback:
        lookback = (config.lookback_days if config and config.lookback_days else None) or 30
    return (source or "").strip(), int(lookback)


def _safe_mapping_preview(
    context: MCPContext,
    connector: ConnectorCredential,
    *,
    source_project_id: str | None = None,
    lookback_days: int | None = None,
) -> tuple[list[dict], list[dict], list[str]]:
    if _needs_credentials(connector):
        return [], [], []
    source, lookback = _preview_source_and_lookback(
        connector, source_project_id=source_project_id, lookback_days=lookback_days
    )
    try:
        adapter = get_adapter(connector)
        traces = adapter.sample_units(
            lookback_days=lookback,
            limit=_SAMPLE_LIMIT,
            source_project_id=source,
        )
        shapes = profile_capability_candidates(traces, adapter.conventions)[:_MAX_SHAPES]
        suggested = suggest_parent_boundaries(context.project, shapes)
        return shapes, suggested, unmapped_root_names(shapes, suggested)
    except Exception:  # noqa: BLE001 — provider details must never reach MCP
        return [], [], []


def _should_sample_mapping(
    connector: ConnectorCredential,
    *,
    include_source_projects: bool,
    preview: bool,
    preview_source_project_id: str | None,
) -> bool:
    if _needs_credentials(connector):
        return False
    config = connector.active_config()
    source = preview_source_project_id or (config.source_project_id if config is not None else "")
    return bool(include_source_projects or preview or (source or "").strip())


def _apply_pending_mapping(connector: ConnectorCredential) -> None:
    connector.capability_mapping = connector.pending_capability_mapping or {}
    connector.pending_capability_mapping = {}
    connector.capability_mapping_confirmed = True
    connector.save(
        update_fields=[
            "capability_mapping",
            "pending_capability_mapping",
            "capability_mapping_confirmed",
            "updated_at",
        ]
    )
    if connection.features.supports_json_field_contains:
        relabel_connector_capabilities(connector)


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
    if _needs_credentials(connector):
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
    normalized = _json_safe_mapping(serializer.validated_data)
    unsupported = capability_source_error(connector_type, normalized.get("source"))
    return normalized, unsupported


def _parent_only_mapping(
    mapping: dict | None,
    *,
    shapes: list[dict],
    suggested: list[dict],
    fill_if_omitted: bool,
) -> tuple[dict | None, list[str]]:
    if mapping is None:
        if fill_if_omitted and suggested:
            return mapping_from_suggested(suggested), []
        return None, []
    mapping = union_custom_mapping(mapping, suggested)
    return drop_nested_mapping_names(mapping, shapes)


def _resolve_capability(context: MCPContext, reference: str) -> Capability | None:
    from overbae.services.capabilities import identity

    return identity.lookup(context.project.id, str(reference))


def _inspect_connectors_sync(
    payload: InspectConnectorsInput, context: MCPContext
) -> InspectConnectorsOutput:
    available = _available_types()
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
        link = resource_link("connectors", str(connector.id), connector.name)
        shapes, suggested, unmapped = ([], [], [])
        if _should_sample_mapping(
            connector,
            include_source_projects=payload.include_source_projects,
            preview=payload.preview,
            preview_source_project_id=payload.preview_source_project_id,
        ):
            shapes, suggested, unmapped = _safe_mapping_preview(
                context,
                connector,
                source_project_id=payload.preview_source_project_id,
                lookback_days=payload.preview_lookback_days,
            )
        return InspectConnectorsOutput(
            summary="Connector inspected.",
            connectors=[details],
            connector=details,
            available_types=available,
            mapping_options=_mapping_options(context),
            proposed_assignments=_proposed_assignments(
                context, connector.pending_capability_mapping
            ),
            observation_shapes=_observation_shapes(shapes),
            suggested_boundaries=_suggested_boundaries(suggested),
            unmapped_roots=unmapped,
            console_traces_url=_console_traces_url(context.project),
            human_action=_inspect_action(connector),
            resource_links=[link],
        )

    connectors = list(
        ConnectorCredential.objects.filter(project=context.project, is_active=True)
        .exclude(api_key="")
        .prefetch_related("configs", "runs")
        .order_by("-created_at")[:50]
    )
    details = [_details(context, connector, max_runs=payload.max_runs) for connector in connectors]
    links = [
        resource_link("connectors", str(connector.id), connector.name) for connector in connectors
    ]
    if not connectors:
        action = _cli_setup_action()
    elif any(_needs_credentials(connector) for connector in connectors):
        action = _cli_setup_action(
            next(c.connector_type for c in connectors if _needs_credentials(c))
        )
    elif any(connector.active_config() is None for connector in connectors):
        action = _config_action()
    elif any(_needs_mapping_approval(connector) for connector in connectors):
        action = _mapping_action()
    else:
        action = None
    return InspectConnectorsOutput(
        summary="Connectors inspected.",
        connectors=details,
        available_types=available,
        mapping_options=_mapping_options(context),
        console_traces_url=_console_traces_url(context.project),
        human_action=action,
        resource_links=links,
    )


def _configure_connector_sync(
    payload: ConfigureConnectorInput, context: MCPContext
) -> ConfigureConnectorOutput:
    connector = _resolve_connector(context, payload.connector)
    link = resource_link("connectors", str(connector.id), connector.name)
    if _needs_credentials(connector):
        details = _details(context, connector)
        return ConfigureConnectorOutput(
            summary="Connector setup is required.",
            configured=False,
            connector=details,
            mapping_options=_mapping_options(context),
            console_traces_url=_console_traces_url(context.project),
            human_action=_cli_setup_action(connector.connector_type),
            resource=link,
            resource_links=[link],
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

    preview_source = (
        payload.source_project_id if "source_project_id" in payload.model_fields_set else None
    )
    preview_lookback = (
        payload.lookback_days if "lookback_days" in payload.model_fields_set else None
    )
    shapes, suggested, unmapped = _safe_mapping_preview(
        context,
        connector,
        source_project_id=preview_source,
        lookback_days=preview_lookback,
    )
    previous_pending = dict(connector.pending_capability_mapping or {})
    had_pending = bool(previous_pending)
    fill = not connector.capability_mapping_confirmed and not had_pending
    normalized_mapping, dropped = _parent_only_mapping(
        normalized_mapping,
        shapes=shapes,
        suggested=suggested,
        fill_if_omitted=fill,
    )
    if normalized_mapping is not None:
        normalized_mapping = _json_safe_mapping(normalized_mapping)

    config_fields = {
        "source_project_id",
        "lookback_days",
        "backfill_from",
        "backfill_to",
    }
    has_config_input = bool(payload.model_fields_set & config_fields)
    creating_first = connector.active_config() is None
    apply_mapping = False
    with transaction.atomic():
        if normalized_mapping is not None:
            connector.pending_capability_mapping = normalized_mapping
            connector.save(update_fields=["pending_capability_mapping", "updated_at"])
            apply_mapping = (
                payload.confirm_mapping and had_pending and previous_pending == normalized_mapping
            )
        elif payload.confirm_mapping and had_pending:
            apply_mapping = True
        if apply_mapping:
            _apply_pending_mapping(connector)
        if not creating_first:
            if payload.auto_sync_enabled is not None:
                connector.auto_sync_enabled = payload.auto_sync_enabled
            if payload.poll_interval_seconds is not None:
                connector.poll_interval_seconds = payload.poll_interval_seconds
            if payload.auto_sync_enabled is not None or payload.poll_interval_seconds is not None:
                connector.save(
                    update_fields=["auto_sync_enabled", "poll_interval_seconds", "updated_at"]
                )
        elif payload.poll_interval_seconds is not None:
            connector.poll_interval_seconds = payload.poll_interval_seconds
            connector.save(update_fields=["poll_interval_seconds", "updated_at"])
        if has_config_input or creating_first:
            config_kwargs = {"target_project": context.project}
            for field in config_fields:
                if field in payload.model_fields_set:
                    config_kwargs[field] = getattr(payload, field)
            if creating_first and "lookback_days" not in payload.model_fields_set:
                config_kwargs["lookback_days"] = _FIRST_CONFIG_LOOKBACK_DAYS
            save_sync_config(connector, **config_kwargs)
            ConnectorCredential.objects.filter(pk=connector.pk).update(
                sync_cursor={},
                sync_status=ConnectorCredential.SyncStatus.IDLE,
                sync_error="",
                next_poll_at=None,
            )
    connector.refresh_from_db()
    details = _details(context, connector)
    mapping_pending = _needs_mapping_approval(connector)
    options = _mapping_options(context)
    preview = {
        "observation_shapes": _observation_shapes(shapes),
        "suggested_boundaries": _suggested_boundaries(suggested),
        "unmapped_roots": unmapped,
        "dropped_nested_names": dropped,
    }
    if mapping_pending:
        mapping_touched = normalized_mapping is not None or payload.confirm_mapping
        return ConfigureConnectorOutput(
            summary=(
                "Capability mapping is waiting for human approval."
                if mapping_touched
                else "Connector configuration saved."
            ),
            configured=True,
            mapping_pending=True,
            proposed_assignments=_proposed_assignments(
                context, connector.pending_capability_mapping
            ),
            mapping_options=options,
            connector=details,
            console_traces_url=_console_traces_url(context.project),
            human_action=_mapping_action(),
            resource=link,
            resource_links=[link],
            **preview,
        )
    return ConfigureConnectorOutput(
        summary="Connector configuration saved.",
        configured=True,
        mapping_pending=False,
        proposed_assignments=[],
        mapping_options=options,
        connector=details,
        console_traces_url=_console_traces_url(context.project),
        resource=link,
        resource_links=[link],
        **preview,
    )


def _sync_connector_sync(payload: SyncConnectorInput, context: MCPContext) -> SyncConnectorOutput:
    connector = _resolve_connector(context, payload.connector)
    link = resource_link("connectors", str(connector.id), connector.name)
    traces_url = _console_traces_url(context.project)
    if _needs_credentials(connector):
        return SyncConnectorOutput(
            summary="Connector setup is required.",
            queued=False,
            console_traces_url=traces_url,
            connector=_details(context, connector),
            resource=link,
            human_action=_cli_setup_action(connector.connector_type),
            resource_links=[link],
        )
    if connector.active_config() is None:
        return SyncConnectorOutput(
            summary="Connector configuration is required.",
            queued=False,
            console_traces_url=traces_url,
            connector=_details(context, connector),
            resource=link,
            human_action=_config_action(),
            resource_links=[link],
        )
    if connector.connector_type not in registered_sources():
        raise MCPError("connector_unsupported", "This connector type cannot be synced.")
    if _needs_source_project(connector):
        return SyncConnectorOutput(
            summary="A source project is required.",
            queued=False,
            console_traces_url=traces_url,
            connector=_details(context, connector),
            resource=link,
            human_action=_source_action(),
            resource_links=[link],
        )
    if _needs_mapping_approval(connector):
        return SyncConnectorOutput(
            summary="Capability mapping must be approved before import.",
            queued=False,
            console_traces_url=traces_url,
            connector=_details(context, connector),
            resource=link,
            human_action=_mapping_action(),
            resource_links=[link],
        )
    recarving = will_recarve(connector)
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
        summary=(
            "Connector sync queued; existing traces will be recarved."
            if recarving
            else "Connector sync queued."
        ),
        queued=True,
        recarving=recarving,
        console_traces_url=traces_url,
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
            "List or inspect project connectors with safe configuration, parent-only mapping "
            "suggestions, alternatives, and bounded sync status. Stage mapping without "
            "confirm_mapping. Present suggested_boundaries, alternatives, unmapped_roots, and "
            "mapping_options, then stop until the human replies.",
            InspectConnectorsInput,
            InspectConnectorsOutput,
            _inspect_connectors_sync,
            True,
            "sync",
        ),
        (
            "configure_connector",
            "Configure connector",
            "Save an existing project connector's source-project, lookback, and polling settings. "
            "mapping.names are observation-name boundaries (trace roots). Nested children are "
            "dropped when their ancestor is also listed. Default proposal is suggested parents; "
            "alternatives match the same capability and the human may pick one as the boundary. "
            "Do not set confirm_mapping until the human replies.",
            ConfigureConnectorInput,
            ConfigureConnectorOutput,
            _configure_connector_sync,
            False,
            "sync",
        ),
        (
            "sync_connector",
            "Sync connector",
            "Queue an existing project connector's configured import. If mapping.names or source "
            "changed since the last import, existing connector spans are recarved on this sync. "
            "Returns console_traces_url.",
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
