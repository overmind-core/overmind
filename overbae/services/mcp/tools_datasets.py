"""Project-scoped dataset tools: create, inspect, query, and shape datasets with scripts
that run in the sandbox. Overmind never writes or chooses a script."""

from __future__ import annotations

import json
import re

import duckdb
from asgiref.sync import sync_to_async
from pydantic import ValidationError as PydanticValidationError

from overbae.models import Capability, Dataset
from overbae.services.datasets import dispatch, imports, paths, store, versions, workshop
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
    CancelDatasetJobInput,
    CancelDatasetJobOutput,
    CreateDatasetInput,
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
    VersionRef,
    dataset_import_job_link,
    dataset_output,
    dataset_resource_link,
    import_receipt,
    import_status,
    mutation_output,
    mutation_ref,
    probe_output,
    run_job_link,
    sanitize_error,
    serialize_dataset_detail,
    serialize_dataset_list_item,
    try_output,
    version_output,
)
from overbae.services.mcp.contracts.observability import QueryTracesInput
from overbae.services.mcp.errors import MCPError, dataset_mcp_error, mcp_dataset, mcp_version
from overbae.services.mcp.tools_observability import trace_filter_data

_READ_ONLY_SQL = re.compile(r"^\s*(?:select|with)\b", re.IGNORECASE)
QUERY_SECONDS = 10
QUERY_MAX_BYTES = 32 * 1024
QUERY_MAX_COLUMNS = 200
_QUERY_TRACE_PAGING = frozenset({"limit", "offset", "cursor", "all_spans"})
_QUERY_TRACE_FILTERS = frozenset(QueryTracesInput.model_fields) - _QUERY_TRACE_PAGING


def _resolve_capability(context: MCPContext, reference: str) -> Capability:
    from overbae.services.capabilities import identity

    capability = identity.lookup(context.project.id, str(reference))
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
    offset = payload.offset
    if payload.cursor is not None:
        if not payload.cursor.isdigit():
            raise MCPError("invalid_input", "The cursor is invalid.", fields={"cursor": "invalid"})
        offset = int(payload.cursor)
    total = query.count()
    datasets = list(
        query.select_related("capability", "active").order_by("-created_at")[
            offset : offset + payload.limit
        ]
    )
    items = [serialize_dataset_list_item(dataset) for dataset in datasets]
    next_offset = offset + len(items)
    return ListDatasetsOutput(
        summary=f"{len(items)} datasets.",
        datasets=items,
        page=PageContract(
            limit=payload.limit,
            offset=offset,
            total=total,
            has_more=next_offset < total,
            next_cursor=str(next_offset) if next_offset < total else None,
        ),
        resource_links=[item.resource for item in items],
    )


def _inspect_dataset_sync(payload: InspectDatasetInput, context: MCPContext) -> DatasetDetail:
    dataset = mcp_dataset(context, payload.dataset)
    return serialize_dataset_detail(
        dataset,
        offset=payload.version_offset,
        limit=payload.version_limit,
        selected=mcp_version(dataset, payload.version),
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
        version=VersionRef(id=str(cell.id), version=cell.label),
        columns=columns,
        rows=rows,
        n=len(rows),
        truncated=len(result["rows"]) > limit,
        resource_links=[dataset_resource_link(dataset)],
    )


def _trace_source(context: MCPContext, source) -> TraceSource:
    """A query_traces selection is accepted as it was written: its argument names are
    translated to traces-list filters, and any other key must already be one."""
    filters = {k: v for k, v in (source.filters or {}).items() if k not in _QUERY_TRACE_PAGING}
    named = {key: filters.pop(key) for key in list(filters) if key in _QUERY_TRACE_FILTERS}
    search, ordering = source.search, ""
    if named:
        try:
            query = QueryTracesInput.model_validate(named)
        except PydanticValidationError as exc:
            fields = {
                ".".join(map(str, issue["loc"])): issue["msg"][:300]
                for issue in exc.errors(include_input=False, include_url=False)[:20]
            }
            raise MCPError(
                "invalid_filter", "The trace filters are invalid.", fields=fields
            ) from None
        translated = trace_filter_data(query, context)
        translated.pop("project", None)
        translated.pop("all_spans", None)
        filters.update(translated)
        search = search or query.search
        ordering = query.ordering if "ordering" in named else ""
    try:
        return TraceSource.parse(
            {
                "trace_ids": source.trace_ids,
                "filters": filters,
                "search": search,
                "ordering": ordering,
                "limit": source.limit,
            }
        )
    except TraceSourceError as exc:
        raise MCPError("invalid_filter", str(exc)) from exc


def _llm_calls_source(context: MCPContext, source, capability: Capability):
    from overbae.services.datasets.llm_calls import Selection, SelectionError

    raw = {"capability_id": str(capability.id), "since": source.since, "limit": source.limit}
    if source.until:
        raw["until"] = source.until
    if source.model:
        raw["model"] = source.model
    try:
        selection = Selection.parse(raw)
        matched = selection.count(context.project.id)
    except SelectionError as exc:
        raise MCPError("invalid_input", str(exc)) from exc
    return selection, matched


