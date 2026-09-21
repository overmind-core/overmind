"""Typed contracts for the MCP observability and status tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel, PageContract, ResourceLinkContract

MAX_FAILURES = 50
MAX_ROWS = 100


class InspectCapabilityHealthInput(MCPModel):
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    days: int = Field(default=30, ge=1, le=365)


class HealthEvaluator(MCPModel):
    evaluator: str
    n: int = Field(ge=0)
    avg_value: float | None = None
    pass_rate: float | None = None
    n_failed: int = Field(ge=0)
    pass_rate_delta: float | None = None
    avg_value_delta: float | None = None


class HealthScores(MCPModel):
    total: int = Field(ge=0)
    graded: int = Field(ge=0)
    overall_pass_rate: float | None = None
    outcomes: dict[str, int] = Field(default_factory=dict)
    evaluators: list[HealthEvaluator] = Field(max_length=50)


class LiveTraceHealth(MCPModel):
    traces_in_window: int = Field(ge=0)
    traces_scored: int = Field(ge=0)
    coverage: float | None = None
    overall_pass_rate: float | None = None
    evaluators: list[HealthEvaluator] = Field(max_length=50)


class TraceHealth(MCPModel):
    n: int = Field(ge=0)
    errors: int = Field(ge=0)
    error_rate: float | None = None
    avg_duration_ms: float | None = None
    p95_duration_ms: float | None = None


class InspectCapabilityHealthOutput(MCPModel):
    days: int = Field(ge=1, le=365)
    capability: str | None = None
    scores: HealthScores
    live_trace_scores: LiveTraceHealth
    traces: TraceHealth
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=1)
    summary: str = Field(min_length=1, max_length=240)


class QueryFailuresInput(MCPModel):
    capability: str = Field(min_length=1, max_length=255)
    since_days: int = Field(default=7, ge=1, le=90)
    limit: int = Field(default=20, ge=1, le=MAX_FAILURES)


class FailureScore(MCPModel):
    evaluator: str = ""
    reasoning: str = ""


class FailureRow(MCPModel):
    trace_ref: str
    trace_id: str
    summary: str = ""
    failed_scores: list[FailureScore] = Field(max_length=50)
    tools: list[str] = Field(max_length=100)
    resource: ResourceLinkContract | None = None


class QueryFailuresOutput(MCPModel):
    capability: str
    since_days: int = Field(ge=1, le=90)
    failures: list[FailureRow] = Field(max_length=MAX_FAILURES)
    n: int = Field(ge=0, le=MAX_FAILURES)
    resource_links: list[ResourceLinkContract] = Field(max_length=MAX_FAILURES + 1)
    summary: str = Field(min_length=1, max_length=240)


TraceOrdering = Literal[
    "start_time_ns",
    "-start_time_ns",
    "duration_ns",
    "-duration_ns",
    "received_at",
    "-received_at",
    "name",
    "-name",
    "status_code",
    "-status_code",
]


class QueryTracesInput(MCPModel):
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    session: str | None = Field(default=None, min_length=1, max_length=512)
    search: str | None = Field(default=None, min_length=1, max_length=255)
    trace_id: str | None = Field(default=None, min_length=1, max_length=64)
    span_id: str | None = Field(default=None, min_length=1, max_length=16)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    operation: str | None = Field(default=None, min_length=1, max_length=512)
    service_name: str | None = Field(default=None, min_length=1, max_length=255)
    span_type: str | None = Field(default=None, min_length=1, max_length=40)
    status: Literal["ok", "error", "1", "2"] | None = None
    status_code: int | None = Field(default=None, ge=0, le=2)
    has_error: bool | None = None
    model: str | None = Field(default=None, min_length=1, max_length=255)
    has_model: bool | None = None
    start_time_ns__gte: int | None = Field(default=None, ge=0)
    start_time_ns__lte: int | None = Field(default=None, ge=0)
    min_duration_ms: float | None = Field(default=None, ge=0)
    max_duration_ms: float | None = Field(default=None, ge=0)
    start_after: datetime | None = None
    start_before: datetime | None = None
    total_tokens_gte: float | None = Field(default=None, ge=0)
    total_tokens_lte: float | None = Field(default=None, ge=0)
    total_cost_gte: float | None = Field(default=None, ge=0)
    total_cost_lte: float | None = Field(default=None, ge=0)
    all_spans: bool = False
    unbound: bool | None = None
    ordering: TraceOrdering = "-start_time_ns"
    limit: int = Field(default=20, ge=1, le=MAX_ROWS)
    offset: int = Field(default=0, ge=0)
    cursor: str | None = Field(default=None, min_length=1, max_length=128)


class TraceRow(MCPModel):
    trace_id: str
    span_id: str
    name: str = ""
    capability: str | None = None
    span_type: str
    operation: str = ""
    service_name: str = ""
    status_code: int
    duration_ms: float
    start_time_ns: int
    received_at: datetime
    total_tokens: int | None = None
    total_cost: float | None = None
    model: str | None = None
    trace_status: str
    resource: ResourceLinkContract


class QueryTracesOutput(MCPModel):
    traces: list[TraceRow] = Field(max_length=MAX_ROWS)
    n: int = Field(ge=0, le=MAX_ROWS)
    page: PageContract
    all_spans: bool
    resource_links: list[ResourceLinkContract] = Field(max_length=MAX_ROWS)
    summary: str = Field(min_length=1, max_length=240)


class QueryTaskExecutionsInput(MCPModel):
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    behaviour: str | None = Field(default=None, min_length=1, max_length=255)
    binding_source: str | None = Field(default=None, min_length=1, max_length=20)
    trace_id: str | None = Field(default=None, min_length=1, max_length=32)
    status: str | None = Field(default=None, min_length=1, max_length=20)
    started_after: datetime | None = None
    started_before: datetime | None = None
    min_duration_ms: int | None = Field(default=None, ge=0)
    max_duration_ms: int | None = Field(default=None, ge=0)
    has_error: bool | None = None
    service_name: str | None = Field(default=None, min_length=1, max_length=255)
    operation: str | None = Field(default=None, min_length=1, max_length=512)
    span_type: str | None = Field(default=None, min_length=1, max_length=40)
    status_code: int | None = Field(default=None, ge=0, le=2)
    model: str | None = Field(default=None, min_length=1, max_length=255)
    has_model: bool | None = None
    total_tokens_gte: float | None = Field(default=None, ge=0)
    total_tokens_lte: float | None = Field(default=None, ge=0)
    total_cost_gte: float | None = Field(default=None, ge=0)
    total_cost_lte: float | None = Field(default=None, ge=0)
    limit: int = Field(default=20, ge=1, le=MAX_ROWS)
    offset: int = Field(default=0, ge=0)


class TaskExecutionRow(MCPModel):
    id: str
    capability: str | None = None
    behaviour: str | None = None
    behaviour_key: str = ""
    trace_id: str
    unit_span_id: str
    conversation_id: str = ""
    binding_source: str
    success_score: float | None = None
    session_score: float | None = None
    session_rationale: str = ""
    route_flags: list[Any] = Field(default_factory=list)
    terminal_kind: str = ""
    status: str
    started_at: datetime | None = None
    duration_ms: int | None = None
    total_tokens: int | None = None
    total_cost: float | None = None
    model: str | None = None
    scoring_pending: bool = False
    resource: ResourceLinkContract


class QueryTaskExecutionsOutput(MCPModel):
    task_executions: list[TaskExecutionRow] = Field(max_length=MAX_ROWS)
    n: int = Field(ge=0, le=MAX_ROWS)
    page: PageContract
    resource_links: list[ResourceLinkContract] = Field(max_length=MAX_ROWS)
    summary: str = Field(min_length=1, max_length=240)


JobKind = Literal[
    "training_preparation",
    "dataset_run",
    "connector_sync",
    "eval_run",
    "finetune",
    "finetune_job",
    "optimizer",
    "optimizer_run",
    "optimizer_experiment",
    "deployment",
]


class GetJobInput(MCPModel):
    kind: JobKind
    id: str = Field(min_length=1, max_length=255)


class GetJobOutput(MCPModel):
    kind: str
    id: str
    status: str
    label: str
    job_error: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    progress: dict[str, Any] | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    resource: ResourceLinkContract
    resource_links: list[ResourceLinkContract] = Field(max_length=3)
    summary: str = Field(min_length=1, max_length=240)
