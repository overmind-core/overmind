"""Bounded MCP contracts for datasets, their versions and script runs."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import AliasChoices, Field, field_validator, model_validator

from overbae.models import Cell, Dataset, Execution
from overbae.services.datasets import executions, imports, paths, store, workshop
from overbae.services.datasets.context import workshop_context
from overbae.services.datasets.contract import public_intent
from overbae.services.mcp.contracts.common import (
    JobReceipt,
    MCPModel,
    PageContract,
    ResourceLinkContract,
)

_LIST_CAP = 100
_VERSION_DEFAULT = 50
_VERSION_CAP = 200
_SAMPLE_ROWS = 5
_SAMPLE_CELL_CHARS = 600
_QUERY_ROWS = 100
_SCRIPT_CHARS = 4_000
_SCRIPT_INPUT_CHARS = 20_000
_STDOUT_CHARS = 4_000
_RANK_CAP = 20
_SUMMARY_CHARS = 240
_PATH_RE = re.compile(r"(?:/[\w.-]+)+")

DatasetState = Literal["landing", "idle", "error"]
RunStatus = Literal["queued", "running", "ok", "failed"]


def _clip(value: str, limit: int = _SCRIPT_CHARS) -> str:
    value = value or ""
    return value if len(value) <= limit else value[:limit]


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return "<path>"
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in list(value.items())[:100]}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in list(value)[:100]]
    if isinstance(value, str):
        return _clip(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return None if value != value else value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return _clip(str(value))


def sanitize_error(value: str, limit: int = 500) -> str:
    return _clip(_PATH_RE.sub("<path>", (value or "").strip()), limit)


def _link(uri: str, title: str) -> ResourceLinkContract:
    return ResourceLinkContract(uri=uri, title=_clip(title.strip() or "Dataset", 160))


def dataset_resource_link(dataset) -> ResourceLinkContract:
    ds_id = quote(str(dataset.id), safe="")
    return _link(f"overmind://datasets/{ds_id}", dataset.name or "Dataset")


def dataset_import_job_link(dataset) -> ResourceLinkContract:
    ds_id = quote(str(dataset.id), safe="")
    return _link(f"overmind://jobs/dataset_import/{ds_id}", f"{dataset.name or 'Dataset'} import")


def _capability_link(capability) -> ResourceLinkContract:
    cap_id = quote(str(capability.id), safe="")
    return _link(f"overmind://capabilities/{cap_id}", capability.name or "Capability")


class FitReport(MCPModel):
    ok: bool
    reason: str = Field(default="", max_length=500)


class CapabilityRef(MCPModel):
    id: str
    name: str = Field(min_length=1, max_length=255)
    resource: ResourceLinkContract


class ActiveVersion(MCPModel):
    id: str
    version: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=255)
    rows: int = Field(ge=0)
    fingerprint: str = Field(default="", max_length=64)
    fits: FitReport


class DatasetListItem(MCPModel):
    id: str
    name: str = Field(default="", max_length=255)
    intent: Literal["train", "eval", "pending"]
    source_kind: Literal["file", "traces", "llm_calls"]
    state: DatasetState
    capability: CapabilityRef | None = None
    active: ActiveVersion | None = None
    resource: ResourceLinkContract


class VersionSummary(MCPModel):
    id: str
    version: str = Field(min_length=1, max_length=32)
    parent: str | None = Field(default=None, max_length=32)
    title: str = Field(min_length=1, max_length=255)
    script: str = Field(default="", max_length=_SCRIPT_CHARS)
    script_truncated: bool = False
    rows: int = Field(ge=0)
    columns: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    fingerprint: str = Field(default="", max_length=64)
    fits: FitReport
    intent_report: dict[str, Any] = Field(default_factory=dict)
    capability_report: dict[str, Any] = Field(default_factory=dict)
    impact: dict[str, Any] = Field(default_factory=dict)
    used: bool
    created_at: datetime


class CapabilityRankItem(MCPModel):
    capability_id: str
    name: str = Field(min_length=1, max_length=255)
    score: float = 0.0
    reason: str = Field(default="", max_length=500)


class DatasetSample(MCPModel):
    version: str = Field(min_length=1, max_length=32)
    cell_id: str
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=_SAMPLE_ROWS)


class NextAction(MCPModel):
    tool: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)
    arguments: dict[str, Any] = Field(default_factory=dict)


class DatasetHumanAction(MCPModel):
    command: str = Field(min_length=1, max_length=240)
    arguments: dict[str, str] = Field(default_factory=dict, max_length=20)


class SourceFile(MCPModel):
    filename: str = Field(max_length=255)
    bytes: int | None = Field(default=None, ge=0)
    rows: int | None = Field(default=None, ge=0)
    extraction: dict[str, Any] = Field(default_factory=dict)


class DatasetSource(MCPModel):
    files: list[SourceFile] = Field(default_factory=list, max_length=_LIST_CAP)
    limitations: list[str] = Field(default_factory=list, max_length=50)


class DatasetDetail(DatasetListItem):
    source: DatasetSource
    preparation_context: dict[str, Any] = Field(default_factory=dict)
    contamination_report: dict[str, Any] = Field(default_factory=dict)
    capability_rank: list[CapabilityRankItem] = Field(default_factory=list, max_length=_RANK_CAP)
    versions: list[VersionSummary] = Field(default_factory=list, max_length=_VERSION_CAP)
    versions_total: int = Field(ge=0)
    running: int = Field(default=0, ge=0)
    sample: DatasetSample | None = None
    next_actions: list[NextAction] = Field(default_factory=list, max_length=8)
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=8)
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    human_action: DatasetHumanAction | None = None
    error: str | None = None


class DatasetMutationRef(MCPModel):
    id: str
    name: str = Field(default="", max_length=255)
    state: DatasetState
    resource: ResourceLinkContract


class DatasetJobReceipt(JobReceipt):
    kind: Literal["dataset_import"] = "dataset_import"


class DatasetMutationOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    dataset: DatasetMutationRef
    job: DatasetJobReceipt
    eval_dataset: DatasetMutationRef | None = None
    # Set by create_dataset_from_traces: the traces the selection resolved to.
    traces: int | None = Field(default=None, ge=0)
    # Set by create_dataset_from_llm_calls: spans matching the selection.
    calls: int | None = Field(default=None, ge=0)
    resource_links: list[ResourceLinkContract] = Field(max_length=4)


def _dataset_field():
    return Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("dataset", "dataset_id"),
    )


def _version_field():
    return Field(default=None, min_length=1, max_length=80)


class ListDatasetsInput(MCPModel):
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    intent: Literal["train", "eval", "pending"] | None = None
    state: DatasetState | None = None
    search: str | None = Field(default=None, min_length=1, max_length=255)
    limit: int = Field(default=20, ge=1, le=_LIST_CAP)
    offset: int = Field(default=0, ge=0)


class ListDatasetsOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    datasets: list[DatasetListItem] = Field(default_factory=list, max_length=_LIST_CAP)
    page: PageContract
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=_LIST_CAP)


class InspectDatasetInput(MCPModel):
    dataset: str = _dataset_field()
    cell_offset: int = Field(default=0, ge=0)
    cell_limit: int = Field(default=_VERSION_DEFAULT, ge=1, le=_VERSION_CAP)


class QueryDatasetInput(MCPModel):
    dataset: str = _dataset_field()
    sql: str = Field(
        min_length=1,
        max_length=8_000,
        description="One DuckDB SELECT over table t. source_row is row identity, not data.",
    )
    version: str | None = _version_field()
    limit: int = Field(default=_QUERY_ROWS, ge=1, le=_QUERY_ROWS)


class QueryDatasetOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    dataset: str
    cell_id: str
    version: str
    columns: list[str] = Field(default_factory=list, max_length=200)
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=_QUERY_ROWS)
    n: int = Field(ge=0, le=_QUERY_ROWS)
    truncated: bool = False
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=2)


class SplitInput(MCPModel):
    group_by: list[str] = Field(default_factory=list, max_length=10)
    stratify_by: str | None = Field(default=None, max_length=255)
    deduplicate: bool = True
    eval_percent: int = Field(
        default=20, ge=1, le=99, description="Share of the rows that lands as the eval dataset."
    )
    position: Literal["head", "tail", "random"] = Field(
        default="tail", description="Where the eval rows are taken from."
    )


class CreateDatasetFromTracesInput(MCPModel):
    name: str = Field(min_length=1, max_length=255, description="Dataset name.")
    trace_ids: list[str] | None = Field(
        default=None,
        max_length=10_000,
        description="Explicit trace ids (32 hex chars each). Rows land in this order.",
    )
    filters: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Keys: capability, has_error, received_at__gte/__lte (ISO 8601), model, "
            "service_name, session, conversation, min/max_duration_ms, total_tokens__gte/__lte, "
            "total_cost__gte/__lte, unbound. Unknown keys are refused."
        ),
    )
    search: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Free text over span name, service name, trace id and span id.",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=1_000_000,
        description="Take at most this many matching traces, newest first.",
    )
    intent: Literal["train", "eval"] | None = Field(
        default=None, description="Omit to let landing propose it from the rows."
    )
    capability: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Capability uuid. Omit to infer from the rows; null means none.",
    )
    split: SplitInput | None = Field(
        default=None,
        description=(
            "Land the selection as two datasets, `<name> train` and `<name> eval`, "
            "with disjoint rows. Cannot be combined with intent."
        ),
    )

    @model_validator(mode="after")
    def require_selection(self):
        if not self.trace_ids and not self.filters and not self.search:
            raise ValueError("provide trace_ids, or filters and/or search")
        if self.trace_ids and (self.filters or self.search):
            raise ValueError("give either trace_ids or a filter selection, not both")
        if self.split is not None and self.intent is not None:
            raise ValueError("split fixes the intents; omit intent")
        return self

    @field_validator("filters")
    @classmethod
    def filters_are_object(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        encoded = json.dumps(value, default=str)
        if len(encoded) > 12_000:
            raise ValueError("filters are too large")
        return value


class CreateDatasetFromLlmCallsInput(MCPModel):
    name: str = Field(min_length=1, max_length=255)
    capability: str = Field(min_length=1, max_length=255)
    since: str = Field(min_length=1, max_length=64)
    until: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=255)
    limit: int = Field(default=200, ge=1, le=10_000)
    intent: Literal["train", "eval"] = "eval"
    split: bool = False
    eval_percent: int = Field(default=20, ge=1, le=99)

    @model_validator(mode="after")
    def split_name_fits(self):
        # create_split appends " train" / " eval", and Dataset.name is 255.
        if self.split and len(self.name) > 249:
            raise ValueError("name must be at most 249 characters when split is set")
        return self


class TryDatasetScriptInput(MCPModel):
    dataset: str = _dataset_field()
    parent: str | None = _version_field()
    script: str = Field(min_length=1, max_length=_SCRIPT_INPUT_CHARS)


class ProbeDatasetVersionInput(MCPModel):
    dataset: str = _dataset_field()
    version: str | None = _version_field()
    script: str = Field(min_length=1, max_length=_SCRIPT_INPUT_CHARS)


class AddDatasetVersionInput(MCPModel):
    dataset: str = _dataset_field()
    parent: str | None = _version_field()
    title: str = Field(min_length=1, max_length=120)
    script: str = Field(min_length=1, max_length=_SCRIPT_INPUT_CHARS)


class AddSyntheticExamplesInput(MCPModel):
    dataset: str = _dataset_field()
    parent: str | None = _version_field()
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=50)
    instruction: str = Field(min_length=1, max_length=4_000)


class DiffDatasetVersionsInput(MCPModel):
    dataset: str = _dataset_field()
    start: str | None = Field(default=None, min_length=1, max_length=80, alias="from")
    to: str | None = _version_field()


class ActivateDatasetVersionInput(MCPModel):
    dataset: str = _dataset_field()
    version: str = Field(min_length=1, max_length=80)
    expected_active: str | None = _version_field()


class UpdateDatasetInput(MCPModel):
    dataset: str = _dataset_field()
    name: str | None = Field(default=None, min_length=1, max_length=255)
    intent: Literal["train", "eval"] | None = None
    capability: str | None = Field(default=None, min_length=1, max_length=255)


class ResumeDatasetImportInput(MCPModel):
    dataset: str = _dataset_field()


class ScriptRunOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    status: RunStatus
    execution: str | None = None
    transient: bool = False
    error: str | None = None
    stdout: str = Field(default="", max_length=_STDOUT_CHARS)
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=2)


class TryDatasetScriptOutput(ScriptRunOutput):
    rows: int | None = Field(default=None, ge=0)
    columns: list[str] = Field(default_factory=list, max_length=200)
    head: list[dict[str, Any]] = Field(default_factory=list, max_length=executions.PREVIEW_ROWS)


class AddDatasetVersionOutput(ScriptRunOutput):
    version: VersionSummary | None = None


class DiffDatasetVersionsOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    start: str = Field(alias="from", min_length=1, max_length=32)
    to: str = Field(min_length=1, max_length=32)
    diff: dict[str, Any] = Field(default_factory=dict)
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=2)


class DatasetOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    dataset: DatasetListItem
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=2)


def _mutation_ref(dataset) -> DatasetMutationRef:
    return DatasetMutationRef(
        id=str(dataset.id),
        name=dataset.name or "",
        state=dataset.state,
        resource=dataset_resource_link(dataset),
    )


def mutation_output(
    dataset,
    *,
    summary: str,
    traces: int | None = None,
    calls: int | None = None,
    eval_dataset=None,
) -> DatasetMutationOutput:
    links = [dataset_resource_link(dataset), dataset_import_job_link(dataset)]
    if eval_dataset is not None:
        links += [dataset_resource_link(eval_dataset), dataset_import_job_link(eval_dataset)]
    return DatasetMutationOutput(
        summary=_clip(summary, _SUMMARY_CHARS),
        dataset=_mutation_ref(dataset),
        job=DatasetJobReceipt(id=str(dataset.id), status=dataset.state, resource=links[1]),
        eval_dataset=_mutation_ref(eval_dataset) if eval_dataset is not None else None,
        traces=traces,
        calls=calls,
        resource_links=links,
    )


def _fit(cell: Cell, intent: str) -> FitReport:
    ok, reason = cell.fits(intent)
    return FitReport(ok=ok, reason=sanitize_error(reason, 500))


def _capability_ref(dataset) -> CapabilityRef | None:
    capability = dataset.capability
    if capability is None:
        return None
    return CapabilityRef(
        id=str(capability.id),
        name=capability.name or "Capability",
        resource=_capability_link(capability),
    )


def _active_version(dataset) -> ActiveVersion | None:
    cell = dataset.active
    if cell is None:
        return None
    return ActiveVersion(
        id=str(cell.id),
        version=cell.label,
        title=cell.title.strip() or "Version",
        rows=int(cell.rows or 0),
        fingerprint=cell.fingerprint or "",
        fits=_fit(cell, public_intent(dataset.intent)),
    )


def _list_fields(dataset) -> dict[str, Any]:
    return {
        "id": str(dataset.id),
        "name": dataset.name or "",
        "intent": public_intent(dataset.intent),
        "source_kind": dataset.source_kind,
        "state": dataset.state,
        "capability": _capability_ref(dataset),
        "active": _active_version(dataset),
        "resource": dataset_resource_link(dataset),
    }


def serialize_dataset_list_item(dataset) -> DatasetListItem:
    return DatasetListItem.model_validate(_list_fields(dataset))


def version_summary(dataset, cell: Cell) -> VersionSummary:
    columns = [
        _jsonable(col) if isinstance(col, dict) else {"name": str(col)}
        for col in (cell.columns or [])[:100]
    ]
    return VersionSummary(
        id=str(cell.id),
        version=cell.label,
        parent=cell.parent.label if cell.parent_id else None,
        title=cell.title.strip() or "Version",
        script=_clip(cell.script or ""),
        script_truncated=len(cell.script or "") > _SCRIPT_CHARS,
        rows=int(cell.rows or 0),
        columns=columns,
        fingerprint=cell.fingerprint or "",
        fits=_fit(cell, public_intent(dataset.intent)),
        intent_report=_jsonable(cell.intent_report or {}),
        capability_report=_jsonable(cell.capability_report or {}),
        impact=_jsonable(cell.impact or {}),
        used=cell.used_at is not None,
        created_at=cell.created_at,
    )


def _rank(raw) -> list[CapabilityRankItem]:
    out: list[CapabilityRankItem] = []
    for item in (raw or [])[:_RANK_CAP]:
        if not isinstance(item, dict) or not item.get("capability_id"):
            continue
        out.append(
            CapabilityRankItem(
                capability_id=str(item["capability_id"]),
                name=str(item.get("name") or "Capability")[:255],
                score=float(item.get("score") or 0),
                reason=_clip(str(item.get("reason") or ""), 500),
            )
        )
    return out


def _sample_cell(value: Any) -> Any:
    """One cell of the sample, bounded: a transcript row is tens of thousands
    of characters, and ``query_dataset`` reads any value in full."""
    value = _jsonable(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        return value if len(text) <= _SAMPLE_CELL_CHARS else text[:_SAMPLE_CELL_CHARS] + "…"
    if isinstance(value, str) and len(value) > _SAMPLE_CELL_CHARS:
        return value[:_SAMPLE_CELL_CHARS] + "…"
    return value


def _sample(dataset, cell: Cell | None) -> DatasetSample | None:
    if cell is None:
        return None
    path = paths.cell_path(dataset.id, cell.id)
    if not path.exists():
        return None
    try:
        rows = store.head(path, _SAMPLE_ROWS)
    except Exception:  # noqa: BLE001 — sample is optional; never leak store errors
        return None
    return DatasetSample(
        version=cell.label,
        cell_id=str(cell.id),
        rows=[{str(k): _sample_cell(v) for k, v in row.items()} for row in rows],
    )


def _human_action(dataset, active: Cell | None) -> DatasetHumanAction | None:
    project_id = str(dataset.project_id)
    if active is not None:
        return DatasetHumanAction(
            command="overmind dataset export DATASET --json",
            arguments={"dataset": str(dataset.id), "project_id": project_id},
        )
    if dataset.source_kind == Dataset.SourceKind.FILE:
        return DatasetHumanAction(
            command="overmind dataset upload FILE --json",
            arguments={"file": "<path>", "project_id": project_id},
        )
    return None


def next_actions(dataset, active: Cell | None) -> list[NextAction]:
    """The one answer to "what now" for a dataset. The arguments are the ones Overmind
    knows; the caller adds the rest (a title and script, an intent)."""
    ds_id = str(dataset.id)
    if dataset.state == Dataset.State.LANDING:
        return [
            NextAction(
                tool="get_job",
                reason="The source is landing.",
                arguments={"kind": "dataset_import", "id": ds_id},
            )
        ]
    if dataset.state == Dataset.State.ERROR:
        if imports.resumable_imports(dataset.pk).exists():
            return [
                NextAction(
                    tool="resume_dataset_import",
                    reason="Resume the retained source import.",
                    arguments={"dataset": ds_id},
                )
            ]
        return []
    if active is None:
        return []
    if dataset.intent == Dataset.Intent.PENDING:
        return [
            NextAction(
                tool="update_dataset",
                reason="Choose train or eval with intent.",
                arguments={"dataset": ds_id},
            )
        ]
    intent = public_intent(dataset.intent)
    ok, reason = active.fits(intent)
    if not ok:
        return [
            NextAction(
                tool="add_dataset_version",
                reason=sanitize_error(reason) or "The active version does not fit.",
                arguments={"dataset": ds_id, "parent": active.label},
            )
        ]
    args = {"dataset": ds_id, "cell": str(active.id)}
    if intent == Dataset.Intent.TRAIN:
        return [
            NextAction(
                tool="check_finetune_readiness", reason="Active version fits train.", arguments=args
            )
        ]
    actions = [
        NextAction(
            tool="check_evaluation_readiness", reason="Active version fits eval.", arguments=args
        )
    ]
    if dataset.capability_id:
        actions.append(
            NextAction(
                tool="check_optimizer_readiness",
                reason="Active version fits eval.",
                arguments={**args, "capability": str(dataset.capability_id)},
            )
        )
    return actions


def _detail_summary(dataset, total: int, active: Cell | None, running: int) -> str:
    if dataset.state == Dataset.State.ERROR:
        return _clip(
            f"Dataset error: {sanitize_error(dataset.error) or 'The source did not land.'}",
            _SUMMARY_CHARS,
        )
    if dataset.state == Dataset.State.LANDING:
        return "The source is landing."
    noun = "version" if total == 1 else "versions"
    text = f"{total} {noun}"
    if active is not None:
        text += f", active {active.label} with {int(active.rows)} rows"
    if running:
        text += f", {running} running"
    return text + "."


def _source(dataset) -> DatasetSource:
    spec = dataset.source_spec or {}
    described = spec.get("files") or (
        [{"filename": spec["filename"]}] if spec.get("filename") else []
    )
    return DatasetSource(
        files=[
            SourceFile(
                filename=str(item.get("filename", ""))[:255],
                bytes=item.get("bytes"),
                rows=item.get("rows"),
                extraction=_jsonable(item.get("extraction") or {}),
            )
            for item in described[:_LIST_CAP]
        ],
        limitations=list((spec.get("extraction") or {}).get("limitations", []))[:50],
    )


def serialize_dataset_detail(dataset, *, offset: int = 0, limit: int = _VERSION_DEFAULT):
    status = workshop.status(dataset, offset, limit)
    active = status["active"]
    links = [dataset_resource_link(dataset)]
    if dataset.state == Dataset.State.LANDING:
        links.append(dataset_import_job_link(dataset))
    return DatasetDetail.model_validate(
        {
            **_list_fields(dataset),
            "source": _source(dataset),
            "preparation_context": _jsonable(workshop_context(dataset)),
            "capability_rank": _rank(dataset.capability_rank),
            "contamination_report": _jsonable(dataset.source_spec.get("contamination_report", {})),
            "versions": [version_summary(dataset, cell) for cell in status["versions"]],
            "versions_total": status["total"],
            "running": status["running"],
            "sample": _sample(dataset, active),
            "next_actions": next_actions(dataset, active),
            "resource_links": links,
            "summary": _detail_summary(dataset, status["total"], active, status["running"]),
            "human_action": _human_action(dataset, active),
            "error": sanitize_error(dataset.error) or None,
        }
    )


def _run_fields(dataset, run: workshop.Run) -> dict[str, Any]:
    execution = run.execution
    fields: dict[str, Any] = {
        "status": run.status,
        "resource_links": [dataset_resource_link(dataset)],
    }
    if execution is not None:
        fields.update(
            execution=str(execution.id),
            transient=execution.transient,
            error=sanitize_error(execution.error, 2_000) or None,
            stdout=_clip(execution.stdout, _STDOUT_CHARS),
            queued_at=execution.queued_at,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
        )
    return fields


def _run_summary(run: workshop.Run, done: str) -> str:
    if run.status == Execution.State.QUEUED:
        return "Queued. Repeat the identical call to get the result."
    if run.status == Execution.State.RUNNING:
        return "Running. Repeat the identical call to get the result."
    if run.status == Execution.State.FAILED:
        execution = run.execution
        if execution is not None and execution.transient:
            return "The platform stopped the run; the identical call runs again."
        if execution is not None and execution.mode == Execution.Mode.SYNTHETIC:
            return "The rows were refused."
        return "The script failed."
    return _clip(done, _SUMMARY_CHARS)


def try_output(dataset, run: workshop.Run) -> TryDatasetScriptOutput:
    result = run.execution.result if run.execution is not None else {}
    summary = _run_summary(run, f"{result.get('rows', 0)} rows. No version was created.")
    return TryDatasetScriptOutput(
        summary=summary,
        **_run_fields(dataset, run),
        rows=result.get("rows"),
        columns=result.get("columns", []),
        head=[{str(k): _sample_cell(v) for k, v in row.items()} for row in result.get("head", [])],
    )


def probe_output(dataset, run: workshop.Run) -> ScriptRunOutput:
    return ScriptRunOutput(summary=_run_summary(run, "Done."), **_run_fields(dataset, run))


def version_output(dataset, run: workshop.Run) -> AddDatasetVersionOutput:
    cell = None
    done = "Done."
    if run.cell is not None:
        cell = Cell.objects.select_related("parent").get(pk=run.cell.pk)
        state = "active" if dataset.active_id == cell.pk else "not active"
        done = f"{cell.label} from {cell.parent.label}: {cell.rows} rows, {state}."
    return AddDatasetVersionOutput(
        summary=_run_summary(run, done),
        **_run_fields(dataset, run),
        version=version_summary(dataset, cell) if cell is not None else None,
    )


def dataset_output(dataset, summary: str) -> DatasetOutput:
    dataset = Dataset.objects.select_related("capability", "active").get(pk=dataset.pk)
    return DatasetOutput(
        summary=_clip(summary, _SUMMARY_CHARS),
        dataset=serialize_dataset_list_item(dataset),
        resource_links=[dataset_resource_link(dataset)],
    )
