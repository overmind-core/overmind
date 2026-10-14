"""Bounded MCP contracts for datasets, their versions and script runs."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from django.db.models import Q
from pydantic import AliasChoices, Field, model_validator

from overbae.models import Cell, Dataset, DatasetImport, Execution
from overbae.services.datasets import executions, imports, lifecycle, paths, store, workshop
from overbae.services.datasets.context import workshop_context
from overbae.services.datasets.contract import public_intent
from overbae.services.mcp.contracts.common import (
    JobReceipt,
    MCPModel,
    PageContract,
    ResourceLinkContract,
    VersionRef,
    request_key_field,
    version_field,
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
_USED_BY_CAP = 20
_LIVE_RUNS = 20
_PATH_RE = re.compile(r"(?:/[\w.-]+)+")

DatasetState = Literal[tuple(Dataset.State.values)]
RunStatus = Literal["queued", "running", "ok", "failed", "cancelled"]
SourceKind = Literal["file", "traces", "llm_calls", "version"]


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
    # Present only for decision rows, which decision models read instead of chat consumers.
    format: Literal["decision"] | None = Field(default=None, exclude_if=lambda value: value is None)


class CapabilityRef(MCPModel):
    id: str
    name: str = Field(min_length=1, max_length=255)
    resource: ResourceLinkContract


class ActiveVersion(VersionRef):
    title: str = Field(min_length=1, max_length=255)
    rows: int = Field(ge=0)
    fingerprint: str = Field(default="", max_length=64)
    fits: FitReport


class DatasetListItem(MCPModel):
    id: str
    name: str = Field(default="", max_length=255)
    intent: Literal["train", "eval", "pending"]
    source_kind: SourceKind
    state: DatasetState
    capability: CapabilityRef | None = None
    active: ActiveVersion | None = None
    resource: ResourceLinkContract


class VersionAttachment(MCPModel):
    filename: str = Field(default="", max_length=255)
    rows: int | None = Field(default=None, ge=0)


class VersionRow(VersionRef):
    parent: str | None = Field(default=None, max_length=32)
    title: str = Field(min_length=1, max_length=255)
    rows: int = Field(ge=0)
    fits: bool
    used: bool
    attachment: VersionAttachment | None = None
    created_at: datetime


class VersionUse(MCPModel):
    kind: Literal["eval_run", "finetune_job", "optimizer_experiment"]
    id: str
    name: str = Field(default="", max_length=255)


class VersionSummary(VersionRef):
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
    analytics: dict[str, Any] = Field(default_factory=dict)
    attachment: dict[str, Any] = Field(default_factory=dict)
    used: bool
    used_by: list[VersionUse] = Field(default_factory=list, max_length=_USED_BY_CAP)
    created_at: datetime


class CapabilityRankItem(MCPModel):
    capability_id: str
    name: str = Field(min_length=1, max_length=255)
    score: float = 0.0
    reason: str = Field(default="", max_length=500)


class DatasetSample(VersionRef):
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


class SourceVersion(VersionRef):
    dataset: str
    name: str = Field(default="", max_length=255)
    fingerprint: str = Field(default="", max_length=64)
    resource: ResourceLinkContract


class SplitSibling(MCPModel):
    id: str
    name: str = Field(default="", max_length=255)
    resource: ResourceLinkContract


class SplitPair(MCPModel):
    role: Literal["train", "eval"]
    sibling: SplitSibling | None = None
    eval_percent: int = Field(ge=1, le=99)


class DatasetSource(MCPModel):
    files: list[SourceFile] = Field(default_factory=list, max_length=_LIST_CAP)
    limitations: list[str] = Field(default_factory=list, max_length=50)
    version: SourceVersion | None = None
    split: SplitPair | None = None


class LiveRun(MCPModel):
    id: str
    mode: Literal["try", "probe", "cell", "synthetic"]
    title: str = Field(default="", max_length=120)
    state: Literal["queued", "running"]
    queued_at: datetime
    job: ResourceLinkContract


class DatasetDetail(DatasetListItem):
    active: VersionSummary | None = None
    selected: VersionSummary | None = None
    source: DatasetSource
    preparation_context: dict[str, Any] = Field(default_factory=dict)
    contamination_report: dict[str, Any] = Field(default_factory=dict)
    capability_rank: list[CapabilityRankItem] = Field(default_factory=list, max_length=_RANK_CAP)
    versions: list[VersionRow] = Field(default_factory=list, max_length=_VERSION_CAP)
    versions_total: int = Field(ge=0)
    running: list[LiveRun] = Field(default_factory=list, max_length=_LIVE_RUNS)
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
    traces: int | None = Field(default=None, ge=0)
    calls: int | None = Field(default=None, ge=0)
    resource_links: list[ResourceLinkContract] = Field(max_length=4)


def _dataset_field():
    return Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("dataset", "dataset_id"),
    )


class ListDatasetsInput(MCPModel):
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    intent: Literal["train", "eval", "pending"] | None = None
    state: DatasetState | None = None
    search: str | None = Field(default=None, min_length=1, max_length=255)
    limit: int = Field(default=20, ge=1, le=_LIST_CAP)
    offset: int = Field(default=0, ge=0)
    cursor: str | None = Field(default=None, min_length=1, max_length=20)


class ListDatasetsOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    datasets: list[DatasetListItem] = Field(default_factory=list, max_length=_LIST_CAP)
    page: PageContract
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=_LIST_CAP)


class InspectDatasetInput(MCPModel):
    dataset: str = _dataset_field()
    version: str | None = version_field()
    version_offset: int = Field(default=0, ge=0)
    version_limit: int = Field(default=_VERSION_DEFAULT, ge=1, le=_VERSION_CAP)


class QueryDatasetInput(MCPModel):
    dataset: str = _dataset_field()
    sql: str = Field(
        min_length=1,
        max_length=8_000,
        description="One DuckDB SELECT over table t. source_row is row identity, not data.",
    )
    version: str | None = version_field()
    limit: int = Field(default=_QUERY_ROWS, ge=1, le=_QUERY_ROWS)


class QueryDatasetOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    dataset: str
    version: VersionRef
    columns: list[str] = Field(default_factory=list, max_length=200)
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=_QUERY_ROWS)
    n: int = Field(ge=0, le=_QUERY_ROWS)
    truncated: bool = False
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=2)


class SplitInput(MCPModel):
    eval_percent: int = Field(default=20, ge=1, le=99)
    position: Literal["head", "tail", "random", "hash"] | None = Field(
        default=None,
        description="Where eval rows come from; default tail, and llm_calls only hash span_id.",
    )
    group_by: list[str] = Field(default_factory=list, max_length=10)
    stratify_by: str | None = Field(default=None, max_length=255)
    deduplicate: bool = True


class TracesSource(MCPModel):
    kind: Literal["traces"]
    trace_ids: list[str] | None = Field(default=None, max_length=10_000)
    filters: dict[str, Any] | None = Field(
        default=None,
        description="query_traces arguments or traces-list filters; unknown keys are refused.",
    )
    search: str | None = Field(default=None, min_length=1, max_length=255)
    limit: int | None = Field(default=None, ge=1, le=1_000_000, description="Newest first.")

    @model_validator(mode="after")
    def require_selection(self):
        if not self.trace_ids and not self.filters and not self.search:
            raise ValueError("provide trace_ids, or filters and/or search")
        if self.trace_ids and (self.filters or self.search):
            raise ValueError("give either trace_ids or a filter selection, not both")
        if self.filters is not None and len(json.dumps(self.filters, default=str)) > 12_000:
            raise ValueError("filters are too large")
        return self


class LlmCallsSource(MCPModel):
    kind: Literal["llm_calls"]
    since: str = Field(min_length=1, max_length=64)
    until: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=255)
    limit: int = Field(default=200, ge=1, le=10_000)


class VersionSource(MCPModel):
    kind: Literal["version"]
    dataset: str = Field(min_length=1, max_length=255)
    version: str | None = version_field()


class CreateDatasetInput(MCPModel):
    name: str = Field(min_length=1, max_length=255)
    source: TracesSource | LlmCallsSource | VersionSource = Field(discriminator="kind")
    intent: Literal["train", "eval"] | None = Field(
        default=None, description="Omit to propose it from the rows, or keep a version's."
    )
    capability: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Id, name or slug. Omit to infer it, or keep a version's; null means none.",
    )
    split: SplitInput | None = Field(
        default=None, description="Land `<name> train` and `<name> eval` with disjoint rows."
    )
    request_key: str | None = request_key_field()

    @model_validator(mode="after")
    def check_shape(self):
        if self.split is not None and self.intent is not None:
            raise ValueError("split fixes the intents; omit intent")
        if self.split is not None and len(self.name) > 249:
            raise ValueError("name must be at most 249 characters when split is set")
        if self.source.kind == "llm_calls":
            if not self.capability:
                raise ValueError("llm_calls needs capability")
            if self.split is None and self.intent is None:
                raise ValueError("llm_calls needs intent or split")
        return self


def _attachment_field():
    return Field(
        default=None,
        min_length=36,
        max_length=36,
        description="upload_id from `overmind dataset attach FILE --json`; the script reads "
        "it as the frame attachment.",
    )


class TryDatasetScriptInput(MCPModel):
    dataset: str = _dataset_field()
    parent: str | None = version_field()
    script: str = Field(min_length=1, max_length=_SCRIPT_INPUT_CHARS)
    attachment: str | None = _attachment_field()


class ProbeDatasetVersionInput(MCPModel):
    dataset: str = _dataset_field()
    version: str | None = version_field()
    script: str = Field(min_length=1, max_length=_SCRIPT_INPUT_CHARS)


class AddDatasetVersionInput(MCPModel):
    dataset: str = _dataset_field()
    parent: str | None = version_field()
    title: str = Field(min_length=1, max_length=120)
    script: str = Field(min_length=1, max_length=_SCRIPT_INPUT_CHARS)
    attachment: str | None = _attachment_field()


class AddSyntheticExamplesInput(MCPModel):
    dataset: str = _dataset_field()
    parent: str | None = version_field()
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=50)
    instruction: str = Field(min_length=1, max_length=4_000)


class DiffDatasetVersionsInput(MCPModel):
    dataset: str = _dataset_field()
    start: str | None = Field(default=None, min_length=1, max_length=80, alias="from")
    to: str | None = version_field()


class ActivateDatasetVersionInput(MCPModel):
    dataset: str = _dataset_field()
    version: str = Field(min_length=1, max_length=80)
    expected_active: str | None = version_field()


class UpdateDatasetInput(MCPModel):
    dataset: str = _dataset_field()
    name: str | None = Field(default=None, min_length=1, max_length=255)
    intent: Literal["train", "eval"] | None = None
    capability: str | None = Field(default=None, min_length=1, max_length=255)


class ResumeDatasetImportInput(MCPModel):
    dataset: str = _dataset_field()


class CancelDatasetJobInput(MCPModel):
    dataset: str = _dataset_field()
    run: str | None = Field(
        default=None,
        min_length=36,
        max_length=36,
        description="Script run id; omit for the import.",
    )


class CancelDatasetJobOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    kind: Literal["dataset_import", "dataset_run"]
    id: str
    status: str = Field(min_length=1, max_length=16)
    datasets: list[DatasetMutationRef] = Field(default_factory=list, max_length=2)
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=3)


class RunReceipt(JobReceipt):
    kind: Literal["dataset_run"] = "dataset_run"


class ScriptRunOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    status: RunStatus
    run: str | None = None
    job: RunReceipt | None = None
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
    created: bool = False
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


def mutation_ref(dataset) -> DatasetMutationRef:
    return DatasetMutationRef(
        id=str(dataset.id),
        name=dataset.name or "",
        state=dataset.state,
        resource=dataset_resource_link(dataset),
    )


def import_receipt(dataset) -> DatasetImport | None:
    return (
        DatasetImport.objects.filter(Q(dataset=dataset) | Q(evaluation=dataset))
        .order_by("-queued_at")
        .first()
    )


STOPPED = "stopped"
_LANDED_STATUS = {
    Dataset.State.LANDING: DatasetImport.State.QUEUED,
    Dataset.State.IDLE: "landed",
    Dataset.State.ERROR: "failed",
}


def import_status(dataset, receipt: DatasetImport | None) -> str:
    """The import's own state: queued, running, blocked, landed, failed or stopped."""
    if receipt is None:
        return _LANDED_STATUS.get(dataset.state, dataset.state)
    if receipt.state == DatasetImport.State.COMPLETE:
        return "landed"
    if receipt.state == DatasetImport.State.CANCELLED:
        return STOPPED
    if receipt.state == DatasetImport.State.BLOCKED and not (
        imports.resumable_imports(dataset.pk).filter(pk=receipt.pk).exists()
    ):
        return "failed"
    return receipt.state


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
        dataset=mutation_ref(dataset),
        job=DatasetJobReceipt(
            id=str(dataset.id),
            status=import_status(dataset, import_receipt(dataset)),
            resource=links[1],
        ),
        eval_dataset=mutation_ref(eval_dataset) if eval_dataset is not None else None,
        traces=traces,
        calls=calls,
        resource_links=links,
    )


