"""Strict contracts for the issue and training-signal MCP tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel, PageContract

Range = Literal["24h", "7d", "30d", "90d", "180d"]
Cause = Literal["model", "system", "data"]
Severity = Literal["critical", "high", "medium", "low"]
State = Literal["new", "regressed", "escalating", "ongoing", "resolved", "muted"]


class IssueContract(MCPModel):
    id: str
    title: str
    cause: Cause
    severity: Severity
    state: State
    capability: str | None = None
    capability_name: str = ""
    traces: int = Field(ge=0)
    rate: float | None = None
    prior_rate: float | None = None
    first_seen: datetime
    last_seen: datetime
    remedy: str = ""
    trend: list[int] = Field(default_factory=list, max_length=200)


class NextActionContract(MCPModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    why: str


class TrainingSignalContract(MCPModel):
    capability: str
    capability_name: str
    model: str = ""
    issues: list[IssueContract] = Field(max_length=10)
    more_issues: int = Field(ge=0)
    failing_traces: int = Field(ge=0)
    passing_traces: int = Field(ge=0)
    next_actions: list[NextActionContract] = Field(max_length=5)


class ListTrainingSignalsInput(MCPModel):
    range: Range = "7d"
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class ListTrainingSignalsOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    traces_checked: int = Field(ge=0)
    signals: list[TrainingSignalContract] = Field(max_length=50)
    page: PageContract


class ListIssuesInput(MCPModel):
    range: Range = "7d"
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    cause: Cause | None = None
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ListIssuesOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    issues: list[IssueContract] = Field(max_length=100)
    page: PageContract


class InspectIssueInput(MCPModel):
    issue: str = Field(min_length=1, max_length=64, description="Issue id.")
    range: Range = "7d"


class IssueExampleContract(MCPModel):
    trace_id: str
    occurred_at: datetime
    reason: str = ""
    input: str = ""
    output: str = ""


class IssueCauseContract(MCPModel):
    label: str
    count: int = Field(ge=0)


class RelatedIssueContract(MCPModel):
    id: str
    capability_name: str = ""
    traces: int = Field(ge=0)
    rate: float | None = None


class InspectIssueOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=400)
    issue: IssueContract
    sessions: int = Field(ge=0)
    causes: list[IssueCauseContract] = Field(max_length=10)
    examples: list[IssueExampleContract] = Field(max_length=10)
    related: list[RelatedIssueContract] = Field(max_length=10)
    next_actions: list[NextActionContract] = Field(max_length=5)


class UpdateIssueInput(MCPModel):
    issue: str = Field(min_length=1, max_length=64, description="Issue id.")
    state: Literal["resolve", "mute", "reopen"] | None = None
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
        description="A clearer name for the failure, once the traces explain it.",
    )


class UpdateIssueOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=400)
    issue: IssueContract
