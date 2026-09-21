"""Bounded MCP contracts for Dataset + Cell. No ingestion, workshop, ft, or surface."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import AliasChoices, Field, field_validator, model_validator

from overbae.models import Cell, Dataset
from overbae.services.datasets import review
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
_CHAT_DEFAULT = 10
_CHAT_MAX = 30
_SCRIPT_CHARS = 8_000
_RANK_CAP = 20
_SUMMARY_CHARS = 240
_BUSY = (Dataset.State.LANDING, Dataset.State.DIAGNOSING, Dataset.State.RUNNING)
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
    intent: Literal["train", "eval", "pending"]
    source_kind: Literal["file", "traces"]
    state: Literal["landing", "diagnosing", "idle", "running", "error"]
    capability: CapabilityRef | None = None
    active: ActiveVersion | None = None
    resource: ResourceLinkContract


class CellSummary(MCPModel):
    id: str
    position: int = Field(ge=0)
    version: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=255)
    script: str = Field(default="", max_length=_SCRIPT_CHARS)
    script_truncated: bool = False
    note: str = Field(default="", max_length=512)
    state: Literal["proposed", "queued", "running", "ok", "failed"]
    error: str | None = None
    frozen: bool
    rows: int = Field(ge=0)
    columns: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    fingerprint: str = Field(default="", max_length=64)
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


class TouchedCell(MCPModel):
    id: str
    action: str = Field(default="", max_length=40)
    text_offset: int | None = Field(default=None, ge=0)


class AgentProgress(MCPModel):
    stage: str = Field(max_length=40)
    label: str = Field(max_length=255)
    detail: str = Field(max_length=4000)
    started_at: str | None = None
    updated_at: str | None = None
    rows_before: int | None = Field(default=None, ge=0)
    target_rows: int | None = Field(default=None, ge=0)
    generated_rows: int | None = Field(default=None, ge=0)
    cell_id: str | None = None
    proposal_id: str | None = None


class ChatTurn(MCPModel):
    role: Literal["user", "agent"]
    text: str = Field(default="", max_length=_SCRIPT_CHARS)
    error: str | None = None
    cells: list[TouchedCell] = Field(default_factory=list, max_length=20)
    at: str | None = Field(default=None, max_length=80)
    ms: int | None = Field(default=None, ge=0)
    status: Literal["running", "awaiting_approval", "resolved", "complete", "error"] | None = None
    progress: AgentProgress | None = None


class NextAction(MCPModel):
    tool: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)
    arguments: dict[str, Any] = Field(default_factory=dict)


class DatasetHumanAction(MCPModel):
    command: str = Field(min_length=1, max_length=240)
    arguments: dict[str, str] = Field(default_factory=dict, max_length=20)


class DatasetDetail(DatasetListItem):
    preparation_context: dict[str, Any] = Field(default_factory=dict)
    contamination_report: dict[str, Any] = Field(default_factory=dict)
    capability_rank: list[CapabilityRankItem] = Field(default_factory=list, max_length=_RANK_CAP)
    cells: list[CellSummary] = Field(default_factory=list, max_length=_CELL_CAP)
    cells_truncated: bool = False
    sample: DatasetSample | None = None
    recent_chat: list[ChatTurn] = Field(default_factory=list, max_length=_CHAT_MAX)
    next_actions: list[NextAction] = Field(default_factory=list, max_length=8)
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=8)
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    human_action: DatasetHumanAction | None = None
    error: str | None = None


class DatasetMutationRef(MCPModel):
    id: str
    name: str = Field(default="", max_length=255)
    state: Literal["landing", "diagnosing", "idle", "running", "error"]
    resource: ResourceLinkContract


class DatasetJobReceipt(JobReceipt):
    kind: Literal["dataset_run"] = "dataset_run"


class DatasetMutationOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=_SUMMARY_CHARS)
    dataset: DatasetMutationRef
    job: DatasetJobReceipt
    eval_dataset: DatasetMutationRef | None = None
    # Set by create_dataset_from_traces: the traces the selection resolved to.
    traces: int | None = Field(default=None, ge=0)
    resource_links: list[ResourceLinkContract] = Field(max_length=4)


class ListDatasetsInput(MCPModel):
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    intent: Literal["train", "eval", "pending"] | None = None
    state: Literal["landing", "diagnosing", "idle", "running", "error"] | None = None
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
    chat_limit: int = Field(default=_CHAT_DEFAULT, ge=1, le=_CHAT_MAX)


class QueryDatasetInput(MCPModel):
    dataset: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("dataset", "dataset_id"),
    )
    sql: str = Field(
        min_length=1,
        max_length=8_000,
        description="One SELECT over the table `t`, the chosen cell. DuckDB dialect. "
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


class MessageDatasetAgentInput(MCPModel):
    dataset: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("dataset", "dataset_id"),
    )
    message: str = Field(min_length=1, max_length=_SCRIPT_CHARS)


class RunDatasetInput(MCPModel):
    dataset: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("dataset", "dataset_id"),
    )
    proposal_cell: str | None = Field(default=None, min_length=1, max_length=80)


def _mutation_ref(dataset) -> DatasetMutationRef:
    return DatasetMutationRef(
        id=str(dataset.id),
        name=dataset.name or "",
        state=dataset.state,
        resource=dataset_resource_link(dataset),
    )


def mutation_output(
    dataset, *, summary: str, traces: int | None = None, eval_dataset=None
) -> DatasetMutationOutput:
    links = [dataset_resource_link(dataset), dataset_run_job_link(dataset)]
    if eval_dataset is not None:
        links += [dataset_resource_link(eval_dataset), dataset_run_job_link(eval_dataset)]
    return DatasetMutationOutput(
        summary=_clip(summary, _SUMMARY_CHARS),
        dataset=_mutation_ref(dataset),
        job=DatasetJobReceipt(
            kind="dataset_run",
            id=str(dataset.id),
            status=dataset.state,
            resource=links[1],
        ),
        eval_dataset=_mutation_ref(eval_dataset) if eval_dataset is not None else None,
        traces=traces,
        resource_links=links,
    )


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


def _cell_summary(dataset, cell: Cell, versions: dict, frozen_before: int) -> CellSummary:
    version = "proposed" if cell.state == Cell.State.PROPOSED else versions.get(cell.id, "1.0")
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
        note=cell.note or "",
        state=cell.state,
        error=error,
        frozen=cell.used_at is not None or cell.position <= frozen_before,
        rows=int(cell.rows or 0),
        columns=columns,
        fingerprint=cell.fingerprint or "",
        intent_report=_jsonable(cell.intent_report or {}),
        capability_report=_jsonable(cell.capability_report or {}),
        review=_jsonable(cell.review),
        quality_report=_jsonable(cell.quality_report),
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


def _touched(raw) -> list[TouchedCell]:
    out: list[TouchedCell] = []
    for item in raw or []:
        if isinstance(item, dict) and item.get("id"):
            out.append(TouchedCell(id=str(item["id"]), action=str(item.get("action") or "")[:40]))
        elif item:
            out.append(TouchedCell(id=str(item)))
        if len(out) >= 20:
            break
    return out


def _chat(raw, limit: int) -> list[ChatTurn]:
    turns = [item for item in (raw or []) if isinstance(item, dict)]
    window = turns[-max(1, min(limit, _CHAT_MAX)) :]
    out: list[ChatTurn] = []
    for item in window:
        role = item.get("role")
        if role not in ("user", "agent"):
            continue
        error = sanitize_error(str(item.get("error") or "")) or None
        ms = item.get("ms")
        out.append(
            ChatTurn(
                role=role,
                text=_clip(str(item.get("text") or "")),
                error=error,
                cells=_touched(item.get("cells")),
                at=str(item.get("at") or "")[:80] or None,
                ms=int(ms) if isinstance(ms, (int, float)) and ms >= 0 else None,
                status=item.get("status"),
                progress=item.get("progress"),
            )
        )
    return out


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


def next_actions(dataset, chain: list[Cell], active: Cell | None) -> list[NextAction]:
    """The one answer to "what now" for a dataset. Every suggestion satisfies
    the named tool's schema as given."""
    ds_id = str(dataset.id)
    if dataset.state in _BUSY:
        return [
            NextAction(
                tool="get_job",
                reason=f"Dataset is {dataset.state}.",
                arguments={"kind": "dataset_run", "id": ds_id},
            )
        ]
    if dataset.state == Dataset.State.ERROR:
        reason = sanitize_error(dataset.error) or "The dataset is in error."
        return [
            NextAction(
                tool="message_dataset_agent",
                reason=reason,
                arguments={"dataset": ds_id},
            )
        ]
    proposed = [cell for cell in chain if cell.state == Cell.State.PROPOSED]
    if proposed:
        return [
            NextAction(
                tool="run_dataset",
                reason="User must approve this proposed cell.",
                arguments={"dataset": ds_id, "proposal_cell": str(cell.id)},
            )
            for cell in proposed[:5]
        ]
    if active is None:
        return [
            NextAction(
                tool="message_dataset_agent",
                reason="No version has run.",
                arguments={"dataset": ds_id},
            )
        ]
    intent = public_intent(dataset.intent)
    ok, reason = active.fits(intent)
    if not ok:
        return [
            NextAction(
                tool="message_dataset_agent",
                reason=sanitize_error(reason) or "The active version does not fit.",
                arguments={"dataset": ds_id},
            )
        ]
    actions: list[NextAction] = []
    if findings := review.warnings(dataset, active):
        actions.append(
            NextAction(
                tool="message_dataset_agent",
                reason="Review recommended: " + "; ".join(findings),
                arguments={"dataset": ds_id},
            )
        )
    args = {"dataset": ds_id, "cell": str(active.id)}
    if intent == Dataset.Intent.TRAIN:
        return actions + [
            NextAction(
                tool="check_finetune_readiness", reason="Active version fits train.", arguments=args
            )
        ]
    actions += [
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


def _detail_summary(dataset, chain: list[Cell], active: Cell | None) -> str:
    if dataset.state == Dataset.State.ERROR:
        return _clip(
            f"Dataset error: {sanitize_error(dataset.error) or 'The dataset failed.'}",
            _SUMMARY_CHARS,
        )
    if dataset.state == Dataset.State.LANDING:
        return "Dataset is landing."
    if dataset.state == Dataset.State.DIAGNOSING:
        return "Dataset agent is diagnosing."
    if dataset.state == Dataset.State.RUNNING:
        return "Dataset run is in progress."
    n = len(chain)
    rows = int(active.rows) if active is not None else 0
    noun = "cell" if n == 1 else "cells"
    return f"{n} {noun}, {dataset.state}, {rows} rows."


def serialize_dataset_detail(dataset, *, chat_limit: int = _CHAT_DEFAULT) -> DatasetDetail:
    chain = _chain(dataset)
    versions = dataset.versions(chain=chain)
    frozen_before = _frozen_before(chain)
    active = _active_cell(dataset, chain)
    cells = [_cell_summary(dataset, cell, versions, frozen_before) for cell in chain[:_CELL_CAP]]
    fields = _list_fields(dataset, chain)
    dataset_link = fields["resource"]
    links = [dataset_link]
    if dataset.state in _BUSY:
        links.append(dataset_run_job_link(dataset))
    error = sanitize_error(dataset.error) or None
    return DatasetDetail.model_validate(
        {
            **fields,
            "preparation_context": _jsonable(workshop_context(dataset)),
            "capability_rank": _rank(dataset.capability_rank),
            "contamination_report": _jsonable(dataset.source_spec.get("contamination_report", {})),
            "cells": cells,
            "cells_truncated": len(chain) > _CELL_CAP,
            "sample": _sample(dataset, active, versions),
            "recent_chat": _chat(dataset.chat, chat_limit),
            "next_actions": next_actions(dataset, chain, active),
            "resource_links": links,
            "summary": _detail_summary(dataset, chain, active),
            "human_action": _human_action(dataset, active),
            "error": error,
        }
    )