def _create_dataset_sync(payload: CreateDatasetInput, context: MCPContext) -> DatasetMutationOutput:
    from overbae.services.datasets.llm_calls import HASH_POSITION

    explicit = "capability" in payload.model_fields_set
    capability = _resolve_capability(context, payload.capability) if payload.capability else None
    source = payload.source
    counts: dict[str, int] = {}
    if source.kind == "traces":
        selection = _trace_source(context, source)
        try:
            counts["traces"] = selection.count(context.project.id)
        except TraceSourceError as exc:
            raise MCPError("invalid_filter", str(exc)) from exc
        if counts["traces"] == 0:
            raise MCPError(
                "no_traces", "No traces match the selection. Widen the filters or check the ids."
            )
        spec = {"traces": selection.spec()}
        what = f"{counts['traces']} traces"
    elif source.kind == "llm_calls":
        selection, counts["calls"] = _llm_calls_source(context, source, capability)
        if counts["calls"] == 0:
            raise MCPError(
                "no_calls",
                "No LLM calls match the selection. Widen the window or check the capability.",
            )
        spec = {"llm_calls": selection.spec()}
        what = f"{counts['calls']} LLM calls"
    else:
        origin = mcp_dataset(context, source.dataset)
        cell = _version(origin, source.version)
        spec = {"version": {"dataset_id": str(origin.id), "cell_id": str(cell.id)}}
        what = f"{origin.name or 'Dataset'} {cell.label}, {cell.rows} rows"
    split = payload.split
    try:
        if split is not None:
            train, evaluation = dispatch.create_split(
                project=context.project,
                user=context.user,
                name=payload.name,
                source=spec,
                eval_percent=split.eval_percent,
                position=split.position
                or (HASH_POSITION if source.kind == "llm_calls" else "tail"),
                group_by=split.group_by,
                stratify_by=split.stratify_by,
                deduplicate=split.deduplicate,
                capability=capability,
                infer_capability=not explicit,
            )
        else:
            train = dispatch.create_dataset(
                project=context.project,
                user=context.user,
                name=payload.name,
                source=spec,
                intent=payload.intent,
                capability=capability,
                infer_capability=not explicit,
            )
            evaluation = None
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    return mutation_output(
        train,
        summary=f"Landing started from {what}."
        if evaluation is None
        else f"Split landing started from {what}, cut into a train and an eval dataset.",
        eval_dataset=evaluation,
        **counts,
    )


def _try_dataset_script_sync(
    payload: TryDatasetScriptInput, context: MCPContext
) -> TryDatasetScriptOutput:
    dataset = mcp_dataset(context, payload.dataset)
    try:
        run = workshop.try_script(
            dataset,
            payload.parent,
            payload.script,
            attachment=payload.attachment,
            user=context.user,
        )
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
            dataset,
            payload.parent,
            payload.title,
            payload.script,
            attachment=payload.attachment,
            user=context.user,
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
            _resolve_capability(context, payload.capability) if payload.capability else None
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


def _cancel_dataset_job_sync(
    payload: CancelDatasetJobInput, context: MCPContext
) -> CancelDatasetJobOutput:
    dataset = mcp_dataset(context, payload.dataset)
    if payload.run:
        try:
            run = workshop.cancel_run(dataset, payload.run)
        except DatasetError as exc:
            raise dataset_mcp_error(exc) from exc
        return CancelDatasetJobOutput(
            summary="The run was stopped."
            if run.state == "cancelled"
            else f"The run is {run.state}.",
            kind="dataset_run",
            id=str(run.id),
            status=run.state,
            resource_links=[dataset_resource_link(dataset), run_job_link(run)],
        )
    receipt = import_receipt(dataset)
    if receipt is None:
        raise MCPError("import_not_running", "This dataset has no import.")
    if receipt.state in imports.OPEN_STATES:
        try:
            imports.cancel(receipt.pk)
        except DatasetError as exc:
            if exc.code != "import_not_running":
                raise dataset_mcp_error(exc) from exc
        receipt.refresh_from_db()
    targets = list(
        Dataset.objects.filter(pk__in=[receipt.dataset_id, receipt.evaluation_id]).order_by(
            "-intent"
        )
    )
    dataset = next(target for target in targets if target.pk == dataset.pk)
    status = import_status(dataset, receipt)
    return CancelDatasetJobOutput(
        summary="The import was stopped." if status == "stopped" else f"The import is {status}.",
        kind="dataset_import",
        id=str(dataset.id),
        status=status,
        datasets=[mutation_ref(target) for target in targets],
        resource_links=[dataset_import_job_link(dataset)]
        + [dataset_resource_link(target) for target in targets],
    )


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
    "cancel_dataset_job",
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
            "One dataset: its version tree, the active and an optional version in full, "
            "consumer requirements (preparation_context), live runs, a sample and next actions.",
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
            "create_dataset",
            "Create dataset",
            "Land traces (trace_ids, filters such as a query_traces selection, or search), "
            "llm_calls since a time, or a version of a project dataset as a new dataset. "
            "Empty selections are refused. split lands train and eval.",
            CreateDatasetInput,
            DatasetMutationOutput,
            _create_dataset_sync,
            False,
            "task",
        ),
        (
            "try_dataset_script",
            "Try dataset script",
            "Run a script on a version in the sandbox and preview the resulting df. Creates "
            "nothing. While queued or running, poll get_job with the returned job.",
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
            "inactive version. While queued or running, poll get_job with the returned job.",
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
            "Set the default version consumers read when no version is given; started jobs keep "
            "their pinned versions.",
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
        (
            "cancel_dataset_job",
            "Cancel dataset job",
            "Stop the dataset's source import (both halves of a split), or with run one script "
            "run. Nothing it produced is kept; a finished job returns its state.",
            CancelDatasetJobInput,
            CancelDatasetJobOutput,
            _cancel_dataset_job_sync,
            False,
            "sync",
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
