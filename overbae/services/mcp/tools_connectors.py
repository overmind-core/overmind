from __future__ import annotations

import uuid

from asgiref.sync import sync_to_async
from rest_framework.exceptions import APIException

from overbae.api.connector_review import ImportPreviewSerializer
from overbae.models import Capability, ConnectorCredential
from overbae.services.connectors import capabilities_for, get_adapter, registered_sources
from overbae.services.connectors.imports import confirm_import, request_preview
from overbae.services.connectors.review import review_groups
from overbae.services.connectors.sync import enqueue_connector_sync
from overbae.services.mcp.contracts.connectors import (
    ConfigureConnectorInput,
    ConfigureConnectorOutput,
    ConnectorDetails,
    ConnectorHumanAction,
    ConnectorJobReference,
    ConnectorPollHint,
    InspectConnectorsInput,
    InspectConnectorsOutput,
    ReviewTraceGroupsInput,
    ReviewTraceGroupsOutput,
    SyncConnectorInput,
    SyncConnectorOutput,
)
from overbae.services.mcp.errors import MCPError
from overbae.services.mcp.resources import connector_resource_payload, resource_link


def resolve_connector(context, reference):
    reference = reference.removeprefix("connectors:").removeprefix("overmind://connectors/")
    query = ConnectorCredential.objects.filter(project=context.project)
    try:
        connector = query.filter(pk=uuid.UUID(reference)).first()
    except ValueError:
        connector = query.filter(name=reference).first()
    if connector is None:
        raise MCPError("connector_not_found", "The connector was not found in this project.")
    return connector


def connector_link(connector):
    return resource_link("connectors", str(connector.id), connector.name)


def details(context, connector):
    payload = connector_resource_payload(
        context.project, connector, f"overmind://connectors/{connector.id}"
    )
    payload.pop("uri", None)
    payload.pop("kind", None)
    return ConnectorDetails(**payload, resource=connector_link(connector))


def inspect_connectors(payload, context):
    connectors = (
        [resolve_connector(context, payload.connector)]
        if payload.connector
        else list(ConnectorCredential.objects.filter(project=context.project)[:50])
    )
    results = []
    for connector in connectors:
        result = details(context, connector)
        if payload.include_source_projects:
            try:
                result.source_projects = [
                    {"id": p.id, "name": p.name}
                    for p in get_adapter(connector).list_source_projects()[:50]
                ]
                result.provider_status = "available"
            except Exception:
                result.provider_status = "unavailable"
        result.sync_runs = result.sync_runs[: payload.max_runs]
        results.append(result)
    return InspectConnectorsOutput(
        summary=f"{len(results)} integrations.",
        connectors=results,
        connector=results[0] if payload.connector else None,
        mapping_options=[
            {"id": str(c.id), "name": c.name, "slug": c.slug}
            for c in Capability.objects.filter(project=context.project, status="current")[:100]
        ],
        available_types=[
            {
                "connector_type": source,
                "auth": "pair" if capabilities_for(source).needs_secret else "bearer",
                "needs_source_project": capabilities_for(source).needs_source_project,
                "command": f"overmind connector add {source} --json",
            }
            for source in registered_sources()
        ],
        human_action=None
        if results
        else ConnectorHumanAction(
            code="connector_setup_required",
            action="run_cli",
            command="overmind connector add langfuse --json",
            message="Run the connector setup command in a terminal. Provider keys must not be pasted in chat.",
        ),
        resource_links=[connector_link(c) for c in connectors],
    )


def configure_connector(payload, context):
    connector = resolve_connector(context, payload.connector)
    preview = request_preview(connector, **payload.model_dump(exclude={"connector"}))
    return ConfigureConnectorOutput(
        summary="Counting the selected range. Review the count and duration estimate before importing.",
        preview=ImportPreviewSerializer(preview).data,
        resource_links=[connector_link(connector)],
    )


def sync_connector(payload, context):
    connector = resolve_connector(context, payload.connector)
    if payload.preview_id:
        confirm_import(connector, payload.preview_id)
    else:
        enqueue_connector_sync(connector)
    connector.refresh_from_db()
    link = connector_link(connector)
    return SyncConnectorOutput(
        summary="Import queued. Approved patterns apply automatically; new patterns require review.",
        queued=True,
        connector=details(context, connector),
        resource=link,
        job=ConnectorJobReference(id=f"connectors:{connector.id}", resource=link),
        poll_hint=ConnectorPollHint(resource=link),
        resource_links=[link],
    )


def review_trace_groups(payload, context):
    connector = resolve_connector(context, payload.connector)
    results = review_groups(
        connector, [a.model_dump() for a in payload.assignments], actor=context.user
    )
    return ReviewTraceGroupsOutput(
        summary=f"Reviewed {len(results)} patterns.",
        groups=results,
        resource_links=[connector_link(connector)],
    )


def register_connector_tools(catalog):
    from overbae.services.mcp.catalog import ToolDefinition  # catalog imports registrations

    definitions = [
        (
            "review_trace_groups",
            "Confirm trace assignments",
            "Atomically confirm the human's capability choices for all supplied pattern revisions. Null explicitly leaves a pattern unassigned. A stale revision rejects the whole batch. Approved choices also apply to future matching traces.",
            ReviewTraceGroupsInput,
            ReviewTraceGroupsOutput,
            review_trace_groups,
            False,
            "sync",
        ),
        (
            "inspect_connectors",
            "Inspect integrations",
            "Inspect imports, counts and stored trace groups. Includes sample trace IDs and current capability assignments. Credentials are never returned.",
            InspectConnectorsInput,
            InspectConnectorsOutput,
            inspect_connectors,
            True,
            "sync",
        ),
        (
            "configure_connector",
            "Preview trace import",
            "Count an existing connector's selected time range asynchronously. Omit the start for all available history. Show the human the ready count and estimated duration before sync_connector with preview_id.",
            ConfigureConnectorInput,
            ConfigureConnectorOutput,
            configure_connector,
            False,
            "sync",
        ),
        (
            "sync_connector",
            "Import traces",
            "Import a reviewed preview by preview_id, or resume an already confirmed configuration. Import preserves history and applies approved pattern rules; new patterns remain unassigned.",
            SyncConnectorInput,
            SyncConnectorOutput,
            sync_connector,
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

        async def handler(payload, context, fn=function):
            try:
                return await sync_to_async(fn, thread_sensitive=True)(payload, context)
            except APIException as exc:
                raise MCPError(
                    "invalid_input", str(exc.detail), retryable=exc.status_code == 409
                ) from exc

        catalog.register(
            ToolDefinition(
                name=name,
                title=title,
                description=description,
                input_model=input_model,
                output_model=output_model,
                read_only=read_only,
                idempotent=read_only,
                open_world=name != "review_trace_groups",
                required_scopes=frozenset(
                    {"overmind:read", "overmind:connectors"}
                    if read_only
                    else {"overmind:connectors"}
                ),
                cost_class="compute"
                if name in {"configure_connector", "sync_connector"}
                else "free",
                async_mode=async_mode,
            ),
            handler,
        )
