from datetime import datetime
from typing import Literal

from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel
from overbae.services.mcp.contracts.observability import GetJobInput, GetJobOutput
from overbae.services.mcp.errors import MCPErrorData


class InspectJobsInput(MCPModel):
    view: Literal["summary", "full"] = "summary"
    jobs: list[GetJobInput] = Field(min_length=1, max_length=8)
    after: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    timeout_seconds: int = Field(default=0, ge=0, le=30)


class JobObservation(MCPModel):
    kind: str
    id: str
    job: GetJobOutput | None = None
    error: MCPErrorData | None = None


class InspectJobsOutput(MCPModel):
    jobs: list[JobObservation] = Field(max_length=8)
    cursor: str
    changed: bool
    observed_at: datetime
    poll_after_seconds: int = 5


class InspectMutationInput(MCPModel):
    request_key: str = Field(min_length=1, max_length=128)


class InspectMutationOutput(MCPModel):
    id: str
    tool: str
    state: str
    created_at: datetime
    updated_at: datetime
    result: dict
    guidance: str
