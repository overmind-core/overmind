from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel


class InspectActivityInput(MCPModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=25, ge=1, le=100)
    path: Literal["agent", "data"] = "agent"


class ActivityAsset(MCPModel):
    kind: Literal["dataset", "capability", "evaluation", "training_job"]
    id: UUID
    label: str


class ActivityContract(MCPModel):
    id: UUID
    project: UUID | None
    kind: str
    actor_kind: str
    actor_name: str
    action: str
    status: str
    assets: list[ActivityAsset]
    task_kind: str
    task_id: str
    sequence_key: str
    seen: bool
    created_at: datetime
    updated_at: datetime


class MilestoneContract(MCPModel):
    actor_kind: str | None
    actor_name: str | None
    kind: str
    label: str
    completed_at: datetime | None
    activity_id: UUID | None
    assets: list[ActivityAsset]


class InspectActivityOutput(MCPModel):
    results: list[ActivityContract] = Field(max_length=100)
    count: int
    unseen_count: int
    observed_at: datetime
    next_offset: int | None
    milestones: list[MilestoneContract] = Field(max_length=10)


class ReportOnboardingInput(MCPModel):
    run_id: UUID
    completed_steps: int = Field(ge=0, le=10)


class ReportOnboardingOutput(MCPModel):
    milestones: list[MilestoneContract] = Field(max_length=10)
