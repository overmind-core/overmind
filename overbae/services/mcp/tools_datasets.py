"""Project-scoped dataset tools: create, inspect, query, and shape datasets with scripts
that run in the sandbox. Overmind never writes or chooses a script."""

from __future__ import annotations

import json
import re
import uuid

import duckdb
from asgiref.sync import sync_to_async

from overbae.models import Capability, Dataset
from overbae.services.datasets import dispatch, paths, store, versions, workshop
from overbae.services.datasets.contract import stored_intents
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.datasets.selection import TraceSource, TraceSourceError
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.common import PageContract
from overbae.services.mcp.contracts.datasets import (
    ActivateDatasetVersionInput,
    AddDatasetVersionInput,
    AddDatasetVersionOutput,
    AddSyntheticExamplesInput,
    CreateDatasetFromLlmCallsInput,
    CreateDatasetFromTracesInput,
    DatasetDetail,
    DatasetMutationOutput,
    DatasetOutput,
    DiffDatasetVersionsInput,
    DiffDatasetVersionsOutput,
    InspectDatasetInput,
    ListDatasetsInput,
    ListDatasetsOutput,
    ProbeDatasetVersionInput,
    QueryDatasetInput,
    QueryDatasetOutput,
    ResumeDatasetImportInput,
    ScriptRunOutput,
    TryDatasetScriptInput,
    TryDatasetScriptOutput,
    UpdateDatasetInput,
    dataset_output,
    dataset_resource_link,
    mutation_output,
    probe_output,
    sanitize_error,
    serialize_dataset_detail,
    serialize_dataset_list_item,
    try_output,
    version_output,
)
from overbae.services.mcp.errors import MCPError, dataset_mcp_error, mcp_dataset

_READ_ONLY_SQL = re.compile(r"^\s*(?:select|with)\b", re.IGNORECASE)
QUERY_SECONDS = 10
QUERY_MAX_BYTES = 32 * 1024
QUERY_MAX_COLUMNS = 200


def _uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError):
        return None


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
        query.select_related("capability", "active").order_by("-created_at")[
            payload.offset : payload.offset + payload.limit
        ]
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
        mcp_dataset(context, payload.dataset),
        offset=payload.cell_offset,
        limit=payload.cell_limit,
    )


def _version(dataset: Dataset, ref: str | None):
    try:
        return versions.resolve_cell(dataset, ref)
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc


