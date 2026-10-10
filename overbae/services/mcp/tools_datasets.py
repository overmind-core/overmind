from __future__ import annotations

import json
import re
import uuid

import duckdb
from asgiref.sync import sync_to_async

from overbae.models import Capability, Dataset
from overbae.services.datasets import (
    dispatch,
    paths,
    store,
    workbench,
)
from overbae.services.datasets.contract import stored_intents
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.datasets.selection import TraceSource, TraceSourceError
from overbae.services.datasets.versions import resolve_cell
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.common import PageContract
from overbae.services.mcp.contracts.datasets import (
    CreateDatasetFromLlmCallsInput,
    CreateDatasetFromTracesInput,
    DatasetDetail,
    DatasetMutationOutput,
    InspectDatasetInput,
    ListDatasetsInput,
    ListDatasetsOutput,
    QueryDatasetInput,
    QueryDatasetOutput,
    StartDatasetInput,
    dataset_resource_link,
    mutation_output,
    sanitize_error,
    serialize_dataset_detail,
    serialize_dataset_list_item,
)
from overbae.services.mcp.errors import MCPError, dataset_mcp_error, mcp_dataset

_READ_ONLY_SQL = re.compile(r"^\s*(?:select|with)\b", re.IGNORECASE)
QUERY_RESULT_BYTES = 32 * 1024


def _uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError):
        return None


def _resolve_dataset(context: MCPContext, reference: str) -> Dataset:
    return mcp_dataset(context, reference)


def _resolve_capability(context: MCPContext, reference: str) -> Capability:
    from overbae.services.capabilities import identity

    capability = identity.lookup(context.project.id, str(reference))
    if capability is None:
        raise MCPError("capability_not_found", "The capability was not found in this project.")
    return capability


def _resolve_capability_uuid(context: MCPContext, reference: str) -> Capability:
    capability_id = _uuid(reference)
    if capability_id is None:
        raise MCPError("invalid_input", "Capability references must be UUIDs.")
    capability = Capability.objects.filter(project=context.project, id=capability_id).first()
    if capability is None:
        raise MCPError("capability_not_found", "The capability was not found in this project.")
    return capability


def _list_datasets_sync(payload: ListDatasetsInput, context: MCPContext) -> ListDatasetsOutput:
    query = Dataset.objects.filter(project=context.project)
    if payload.capability:
        query = query.filter(capability=_resolve_capability(context, payload.capability))
    if payload.intent:
        query = query.filter(intent__in=stored_intents(payload.intent))
    if payload.state:
        query = query.filter(state=payload.state)
    if payload.search:
        query = query.filter(name__icontains=payload.search.strip())
    total = query.count()
    datasets = list(
        query.select_related("capability", "active")
        .prefetch_related("cells")
        .order_by("-created_at")[payload.offset : payload.offset + payload.limit]
    )
    items = [serialize_dataset_list_item(dataset) for dataset in datasets]
    next_offset = payload.offset + len(items)
    return ListDatasetsOutput(
        summary=f"{len(items)} datasets.",
        datasets=items,
        page=PageContract(
            limit=payload.limit,
            offset=payload.offset,
            total=total,
            has_more=next_offset < total,
            next_cursor=str(next_offset) if next_offset < total else None,
        ),
        resource_links=[item.resource for item in items],
    )


def _inspect_dataset_sync(payload: InspectDatasetInput, context: MCPContext) -> DatasetDetail:
    return serialize_dataset_detail(
        _resolve_dataset(context, payload.dataset),
        cell_offset=payload.cell_offset,
        cell_limit=payload.cell_limit,
        source_offset=payload.source_offset,
        source_limit=payload.source_limit,
    )