def _fit(cell: Cell, intent: str) -> FitReport:
    decision = (cell.intent_report or {}).get("decision") or {}
    if decision.get("reason") != "no decision rows" and decision:
        ok, reason = cell.fits_decision()
        return FitReport(ok=ok, reason=sanitize_error(reason, 500), format="decision")
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


def version_row(dataset, cell: Cell) -> VersionRow:
    attachment = cell.attachment or {}
    return VersionRow(
        id=str(cell.id),
        version=cell.label,
        parent=cell.parent.label if cell.parent_id else None,
        title=cell.title.strip() or "Version",
        rows=int(cell.rows or 0),
        fits=_fit(cell, public_intent(dataset.intent)).ok,
        used=cell.used_at is not None,
        attachment=VersionAttachment(
            filename=str(attachment.get("filename") or "")[:255], rows=attachment.get("rows")
        )
        if attachment
        else None,
        created_at=cell.created_at,
    )


_USE_KINDS = (
    ("eval_runs", "eval_run"),
    ("finetuning_jobs", "finetune_job"),
    ("optimizer_experiments", "optimizer_experiment"),
)


def _used_by(cell: Cell) -> list[VersionUse]:
    usage = lifecycle.usage(cell)
    return [
        VersionUse(kind=kind, id=row["id"], name=str(row.get("name") or "")[:255])
        for key, kind in _USE_KINDS
        for row in usage[key]
    ][:_USED_BY_CAP]


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
        analytics=_jsonable(cell.analytics or {}),
        attachment=_jsonable(cell.attachment or {}),
        used=cell.used_at is not None,
        used_by=_used_by(cell),
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
        id=str(cell.id),
        version=cell.label,
        rows=[{str(k): _sample_cell(v) for k, v in row.items()} for row in rows],
    )


