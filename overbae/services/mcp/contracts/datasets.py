"""Bounded MCP contracts for Dataset + Cell. No ingestion, workshop, ft, or surface."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import AliasChoices, Field, field_validator, model_validator

from overbae.models import Cell, Dataset
from overbae.services.datasets import imports, review, transformation
from overbae.services.datasets.context import workshop_context
from overbae.services.datasets.contract import public_intent
from overbae.services.mcp.contracts.common import (
    JobReceipt,
    MCPModel,
    PageContract,
    ResourceLinkContract,
)

_LIST_CAP = 100
_CELL_CAP = 50
_SAMPLE_ROWS = 5
_SAMPLE_CELL_CHARS = 600
_QUERY_ROWS = 100
_SCRIPT_CHARS = 8_000
_RANK_CAP = 20
_SUMMARY_CHARS = 240
_BUSY = (Dataset.State.LANDING, Dataset.State.RUNNING)
_PATH_RE = re.compile(r"(?:/[\w.-]+)+")


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
        return value if math.isfinite(value) else None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return _clip(str(value))


def _json_bytes(value):
    return len(json.dumps(value, ensure_ascii=False).encode())


def _bounded(value, limit=2500):
    value = _jsonable(value)
    if _json_bytes(value) <= limit:
        return value
    if isinstance(value, dict):
        result = {"truncated": True}
        share = max(30, limit // max(1, len(value)) - 30)
        for key, item in value.items():
            candidate = {**result, key: _bounded(item, share)}
            if _json_bytes(candidate) <= limit:
                result = candidate
        return result
    if isinstance(value, list):
        result = {"items": [], "total": len(value), "truncated": True}
        for item in value:
            candidate = {**result, "items": [*result["items"], _bounded(item, max(30, limit // 4))]}
            if _json_bytes(candidate) > limit:
                break
            result = candidate
        return result if _json_bytes(result) <= limit else {"truncated": True}
    text = str(value)
    while _json_bytes(text + "…") > limit:
        text = text[: max(0, len(text) // 2)]
    return text + "…"


def _quality_summary(report):
    result = review.summary(report or {})
    for key in ("audit", "audits"):
        if key in result:
            result[key] = _bounded(result[key], 500)
    result["checks"] = [
        {**check, "evidence": _clip(check.get("evidence", ""), 160)}
        for check in result.get("checks", [])
    ]
    if result.get("semantic_audit"):
        result["semantic_audit"] = {
            **result["semantic_audit"],
            "definitions": _bounded(result["semantic_audit"].get("definitions", []), 1000),
        }
    return result


def _source_summary(source):
    # Page whole entries: clipping extraction metadata can erase OCR limitations.
    return {
        key: source[key]
        for key in ("id", "sha256", "filename", "bytes", "rows", "extraction")
        if key in source
    }


def sanitize_error(value: str, limit: int = 500) -> str:
    return _clip(_PATH_RE.sub("<path>", (value or "").strip()), limit)


def _link(uri: str, title: str) -> ResourceLinkContract:
    return ResourceLinkContract(uri=uri, title=_clip(title.strip() or "Dataset", 160))


def dataset_resource_link(dataset) -> ResourceLinkContract:
    ds_id = quote(str(dataset.id), safe="")
    return _link(f"overmind://datasets/{ds_id}", dataset.name or "Dataset")


def dataset_run_job_link(dataset) -> ResourceLinkContract:
    ds_id = quote(str(dataset.id), safe="")
    title = f"{dataset.name or 'Dataset'} run"
    return _link(f"overmind://jobs/dataset_run/{ds_id}", title)


def _capability_link(capability) -> ResourceLinkContract:
    cap_id = quote(str(capability.id), safe="")
    return _link(f"overmind://capabilities/{cap_id}", capability.name or "Capability")


def _cell_link(dataset, cell) -> ResourceLinkContract:
    """A cell is read through its dataset: the server serves no per-cell resource."""
    ds_id = quote(str(dataset.id), safe="")
    return _link(f"overmind://datasets/{ds_id}", cell.title.strip() or "Cell")


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
    intent: Literal["train", "eval", "explore", "pending"]
    source_kind: Literal["file", "traces", "llm_calls", "pending"]
    state: Literal["landing", "idle", "running", "error"]
    capability: CapabilityRef | None = None
    active: ActiveVersion | None = None
    resource: ResourceLinkContract


class CellTransformation(MCPModel):
    execution: Literal[
        "source", "unrecorded", "isolated_container", "platform_operations", "external_import"
    ]
    run: str | None = Field(max_length=36)
    pipeline: str | None = Field(max_length=36)
    revision: int | None = Field(ge=1)
    package: str | None = Field(max_length=36)
    package_sha256: str = Field(max_length=64)
    entrypoint: str = Field(max_length=255)
    provenance: str = Field(max_length=8000)


class CellSummary(MCPModel):
    preparation_plan: dict[str, Any] = Field(default_factory=dict)
    id: str
    position: int = Field(ge=0)
    version: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=255)
    script: str = Field(default="", max_length=_SCRIPT_CHARS)
    script_truncated: bool = False
    transformation: CellTransformation
    note: str = Field(default="", max_length=512)
    note_truncated: bool = False
    state: Literal["queued", "running", "ok", "failed"]
    error: str | None = None
    frozen: bool
    rows: int = Field(ge=0)
    columns: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    fingerprint: str = Field(default="", max_length=64)
    input_fingerprint: str = Field(default="", max_length=64)
    intent_report: dict[str, Any] = Field(default_factory=dict)
    capability_report: dict[str, Any] = Field(default_factory=dict)
    fits: FitReport
    seconds: float = 0.0
    review: dict[str, Any] = Field(default_factory=dict)
    quality_report: dict[str, Any] = Field(default_factory=dict)
    readiness: dict[str, Any] = Field(default_factory=dict)
    used_at: datetime | None = None
    resource: ResourceLinkContract


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


class DatasetDetail(DatasetListItem):
    operation: dict[str, Any] = Field(default_factory=dict)
    landing_progress: dict[str, Any] | None = Field(
        default=None,
        description="Latest file stage; completed/total count files. PDF OCR adds pages_total, ocr_pages_completed and ocr_pages_total. Not an ETA or overall percentage.",
    )
    preparation_plan: dict[str, Any] = Field(default_factory=dict)
    brief: str = Field(default="", max_length=8000)
    sources: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    sources_total: int = Field(default=0, ge=0)
    source_page: PageContract
    preparation_context: dict[str, Any] = Field(default_factory=dict)
    contamination_report: dict[str, Any] = Field(default_factory=dict)
    capability_rank: list[CapabilityRankItem] = Field(default_factory=list, max_length=_RANK_CAP)
    cells: list[CellSummary] = Field(default_factory=list, max_length=_CELL_CAP)
    cells_truncated: bool = False
    cell_page: PageContract
    truncated_fields: list[str] = Field(default_factory=list)
    sample: DatasetSample | None = None
    next_actions: list[NextAction] = Field(default_factory=list, max_length=8)
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=8)
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    human_action: DatasetHumanAction | None = None
    error: str | None = None


class DatasetMutationRef(MCPModel):
    id: str
    name: str = Field(default="", max_length=255)
    state: Literal["landing", "idle", "running", "error"]
    resource: ResourceLinkContract


class DatasetJobReceipt(JobReceipt):
    kind: Literal["dataset_run"] = "dataset_run"


class DatasetUploadHandoff(MCPModel):
    argv: list[str]
    replace: dict[str, str]


class DatasetMutationOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    dataset: DatasetMutationRef
    job: DatasetJobReceipt
    eval_dataset: DatasetMutationRef | None = None
    upload: DatasetUploadHandoff | None = None
    # Set by create_dataset_from_traces: the traces the selection resolved to.
    traces: int | None = Field(default=None, ge=0)
    # Set by create_dataset_from_llm_calls: spans matching the selection.
    calls: int | None = Field(default=None, ge=0)
    resource_links: list[ResourceLinkContract] = Field(max_length=4)


class ListDatasetsInput(MCPModel):
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    intent: Literal["train", "eval", "explore", "pending"] | None = None
    state: Literal["landing", "idle", "running", "error"] | None = None
    search: str | None = Field(default=None, min_length=1, max_length=255)
    limit: int = Field(default=20, ge=1, le=_LIST_CAP)
    offset: int = Field(default=0, ge=0)


class ListDatasetsOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    datasets: list[DatasetListItem] = Field(default_factory=list, max_length=_LIST_CAP)
    page: PageContract
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=_LIST_CAP)


class InspectDatasetInput(MCPModel):
    dataset: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("dataset", "dataset_id"),
    )
    cell_offset: int = Field(default=0, ge=0)
    cell_limit: int = Field(default=5, ge=1, le=20)
    source_offset: int = Field(default=0, ge=0)
    source_limit: int = Field(default=10, ge=1, le=20)


class QueryDatasetInput(MCPModel):
    dataset: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("dataset", "dataset_id"),
    )
    sql: str = Field(
        min_length=1,
        max_length=8_000,
        description="One SELECT over the table `t`, the chosen cell. DuckDB dialect; nested columns have JSON type. "
        "`source_row` is the row's identity in the source, not data.",
    )
    cell: str | None = Field(default=None, min_length=1, max_length=80)
    limit: int = Field(default=_QUERY_ROWS, ge=1, le=_QUERY_ROWS)


class QueryDatasetOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    dataset: str
    cell_id: str
    version: str | None = None
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
    """One row lands per trace. Either ``trace_ids`` or a filter selection, never both."""

    name: str = Field(min_length=1, max_length=255, description="Dataset name.")
    brief: str = Field(default="", max_length=8000)
    trace_ids: list[str] | None = Field(
        default=None,
        max_length=10_000,
        description="Explicit trace ids (32 hex chars each). Rows land in this order.",
    )
    filters: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Traces-list filters. Keys: capability (uuid), has_error (true|false), "
            "received_at__gte / received_at__lte (ISO 8601), model, service_name, "
            "session (uuid), conversation, min_duration_ms / max_duration_ms, "
            "total_tokens__gte / __lte, total_cost__gte / __lte, unbound (true|false). "
            "An unknown key is refused."
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
        default=None,
        description="Omit to explore before choosing training or evaluation. Landing leaves purpose pending.",
    )
    capability: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Destination capability uuid. If present in a trace, land its unique invocation "
            "and descendants; repeated invocations fail. Otherwise retain the complete source "
            "trace. A source capability filter takes precedence for extraction. Omit to infer "
            "from source rows; null means none."
        ),
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
    links = [dataset_resource_link(dataset), dataset_run_job_link(dataset)]
    if eval_dataset is not None:
        links += [dataset_resource_link(eval_dataset), dataset_run_job_link(eval_dataset)]
    return DatasetMutationOutput(
        summary=_clip(summary, _SUMMARY_CHARS),
        upload=DatasetUploadHandoff(
            argv=[
                "overmind",
                "dataset",
                "upload",
                "FILE",
                "--project-id",
                str(dataset.project_id),
                "--dataset",
                str(dataset.id),
                "--json",
            ],
            replace={
                "FILE": "Absolute local file path; add --json-rows-field FIELD for a selected JSON wrapper array."
            },
        )
        if dataset.source_kind == "pending"
        else None,
        dataset=_mutation_ref(dataset),
        job=DatasetJobReceipt(
            kind="dataset_run",
            id=str(dataset.id),
            status=dataset.state,
            resource=links[1],
        ),
        eval_dataset=_mutation_ref(eval_dataset) if eval_dataset is not None else None,
        traces=traces,
        calls=calls,
        resource_links=links,
    )


class StartDatasetInput(MCPModel):
    brief: str = Field(min_length=1, max_length=8000)
    name: str = Field(default="Untitled dataset", min_length=1, max_length=255)
    intent: Literal["train", "eval", "explore", "pending"] = "pending"
    capability: str | None = Field(default=None, max_length=255)


def _chain(dataset) -> list[Cell]:
    cached = getattr(dataset, "_prefetched_objects_cache", {}).get("cells")
    if cached is not None:
        return sorted(cached, key=lambda cell: cell.position)
    return dataset.chain


def _active_cell(dataset, chain: list[Cell]) -> Cell | None:
    ran = [cell for cell in chain if cell.state == Cell.State.OK and cell.fingerprint]
    if dataset.active_id is not None:
        for cell in ran:
            if cell.id == dataset.active_id:
                return cell
    return ran[-1] if ran else None


def _frozen_before(chain: list[Cell]) -> int:
    used = [cell.position for cell in chain if cell.used_at is not None]
    return max(used, default=-1)


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


def _active_version(dataset, chain: list[Cell], versions: dict) -> ActiveVersion | None:
    cell = _active_cell(dataset, chain)
    if cell is None:
        return None
    return ActiveVersion(
        id=str(cell.id),
        version=versions.get(cell.id, "1.0"),
        title=cell.title.strip() or "Cell",
        rows=int(cell.rows or 0),
        fingerprint=cell.fingerprint or "",
        fits=_fit(cell, public_intent(dataset.intent)),
    )


def _list_fields(dataset, chain: list[Cell]) -> dict[str, Any]:
    versions = dataset.versions(chain=chain)
    return {
        "id": str(dataset.id),
        "name": dataset.name or "",
        "intent": public_intent(dataset.intent),
        "source_kind": dataset.source_kind,
        "state": dataset.state,
        "capability": _capability_ref(dataset),
        "active": _active_version(dataset, chain, versions),
        "resource": dataset_resource_link(dataset),
    }


def serialize_dataset_list_item(dataset) -> DatasetListItem:
    return DatasetListItem.model_validate(_list_fields(dataset, _chain(dataset)))


def _cell_summary(
    dataset, cell: Cell, versions: dict, frozen_before: int, attribution: dict
) -> CellSummary:
    version = versions.get(cell.id, "1.0")
    error = sanitize_error(cell.error) or None
    columns = [
        _jsonable(col) if isinstance(col, dict) else {"name": str(col)}
        for col in (cell.columns or [])[:100]
    ]
    return CellSummary(
        id=str(cell.id),
        position=cell.position,
        version=version,
        title=cell.title.strip() or "Cell",
        script=_clip(cell.script or ""),
        script_truncated=len(cell.script or "") > _SCRIPT_CHARS,
        transformation=attribution,
        note=_clip(cell.note or "", 512),
        note_truncated=len(cell.note or "") > 512,
        state=cell.state,
        error=error,
        frozen=cell.used_at is not None or cell.position <= frozen_before,
        rows=int(cell.rows or 0),
        columns=columns,
        fingerprint=cell.fingerprint or "",
        input_fingerprint=cell.input_fingerprint or "",
        intent_report=_jsonable(cell.intent_report or {}),
        capability_report=_jsonable(cell.capability_report or {}),
        review=_bounded(cell.review),
        quality_report=_jsonable(_quality_summary(cell.quality_report)),
        preparation_plan=_bounded(cell.preparation_plan),
        readiness=review.readiness(dataset, cell),
        fits=_fit(cell, public_intent(dataset.intent)),
        seconds=float(cell.seconds or 0),
        used_at=cell.used_at,
        resource=_cell_link(dataset, cell),
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


def _sample(dataset, cell: Cell | None, versions: dict) -> DatasetSample | None:
    if cell is None or not cell.ran:
        return None
    from overbae.services.datasets import paths, store

    path = paths.cell_path(dataset.id, cell.id)
    if not path.exists():
        return None
    try:
        rows = store.head(path, _SAMPLE_ROWS)
    except Exception:  # noqa: BLE001 — sample is optional; never leak store errors
        return None
    return DatasetSample(
        version=versions.get(cell.id, "1.0"),
        cell_id=str(cell.id),
        rows=[
            {str(k): _sample_cell(v) for k, v in row.items()}
            if isinstance(row, dict)
            else {"value": _sample_cell(row)}
            for row in rows
        ],
    )


def _human_action(dataset, active: Cell | None) -> DatasetHumanAction | None:
    project_id = str(dataset.project_id)
    if active is not None:
        return DatasetHumanAction(
            command="overmind dataset export DATASET --json",
            arguments={"dataset": str(dataset.id), "project_id": project_id},
        )
    if dataset.source_kind in {Dataset.SourceKind.FILE, Dataset.SourceKind.PENDING}:
        return DatasetHumanAction(
            command="overmind dataset upload FILE --project-id PROJECT --dataset DATASET --json",
            arguments={"file": "<path>", "project_id": project_id, "dataset": str(dataset.id)},
        )
    return None


def next_actions(dataset, chain: list[Cell], active: Cell | None) -> list[NextAction]:
    ds_id = str(dataset.id)
    scoped = {"project_id": str(dataset.project_id)}
    if dataset.state == Dataset.State.ERROR and not chain:
        receipt = imports.status(dataset)
        if receipt and receipt["state"] == "blocked":
            return (
                [
                    NextAction(
                        tool="resume_dataset_import",
                        reason="Resume the retained source import.",
                        arguments={**scoped, "dataset": ds_id},
                    )
                ]
                if receipt["can_resume"]
                else []
            )
    if dataset.state in _BUSY:
        pipeline_run = (
            dataset.pipeline_runs.filter(state__in=["queued", "running"])
            .order_by("-created_at")
            .values_list("id", flat=True)
            .first()
        )
        return [
            NextAction(
                tool="get_job",
                reason="Inspect the current dataset operation.",
                arguments={
                    **scoped,
                    "kind": "dataset_pipeline" if pipeline_run else "dataset_run",
                    "id": str(pipeline_run) if pipeline_run else ds_id,
                },
            )
        ]
    if active is None or dataset.intent == Dataset.Intent.PENDING:
        return [
            NextAction(
                tool="inspect_dataset_workbench",
                reason="Attach source data and explicitly set the requested intent with update_dataset.",
                arguments={**scoped, "dataset": ds_id},
            )
        ]
    ok, reason = active.fits(public_intent(dataset.intent))
    if not ok or dataset.intent == Dataset.Intent.EXPLORE:
        return [
            NextAction(
                tool="inspect_dataset_workbench",
                reason=sanitize_error(reason) or "Inspect available transformation operations.",
                arguments={**scoped, "dataset": ds_id},
            )
        ]
    tool = (
        "check_finetune_readiness"
        if dataset.intent == Dataset.Intent.TRAIN
        else "check_evaluation_readiness"
    )
    return [
        NextAction(
            tool=tool,
            reason="Check the selected version for the intended consumer.",
            arguments={**scoped, "dataset": ds_id, "cell": str(active.id)},
        )
    ]


def _detail_summary(dataset, chain: list[Cell], active: Cell | None) -> str:
    if dataset.state == Dataset.State.ERROR:
        return _clip(
            f"Dataset error: {sanitize_error(dataset.error) or 'The dataset failed.'}",
            _SUMMARY_CHARS,
        )
    if dataset.state == Dataset.State.LANDING:
        return "Dataset is landing."
    if dataset.state == Dataset.State.RUNNING:
        return "Dataset run is in progress."
    n = len(chain)
    rows = int(active.rows) if active is not None else 0
    noun = "cell" if n == 1 else "cells"
    return f"{n} {noun}, {dataset.state}, {rows} rows."


def serialize_dataset_detail(
    dataset,
    *,
    cell_offset: int = 0,
    cell_limit: int = 5,
    source_offset: int = 0,
    source_limit: int = 10,
) -> DatasetDetail:
    chain = _chain(dataset)
    versions = dataset.versions(chain=chain)
    frozen_before = _frozen_before(chain)
    active = _active_cell(dataset, chain)
    page_cells = chain[cell_offset : cell_offset + cell_limit]
    attributions = transformation.records(dataset, page_cells)
    cells = [
        _cell_summary(dataset, cell, versions, frozen_before, attributions[cell.pk])
        for cell in page_cells
    ]
    fields = _list_fields(dataset, chain)
    dataset_link = fields["resource"]
    links = [dataset_link]
    if dataset.state in _BUSY:
        links.append(dataset_run_job_link(dataset))
    error = sanitize_error(dataset.error) or None
    sources = dataset.source_spec.get("sources", [])
    result = DatasetDetail.model_validate(
        {
            **fields,
            "operation": _jsonable({**dataset.operation, "source_import": imports.status(dataset)}),
            "landing_progress": dataset.source_spec.get("landing_progress"),
            "brief": _clip(dataset.brief, 8000),
            "sources": [
                _source_summary(item)
                for item in sources[source_offset : source_offset + source_limit]
            ],
            "sources_total": len(sources),
            "source_page": {
                "limit": source_limit,
                "offset": source_offset,
                "total": len(sources),
                "has_more": False,
                "next_cursor": None,
            },
            "preparation_context": _bounded(workshop_context(dataset, measure_missing=False), 6000),
            "preparation_plan": _bounded(dataset.preparation_plan, 3000),
            "capability_rank": _rank(dataset.capability_rank),
            "contamination_report": _bounded(dataset.source_spec.get("contamination_report", {})),
            "cells": cells,
            "cells_truncated": cell_offset > 0 or cell_offset + len(cells) < len(chain),
            "cell_page": {
                "limit": cell_limit,
                "offset": cell_offset,
                "total": len(chain),
                "has_more": cell_offset + len(cells) < len(chain),
                "next_cursor": str(cell_offset + len(cells))
                if cell_offset + len(cells) < len(chain)
                else None,
            },
            "sample": _sample(dataset, active, versions),
            "next_actions": next_actions(dataset, chain, active),
            "resource_links": links,
            "summary": _detail_summary(dataset, chain, active),
            "human_action": _human_action(dataset, active),
            "error": error,
        }
    )

    # Pagination preserves identities when rich summaries exceed the transport budget.
    while (
        len(json.dumps(result.model_dump(mode="json")).encode()) > 31_000 and len(result.cells) > 1
    ):
        result.cells.pop()
    while (
        len(json.dumps(result.model_dump(mode="json")).encode()) > 31_000
        and len(result.sources) > 1
    ):
        result.sources.pop()
    if len(json.dumps(result.model_dump(mode="json")).encode()) > 31_000:
        result.truncated_fields.extend(["brief", "sample", "capability_rank"])
        result.brief = _bounded(result.brief, 1024)
        result.sample = None
        result.capability_rank = result.capability_rank[:3]
        for name in (
            "operation",
            "preparation_context",
            "preparation_plan",
            "contamination_report",
        ):
            setattr(result, name, _bounded(getattr(result, name), 1000))
            result.truncated_fields.append(name)
        for cell in result.cells:
            cell.script = _bounded(cell.script, 1024)
            cell.script_truncated = True
            cell.note = _bounded(cell.note, 512)
            cell.note_truncated = True
            cell.columns = [_bounded(column, 200) for column in cell.columns[:10]]
            result.truncated_fields.extend(
                [f"cells.{cell.id}.script", f"cells.{cell.id}.note", f"cells.{cell.id}.columns"]
            )
            for name in (
                "review",
                "quality_report",
                "preparation_plan",
                "readiness",
                "intent_report",
                "capability_report",
            ):
                setattr(
                    cell,
                    name,
                    _bounded(getattr(cell, name), 6000 if name == "quality_report" else 500),
                )
                result.truncated_fields.append(f"cells.{cell.id}.{name}")
    result.cell_page.has_more = cell_offset + len(result.cells) < len(chain)
    result.cell_page.next_cursor = (
        str(cell_offset + len(result.cells)) if result.cell_page.has_more else None
    )
    result.cells_truncated = cell_offset > 0 or result.cell_page.has_more
    result.source_page.has_more = source_offset + len(result.sources) < len(sources)
    if result.source_page.has_more:
        result.source_page.next_cursor = str(source_offset + len(result.sources))
        result.next_actions.append(
            NextAction(
                tool="inspect_dataset",
                reason="Read the next source metadata page.",
                arguments={
                    "dataset": str(dataset.pk),
                    "source_offset": source_offset + len(result.sources),
                    "source_limit": source_limit,
                    "project_id": str(dataset.project_id),
                },
            )
        )
    return result
