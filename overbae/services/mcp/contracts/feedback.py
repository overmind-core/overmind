from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel

FrictionKind = Literal[
    "failure", "mismatch", "confusing", "missing", "stuck", "dead_end", "idea", "praise", "question"
]
Area = Literal[
    "setup",
    "observability",
    "datasets",
    "evaluation",
    "optimizer",
    "training",
    "serving",
    "billing",
    "other",
]
# A catalog tool, an overmind:// resource, or an `overmind` CLI command without arguments.
ACTION = r"^([a-z][a-z0-9_]{0,63}|overmind://[A-Za-z0-9_\-./{}]{1,120}|overmind( [a-z][a-z-]{0,30}){1,4})$"


class TrailStep(MCPModel):
    action: str = Field(pattern=ACTION, max_length=140)
    result: Literal["ok", "error", "unexpected", "no_progress"]
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    attempts: int = Field(default=1, ge=1, le=50)
    note: str | None = Field(default=None, max_length=160)


class AgentEnvironment(MCPModel):
    harness: str | None = Field(default=None, max_length=60)
    harness_version: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=80)
    os: Literal["linux", "macos", "windows", "wsl", "other"] | None = None
    os_version: str | None = Field(default=None, max_length=40)
    shell: str | None = Field(default=None, max_length=20)
    python: str | None = Field(default=None, max_length=20)
    overmind_cli: str | None = Field(default=None, max_length=20)
    skills_version: str | None = Field(default=None, max_length=20)


class SubmitFeedbackInput(MCPModel):
    report_id: UUID
    project_id: UUID | None = None
    kind: FrictionKind
    area: Area
    severity: Literal["blocked", "degraded", "minor"]
    outcome: Literal["abandoned", "worked_around", "resolved", "handed_to_user"]
    headline: str = Field(min_length=3, max_length=120)
    summary: str = Field(min_length=3, max_length=1500)
    intent: str | None = Field(default=None, max_length=200)
    expected: str | None = Field(default=None, max_length=500)
    observed: str | None = Field(default=None, max_length=500)
    suggestion: str | None = Field(default=None, max_length=500)
    trail: list[TrailStep] = Field(default_factory=list, max_length=30)
    resources: list[Annotated[str, Field(pattern=r"^overmind://\S{1,500}$")]] = Field(
        default_factory=list, max_length=10
    )
    tool_calls: int | None = Field(default=None, ge=0, le=10_000)
    environment: AgentEnvironment = Field(default_factory=AgentEnvironment)


class SubmitFeedbackOutput(MCPModel):
    report_id: UUID
    created: bool
    stored: dict[str, Any]
    tell_user: str