def _human_action(dataset, active: Cell | None) -> DatasetHumanAction | None:
    if active is not None:
        return DatasetHumanAction(
            command="overmind dataset export DATASET --cell VERSION --json",
            arguments={"dataset": str(dataset.id), "version": active.label},
        )
    if dataset.source_kind == Dataset.SourceKind.FILE:
        return DatasetHumanAction(
            command="overmind dataset upload FILE --project-id PROJECT --json",
            arguments={"file": "<path>", "project": str(dataset.project_id)},
        )
    return None


def split_pair(dataset) -> SplitPair | None:
    """The train/eval pair a split landing made, from the import that made it."""
    receipt = (
        DatasetImport.objects.filter(Q(dataset=dataset) | Q(evaluation=dataset))
        .select_related("dataset", "evaluation")
        .order_by("-queued_at")
        .first()
    )
    split = (receipt.inputs or {}).get("split") if receipt is not None else None
    if not split:
        return None
    role = "train" if receipt.dataset_id == dataset.pk else "eval"
    sibling = receipt.evaluation if role == "train" else receipt.dataset
    return SplitPair(
        role=role,
        sibling=SplitSibling(
            id=str(sibling.id), name=sibling.name or "", resource=dataset_resource_link(sibling)
        )
        if sibling is not None
        else None,
        eval_percent=int(split.get("eval_percent") or 20),
    )