def _query_dataset_sync(payload: QueryDatasetInput, context: MCPContext) -> QueryDatasetOutput:
    dataset = _resolve_dataset(context, payload.dataset)
    sql = payload.sql.strip()
    if not _READ_ONLY_SQL.match(sql):
        raise MCPError("query_invalid", "Only read-only SELECT queries are supported.")
    try:
        cell = resolve_cell(dataset, payload.cell, ran_only=True)
    except DatasetError as exc:
        raise MCPError("cell_not_found", "The cell was not found in this dataset.") from exc
    if cell.dataset_id != dataset.id:
        raise MCPError("cell_not_found", "The cell was not found in this dataset.")
    limit = min(payload.limit, 100)
    try:
        result = store.query(
            sql,
            limit=limit + 1,
            max_bytes=32768,
            max_columns=200,
            t=paths.cell_path(dataset.id, cell.id),
        )
    except store.QueryTimeoutError as exc:
        raise MCPError("query_timeout", str(exc), retryable=False) from exc
    except store.QuerySizeError as exc:
        raise MCPError("query_result_too_large", str(exc), retryable=False) from exc
    except (duckdb.Error, store.StoreError) as exc:
        reason = sanitize_error(str(exc).splitlines()[0], 300)
        raise MCPError("query_invalid", f"The query failed: {reason}") from exc
    rows = list(result["rows"])[:limit]
    columns = [str(column) for column in (result.get("columns") or [])]
    if len(columns) > 200:
        raise MCPError(
            "query_result_too_large",
            "The query result exceeds 200 columns. Select fewer columns or export the "
            "full cell through overmind://dataset-export. No column metadata was omitted.",
        )
    link = dataset_resource_link(dataset)
    output = QueryDatasetOutput(
        summary=f"{len(rows)} rows.",
        dataset=str(dataset.id),
        cell_id=str(cell.id),
        version=dataset.versions().get(cell.id),
        columns=columns,
        rows=rows,
        n=len(rows),
        truncated=len(result["rows"]) > limit,
        resource_links=[link],
    )
    if len(json.dumps(output.model_dump(mode="json")).encode()) > QUERY_RESULT_BYTES:
        raise MCPError(
            "query_result_too_large",
            "The query result exceeds 32 KiB. Select fewer rows or smaller columns, "
            "use SQL aggregates, or export the full cell through overmind://dataset-export. "
            "No values were clipped.",
        )
    return output


def _create_dataset_from_traces_sync(
    payload: CreateDatasetFromTracesInput, context: MCPContext
) -> DatasetMutationOutput:
    capability = (
        _resolve_capability_uuid(context, payload.capability) if payload.capability else None
    )
    try:
        source = TraceSource.parse(
            {
                "trace_ids": payload.trace_ids,
                "filters": payload.filters,
                "search": payload.search,
                "limit": payload.limit,
            }
        )
        matched = source.count(context.project.id)
    except TraceSourceError as exc:
        raise MCPError("invalid_input", str(exc)) from exc
    if matched == 0:
        raise MCPError(
            "no_traces", "No traces match the selection. Widen the filters or check the ids."
        )
    try:
        if payload.split is not None:
            train, evaluation = dispatch.create_split(
                project=context.project,
                user=context.user,
                name=payload.name,
                brief=payload.brief,
                source={"traces": source.spec()},
                eval_percent=payload.split.eval_percent,
                position=payload.split.position,
                group_by=payload.split.group_by,
                stratify_by=payload.split.stratify_by,
                deduplicate=payload.split.deduplicate,
                capability=capability,
                infer_capability="capability" not in payload.model_fields_set,
            )
        else:
            train = dispatch.create_dataset(
                project=context.project,
                user=context.user,
                name=payload.name,
                brief=payload.brief,
                source={"traces": source.spec()},
                intent=payload.intent,
                capability=capability,
                infer_capability="capability" not in payload.model_fields_set,
            )
            evaluation = None
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return mutation_output(
        train,
        summary=f"Dataset landing started: {matched} traces, one row each."
        if evaluation is None
        else f"Split landing started: {matched} traces cut into a train and an eval dataset.",
        traces=matched,
        eval_dataset=evaluation,
    )


def _create_dataset_from_llm_calls_sync(
    payload: CreateDatasetFromLlmCallsInput, context: MCPContext
) -> DatasetMutationOutput:
    from overbae.services.datasets.llm_calls import HASH_POSITION, Selection, SelectionError

    capability = _resolve_capability(context, payload.capability)
    raw = {
        "capability_id": str(capability.id),
        "since": payload.since,
        "limit": payload.limit,
    }
    if payload.until:
        raw["until"] = payload.until
    if payload.model:
        raw["model"] = payload.model
    try:
        selection = Selection.parse(raw)
        matched = selection.count(context.project.id)
    except SelectionError as exc:
        raise MCPError("invalid_input", str(exc)) from exc
    if matched == 0:
        raise MCPError(
            "no_calls",
            "No LLM calls match the selection. Widen the window or check the capability.",
        )
    try:
        if payload.split:
            train, evaluation = dispatch.create_split(
                project=context.project,
                user=context.user,
                name=payload.name,
                source={"llm_calls": selection.spec()},
                eval_percent=payload.eval_percent,
                position=HASH_POSITION,
                capability=capability,
                infer_capability=False,
            )
        else:
            train = dispatch.create_dataset(
                project=context.project,
                user=context.user,
                name=payload.name,
                source={"llm_calls": selection.spec()},
                intent=payload.intent,
                capability=capability,
                infer_capability=False,
            )
            evaluation = None
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return mutation_output(
        train,
        summary=(
            f"Dataset landing started: {matched} LLM calls."
            if evaluation is None
            else f"Split landing started: {matched} LLM calls cut into a train and an eval dataset."
        ),
        calls=matched,
        eval_dataset=evaluation,
    )