def _query_dataset_sync(payload: QueryDatasetInput, context: MCPContext) -> QueryDatasetOutput:
    dataset = mcp_dataset(context, payload.dataset)
    sql = payload.sql.strip()
    if not _READ_ONLY_SQL.match(sql):
        raise MCPError("query_invalid", "Only read-only SELECT queries are supported.")
    cell = _version(dataset, payload.version)
    limit = min(payload.limit, 100)
    try:
        result = store.query(
            sql,
            limit=limit + 1,
            timeout=QUERY_SECONDS,
            t=paths.cell_path(dataset.id, cell.id),
        )
    except store.QueryTimeoutError as exc:
        raise MCPError("query_timeout", str(exc), retryable=False) from exc
    except duckdb.Error as exc:
        reason = sanitize_error(str(exc).splitlines()[0], 300)
        raise MCPError("query_invalid", f"The query failed: {reason}") from exc
    rows = list(result["rows"])[:limit]
    columns = [str(column) for column in (result.get("columns") or [])]
    if len(columns) > QUERY_MAX_COLUMNS:
        raise MCPError(
            "query_result_too_large",
            f"The query returned {len(columns)} columns; select at most {QUERY_MAX_COLUMNS}.",
        )
    size = len(json.dumps(rows, default=str, ensure_ascii=False).encode())
    if size > QUERY_MAX_BYTES:
        raise MCPError(
            "query_result_too_large",
            f"The result is {size // 1024} KiB; the limit is {QUERY_MAX_BYTES // 1024} KiB. "
            "Select fewer rows or columns, or shorter values.",
        )
    return QueryDatasetOutput(
        summary=f"{len(rows)} rows from {cell.label}.",
        dataset=str(dataset.id),
        cell_id=str(cell.id),
        version=cell.label,
        columns=columns,
        rows=rows,
        n=len(rows),
        truncated=len(result["rows"]) > limit,
        resource_links=[dataset_resource_link(dataset)],
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


def _try_dataset_script_sync(
    payload: TryDatasetScriptInput, context: MCPContext
) -> TryDatasetScriptOutput:
    dataset = mcp_dataset(context, payload.dataset)
    try:
        run = workshop.try_script(dataset, payload.parent, payload.script, user=context.user)
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return try_output(dataset, run)


def _probe_dataset_version_sync(
    payload: ProbeDatasetVersionInput, context: MCPContext
) -> ScriptRunOutput:
    dataset = mcp_dataset(context, payload.dataset)
    try:
        run = workshop.probe(dataset, payload.version, payload.script, user=context.user)
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return probe_output(dataset, run)


def _add_dataset_version_sync(
    payload: AddDatasetVersionInput, context: MCPContext
) -> AddDatasetVersionOutput:
    dataset = mcp_dataset(context, payload.dataset)
    try:
        run = workshop.add_version(
            dataset, payload.parent, payload.title, payload.script, user=context.user
        )
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return version_output(dataset, run)


def _add_synthetic_examples_sync(
    payload: AddSyntheticExamplesInput, context: MCPContext
) -> AddDatasetVersionOutput:
    dataset = mcp_dataset(context, payload.dataset)
    try:
        run = workshop.add_synthetic_examples(
            dataset, payload.parent, payload.rows, payload.instruction, user=context.user
        )
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return version_output(dataset, run)


def _diff_dataset_versions_sync(
    payload: DiffDatasetVersionsInput, context: MCPContext
) -> DiffDatasetVersionsOutput:
    dataset = mcp_dataset(context, payload.dataset)
    try:
        result = workshop.diff_versions(dataset, payload.start, payload.to)
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    start, end = result.pop("from"), result.pop("to")
    return DiffDatasetVersionsOutput(
        summary=f"{start} to {end}: {result.get('rows_before')} → {result.get('rows_after')} rows.",
        start=start,
        to=end,
        diff=json.loads(json.dumps(result, default=str)),
        resource_links=[dataset_resource_link(dataset)],
    )


def _activate_dataset_version_sync(
    payload: ActivateDatasetVersionInput, context: MCPContext
) -> DatasetOutput:
    dataset = mcp_dataset(context, payload.dataset)
    try:
        dataset = workshop.activate_version(
            dataset, payload.version, expected_active=payload.expected_active
        )
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return dataset_output(dataset, f"{dataset.active.label} is active.")


def _update_dataset_sync(payload: UpdateDatasetInput, context: MCPContext) -> DatasetOutput:
    dataset = mcp_dataset(context, payload.dataset)
    changes = {}
    if payload.name is not None:
        changes["name"] = payload.name
    if payload.intent is not None:
        changes["intent"] = payload.intent
    if "capability" in payload.model_fields_set:
        changes["capability"] = (
            _resolve_capability_uuid(context, payload.capability) if payload.capability else None
        )
    if not changes:
        raise MCPError("invalid_input", "Give name, intent or capability.")
    try:
        dataset = workshop.update_dataset(dataset, **changes)
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return dataset_output(dataset, "Dataset updated: " + ", ".join(changes) + ".")


def _resume_dataset_import_sync(
    payload: ResumeDatasetImportInput, context: MCPContext
) -> DatasetMutationOutput:
    dataset = mcp_dataset(context, payload.dataset)
    try:
        dataset = dispatch.resume_import(dataset)
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return mutation_output(dataset, summary="Source import resumed.")


def _async_handler(function):
    async def handler(payload, context):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


_IDEMPOTENT = {
    "try_dataset_script",
    "probe_dataset_version",
    "add_dataset_version",
    "add_synthetic_examples",
    "activate_dataset_version",
    "update_dataset",
}


def register_dataset_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "list_datasets",
            "List datasets",
            "List project datasets with optional capability, intent, state and name filters.",
            ListDatasetsInput,
            ListDatasetsOutput,
            _list_datasets_sync,
            True,
            "sync",
        ),
        (
            "inspect_dataset",
            "Inspect dataset",
            "One dataset: its version tree, active version, consumer requirements "
            "(preparation_context), a sample and next actions.",
            InspectDatasetInput,
            DatasetDetail,
            _inspect_dataset_sync,
            True,
            "sync",
        ),
        (
            "query_dataset",
            "Query dataset",
            "Read-only SELECT over table t of one version (active by default). At most 100 rows "
            "and 32 KiB; 10 s timeout.",
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
            "try_dataset_script",
            "Try dataset script",
            "Run a script on a version in the sandbox and preview the resulting df. Creates nothing.",
            TryDatasetScriptInput,
            TryDatasetScriptOutput,
            _try_dataset_script_sync,
            False,
            "sync",
        ),
        (
            "probe_dataset_version",
            "Probe dataset version",
            "Run a script on a version in the sandbox and return what it prints.",
            ProbeDatasetVersionInput,
            ScriptRunOutput,
            _probe_dataset_version_sync,
            False,
            "sync",
        ),
        (
            "add_dataset_version",
            "Add dataset version",
            "Run a script on a parent version in the sandbox and store the resulting df as a new, "
            "inactive version.",
            AddDatasetVersionInput,
            AddDatasetVersionOutput,
            _add_dataset_version_sync,
            False,
            "sync",
        ),
        (
            "add_synthetic_examples",
            "Add synthetic examples",
            "Append 1-50 rows you wrote to a version as a new, inactive version.",
            AddSyntheticExamplesInput,
            AddDatasetVersionOutput,
            _add_synthetic_examples_sync,
            False,
            "sync",
        ),
        (
            "diff_dataset_versions",
            "Diff dataset versions",
            "Compare two versions row by row on source_row.",
            DiffDatasetVersionsInput,
            DiffDatasetVersionsOutput,
            _diff_dataset_versions_sync,
            True,
            "sync",
        ),
        (
            "activate_dataset_version",
            "Activate dataset version",
            "Make a version the one training, evaluation and the optimizer use.",
            ActivateDatasetVersionInput,
            DatasetOutput,
            _activate_dataset_version_sync,
            False,
            "sync",
        ),
        (
            "update_dataset",
            "Update dataset",
            "Set a dataset's name, intent or capability.",
            UpdateDatasetInput,
            DatasetOutput,
            _update_dataset_sync,
            False,
            "sync",
        ),
        (
            "resume_dataset_import",
            "Resume dataset import",
            "Retry a blocked source import.",
            ResumeDatasetImportInput,
            DatasetMutationOutput,
            _resume_dataset_import_sync,
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
                idempotent=read_only or name in _IDEMPOTENT,
                open_world=False,
                required_scopes=frozenset(
                    {"overmind:read"} if read_only else {"overmind:data:write"}
                ),
                cost_class="free",
                async_mode=mode,
            ),
            _async_handler(function),
        )