def _fits_for(dataset, cell: Cell) -> bool:
    if cell.fits_decision()[0]:
        return True
    return cell.fits(public_intent(dataset.intent))[0]


def _activation(dataset, active: Cell) -> NextAction | None:
    newest = dataset.cells.order_by("-position").first()
    if newest is None or newest.pk == active.pk or not _fits_for(dataset, newest):
        return None
    return NextAction(
        tool="activate_dataset_version",
        reason=f"{newest.label} is newer than the active {active.label} and fits; "
        "activate it if the user agrees.",
        arguments={
            "dataset": str(dataset.id),
            "version": newest.label,
            "expected_active": active.label,
        },
    )


def next_actions(dataset, active: Cell | None, pair: SplitPair | None = None) -> list[NextAction]:
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
    if dataset.state == STOPPED:
        return [
            NextAction(
                tool="create_dataset",
                reason="The import was stopped; create the dataset again from its source.",
                arguments={"name": dataset.name or ""},
            )
        ]
    if dataset.state != Dataset.State.IDLE:
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
    activation = _activation(dataset, active)
    actions = [activation] if activation is not None else []
    sibling = pair.sibling.id if pair is not None and pair.sibling is not None else None
    if active.fits_decision()[0]:
        return actions + _decision_next_actions(dataset, active, sibling)
    intent = public_intent(dataset.intent)
    ok, reason = active.fits(intent)
    if not ok:
        return actions + [
            NextAction(
                tool="add_dataset_version",
                reason=sanitize_error(reason) or "The active version does not fit.",
                arguments={"dataset": ds_id, "parent": active.label},
            )
        ]
    args = {"dataset": ds_id, "version": active.label}
    if intent == Dataset.Intent.TRAIN:
        if sibling:
            args["eval_dataset"] = sibling
        if dataset.capability_id:
            args["capability"] = str(dataset.capability_id)
        return actions + [
            NextAction(
                tool="check_finetune_readiness", reason="Active version fits train.", arguments=args
            )
        ]
    actions.append(
        NextAction(
            tool="check_evaluation_readiness", reason="Active version fits eval.", arguments=args
        )
    )
    if dataset.capability_id:
        actions.append(
            NextAction(
                tool="check_optimizer_readiness",
                reason="Active version fits eval.",
                arguments={**args, "capability": str(dataset.capability_id)},
            )
        )
    return actions


