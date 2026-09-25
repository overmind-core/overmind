"""Project-scoped dataset inspection, creation, agent, and run tools."""

from __future__ import annotations

import re
import uuid

import duckdb
from asgiref.sync import sync_to_async

from overbae.models import Capability, Dataset
from overbae.services.datasets import dispatch, paths, store
from overbae.services.datasets.contract import stored_intents
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.datasets.notebook.agent import resolve_cell
from overbae.services.datasets.selection import TraceSource, TraceSourceError
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
    MessageDatasetAgentInput,
    QueryDatasetInput,
    QueryDatasetOutput,
    RunDatasetInput,
    dataset_resource_link,
    mutation_output,
    sanitize_error,
    serialize_dataset_detail,
    serialize_dataset_list_item,
)
from overbae.services.mcp.errors import MCPError, dataset_mcp_error, mcp_dataset

_READ_ONLY_SQL = re.compile(r"^\s*(?:select|with)\b", re.IGNORECASE)


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
        chat_limit=payload.chat_limit,
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
        result = store.query(sql, limit=limit + 1, t=paths.cell_path(dataset.id, cell.id))
    except duckdb.Error as exc:
        reason = sanitize_error(str(exc).splitlines()[0], 300)
        raise MCPError("query_invalid", f"The query failed: {reason}") from exc
    rows = list(result["rows"])[:limit]
    columns = [str(column) for column in (result.get("columns") or [])[:200]]
    link = dataset_resource_link(dataset)
    return QueryDatasetOutput(
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


def _message_dataset_agent_sync(
    payload: MessageDatasetAgentInput, context: MCPContext
) -> DatasetMutationOutput:
    dataset = _resolve_dataset(context, payload.dataset)
    message = payload.message.strip()
    if not message:
        raise MCPError("invalid_input", "A non-empty message is required.")
    try:
        dispatch.message_agent(dataset, context.user, message)
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return mutation_output(dataset, summary="Dataset agent queued.")


def _run_dataset_sync(payload: RunDatasetInput, context: MCPContext) -> DatasetMutationOutput:
    dataset = _resolve_dataset(context, payload.dataset)
    proposal = None
    if payload.proposal_cell:
        proposal_id = _uuid(payload.proposal_cell)
        if proposal_id is None:
            raise MCPError("invalid_input", "Proposal cell references must be UUIDs.")
        proposal = dataset.cells.filter(id=proposal_id).first()
        if proposal is None:
            raise MCPError("cell_not_found", "The proposal was not found in this dataset.")
    try:
        dispatch.run_dataset(dataset, context.user, proposal=proposal)
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return mutation_output(dataset, summary="Dataset run queued.")


def _async_handler(function):
    async def handler(payload, context):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


def register_dataset_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
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
            "Inspect one project dataset, its bounded cell chain, source/active task-family profiles, downstream consumer requirements, sample, recent agent chat, and next actions.",
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
            "At most 100 rows; truncated when more matched.",
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
        (
            "message_dataset_agent",
            "Message dataset agent",
            "Queue one agent turn for an idle project dataset.",
            MessageDatasetAgentInput,
            DatasetMutationOutput,
            _message_dataset_agent_sync,
            False,
            "job",
        ),
        (
            "run_dataset",
            "Run dataset",
            "Run a dataset; approving a proposal activates it and resumes its agent request.",
            RunDatasetInput,
            DatasetMutationOutput,
            _run_dataset_sync,
            False,
            "job",
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
                idempotent=read_only,
                open_world=False,
                required_scopes=frozenset(
                    {"overmind:read"} if read_only else {"overmind:data:write"}
                ),
                cost_class="llm" if name == "run_dataset" else "free",
                async_mode=mode,
            ),
            _async_handler(function),
        )