def _start_dataset_sync(payload: StartDatasetInput, context: MCPContext) -> DatasetMutationOutput:
    capability = _resolve_capability(context, payload.capability) if payload.capability else None
    try:
        dataset = dispatch.create_dataset(
            project=context.project,
            user=context.user,
            name=payload.name,
            brief=payload.brief,
            intent=payload.intent,
            capability=capability,
            infer_capability=False,
        )
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return mutation_output(dataset, summary="Dataset started from the written request.")


def _cancel_dataset_sync(payload, context):
    dataset = _resolve_dataset(context, payload.dataset)
    workbench.cancel(dataset)
    dataset.refresh_from_db()
    return mutation_output(
        dataset, summary="Cancellation requested. Inspect dataset state and run receipts."
    )


def _async_handler(function):
    async def handler(payload, context):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


def register_dataset_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "cancel_dataset",
            "Cancel dataset operation",
            "Cancel publication of queued or running pipeline work, or request cancellation of source landing. In-flight calculations may finish without publishing. Inspect dataset state and run receipts; external agents and providers are not stopped.",
            InspectDatasetInput,
            DatasetMutationOutput,
            _cancel_dataset_sync,
            False,
            "job",
        ),
        (
            "start_dataset",
            "Start dataset",
            "Start from a written request; source, capability and training intent are optional. Inspect for next steps.",
            StartDatasetInput,
            DatasetMutationOutput,
            _start_dataset_sync,
            False,
            "job",
        ),
        (
            "list_datasets",
            "List datasets",
            "List bounded project datasets with optional capability, intent, state, and name filters.",
            ListDatasetsInput,
            ListDatasetsOutput,
            _list_datasets_sync,
            True,
            "sync",
        ),
        (
            "inspect_dataset",
            "Inspect dataset",
            "Inspect dataset versions, extraction metadata, progress, profiles and consumer requirements. Cells expose receipt-backed transformation execution, exact revision/package/entrypoint, distinguishing external imports and unrecorded history. Read all package files through dataset-pipeline-packages resources. Page cells and sources with offsets and next_cursor.",
            InspectDatasetInput,
            DatasetDetail,
            _inspect_dataset_sync,
            True,
            "sync",
        ),
        (
            "query_dataset",
            "Query dataset",
            "Read-only SELECT over table t on one ran cell (active unless cell is an id or version). "
            "At most 100 rows; truncated when more matched. Results over 200 columns or 32 KiB of JSON fail "
            "with query_result_too_large; project smaller columns, aggregate, or use the "
            "dataset-export resource for complete data. Values are never clipped.",
            QueryDatasetInput,
            QueryDatasetOutput,
            _query_dataset_sync,
            True,
            "sync",
        ),
        (
            "create_dataset_from_traces",
            "Create dataset from traces",
            "Land one row per trace. Give trace_ids, or filters and/or search. "
            "Empty selections are refused. split lands train and eval.",
            CreateDatasetFromTracesInput,
            DatasetMutationOutput,
            _create_dataset_from_traces_sync,
            False,
            "task",
        ),
        (
            "create_dataset_from_llm_calls",
            "Create dataset from LLM calls",
            "Land one row per llm_call since a timestamp. split hashes span_id into train and eval.",
            CreateDatasetFromLlmCallsInput,
            DatasetMutationOutput,
            _create_dataset_from_llm_calls_sync,
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
        mode,
    ) in definitions:
        catalog.register(
            ToolDefinition(
                name=name,
                title=title,
                description=description,
                input_model=input_model,
                output_model=output_model,
                read_only=read_only,
                destructive=name == "cancel_dataset",
                idempotent=read_only or name == "cancel_dataset",
                open_world=False,
                required_scopes=frozenset(
                    {"overmind:read"} if read_only else {"overmind:data:write"}
                ),
                cost_class="free",
                async_mode=mode,
            ),
            _async_handler(function),
        )