def _decision_next_actions(dataset, active: Cell, sibling: str | None) -> list[NextAction]:
    """Decision rows are read only by decision models, so they take their own readiness check."""
    if public_intent(dataset.intent) == Dataset.Intent.EVAL:
        if sibling:
            return [
                NextAction(
                    tool="check_decision_readiness",
                    reason="Active version holds decision rows; its split sibling trains.",
                    arguments={
                        "dataset": sibling,
                        "eval_dataset": str(dataset.id),
                        "eval_version": active.label,
                    },
                )
            ]
        return [
            NextAction(
                tool="list_datasets",
                reason=(
                    "Active version holds decision rows; check it with a train dataset: "
                    "check_decision_readiness with this as eval_dataset."
                ),
                arguments={},
            )
        ]
    args = {"dataset": str(dataset.id), "version": active.label}
    if sibling:
        args["eval_dataset"] = sibling
    return [
        NextAction(
            tool="check_decision_readiness",
            reason="Active version holds decision rows.",
            arguments=args,
        )
    ]


def _detail_summary(dataset, total: int, active: Cell | None, running: int) -> str:
    if dataset.state == Dataset.State.LANDING:
        return "The source is landing."
    if dataset.state == STOPPED:
        return "Import stopped; no rows were imported."
    if dataset.state != Dataset.State.IDLE:
        return _clip(
            f"Dataset {dataset.state}: "
            f"{sanitize_error(dataset.error) or 'The source did not land.'}",
            _SUMMARY_CHARS,
        )
    noun = "version" if total == 1 else "versions"
    text = f"{total} {noun}"
    if active is not None:
        text += f", active {active.label} with {int(active.rows)} rows"
    if running:
        text += f", {running} running"
    return text + "."


def _source_version(dataset) -> SourceVersion | None:
    spec = dataset.source_spec or {}
    if dataset.source_kind != Dataset.SourceKind.VERSION or not spec.get("cell_id"):
        return None
    ds_id = quote(str(spec["dataset_id"]), safe="")
    return SourceVersion(
        dataset=str(spec["dataset_id"]),
        name=str(spec.get("dataset_name") or "")[:255],
        version=str(spec.get("version") or "v0")[:32],
        id=str(spec["cell_id"]),
        fingerprint=str(spec.get("fingerprint") or "")[:64],
        resource=_link(f"overmind://datasets/{ds_id}", spec.get("dataset_name") or "Dataset"),
    )


def _source(dataset, pair: SplitPair | None) -> DatasetSource:
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
        version=_source_version(dataset),
        split=pair,
    )


def run_job_link(run: Execution) -> ResourceLinkContract:
    return _link(
        f"overmind://jobs/dataset_run/{quote(str(run.id), safe='')}",
        (run.title or f"{run.mode} run")[:160],
    )


def _live_run(run: Execution) -> LiveRun:
    return LiveRun(
        id=str(run.id),
        mode=run.mode,
        title=run.title or "",
        state=run.state,
        queued_at=run.queued_at,
        job=run_job_link(run),
    )


def serialize_dataset_detail(
    dataset, *, offset: int = 0, limit: int = _VERSION_DEFAULT, selected: Cell | None = None
):
    status = workshop.status(dataset, offset, limit, live=_LIVE_RUNS)
    active = status["active"]
    pair = split_pair(dataset)
    links = [dataset_resource_link(dataset)]
    if dataset.state == Dataset.State.LANDING:
        links.append(dataset_import_job_link(dataset))
    if pair is not None and pair.sibling is not None:
        links.append(pair.sibling.resource)
    return DatasetDetail.model_validate(
        {
            **_list_fields(dataset),
            "active": version_summary(dataset, active) if active is not None else None,
            "selected": version_summary(dataset, selected)
            if selected is not None and (active is None or selected.pk != active.pk)
            else None,
            "source": _source(dataset, pair),
            "preparation_context": _jsonable(workshop_context(dataset)),
            "capability_rank": _rank(dataset.capability_rank),
            "contamination_report": _jsonable(dataset.source_spec.get("contamination_report", {})),
            "versions": [version_row(dataset, cell) for cell in status["versions"]],
            "versions_total": status["total"],
            "running": [_live_run(run) for run in status["running"]],
            "sample": _sample(dataset, selected or active),
            "next_actions": next_actions(dataset, active, pair),
            "resource_links": links,
            "summary": _detail_summary(dataset, status["total"], active, status["running_total"]),
            "human_action": _human_action(dataset, active),
            "error": sanitize_error(dataset.error) or None,
        }
    )


_TERMINAL = (Execution.State.OK, Execution.State.FAILED, Execution.State.CANCELLED)


def _run_fields(dataset, run: workshop.Run) -> dict[str, Any]:
    execution = run.execution
    fields: dict[str, Any] = {
        "status": run.status,
        "resource_links": [dataset_resource_link(dataset)],
    }
    if execution is not None:
        fields.update(
            run=str(execution.id),
            transient=execution.transient,
            error=sanitize_error(execution.error, 2_000) or None,
            stdout=_clip(execution.stdout, _STDOUT_CHARS),
            queued_at=execution.queued_at,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
        )
        if execution.state not in _TERMINAL:
            link = run_job_link(execution)
            fields["job"] = RunReceipt(id=str(execution.id), status=execution.state, resource=link)
            fields["resource_links"].append(link)
    return fields


def _run_summary(run: workshop.Run, done: str) -> str:
    if run.status in (Execution.State.QUEUED, Execution.State.RUNNING):
        return f"{run.status.capitalize()}. Poll get_job with kind dataset_run and this run id."
    if run.status == Execution.State.CANCELLED:
        return "Stopped. The identical call runs it again."
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
        if not run.created:
            done = (
                f"{cell.label} already holds this parent, script and attachment; "
                f"nothing was created. {cell.rows} rows, {state}."
            )
    return AddDatasetVersionOutput(
        summary=_run_summary(run, done),
        **_run_fields(dataset, run),
        created=run.created and cell is not None,
        version=version_summary(dataset, cell) if cell is not None else None,
    )


def dataset_output(dataset, summary: str) -> DatasetOutput:
    dataset = Dataset.objects.select_related("capability", "active").get(pk=dataset.pk)
    return DatasetOutput(
        summary=_clip(summary, _SUMMARY_CHARS),
        dataset=serialize_dataset_list_item(dataset),
        resource_links=[dataset_resource_link(dataset)],
    )


def run_payload(run: Execution) -> dict[str, Any]:
    """A script run as get_job and its resource report it."""
    payload: dict[str, Any] = {
        "run": str(run.id),
        "mode": run.mode,
        "title": run.title or "",
        "status": run.state,
        "dataset": str(run.dataset_id),
        "parent": run.parent.label if run.parent_id else None,
        "transient": run.transient,
        "error": sanitize_error(run.error, 2_000) or None,
        "stdout": _clip(run.stdout, _STDOUT_CHARS),
        "queued_at": run.queued_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }
    if run.state == Execution.State.OK and run.cell_id:
        payload["version"] = VersionRef(id=str(run.cell_id), version=run.cell.label).model_dump()
    if run.mode == Execution.Mode.TRY and run.state == Execution.State.OK:
        result = run.result or {}
        payload.update(
            rows=result.get("rows"),
            columns=list(result.get("columns", []))[:200],
            head=[
                {str(k): _sample_cell(v) for k, v in row.items()} for row in result.get("head", [])
            ],
        )
    return payload
