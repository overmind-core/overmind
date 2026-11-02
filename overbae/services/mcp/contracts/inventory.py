from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from overbae.services.mcp.contracts.common import MCPModel, ResourceLinkContract

ObjectKind = Literal[
    "dataset_transfer",
    "inference_request",
    "training_preparation",
    "dataset_pipeline",
    "model_activation",
    "connector_sync",
    "decision_job",
    "checkpoint",
    "trained_model",
    "finetune",
    "deployment",
    "capability",
    "dataset",
    "eval_set",
    "eval_row",
    "eval_run",
    "optimizer",
    "data_exploration",
    "data_partition",
    "native_evaluation",
    "training_experiment",
    "training_plan",
    "decision_performance",
]


class ListObjectsInput(MCPModel):
    kinds: list[ObjectKind] = Field(default_factory=list, max_length=24)
    search: str = Field(default="", max_length=255)
    status: str | None = Field(default=None, max_length=40)
    capability: UUID | None = None
    request_key: str | None = Field(default=None, max_length=128)
    since: AwareDatetime | None = None
    before: AwareDatetime | None = None
    ordering: Literal["newest", "oldest", "name"] = "newest"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class ObjectSummary(MCPModel):
    kind: ObjectKind
    id: str
    project_id: str
    name: str
    status: str | None
    created_at: datetime
    updated_at: datetime | None = None
    capability_id: str | None = None
    base_model: str | None = None
    training_job_id: str | None = None
    deployment_id: str | None = None
    serving_model_id: str | None = None
    resource: ResourceLinkContract
    console_url: str


class ListObjectsOutput(MCPModel):
    items: list[ObjectSummary] = Field(max_length=100)
    total: int
    offset: int
    next_offset: int | None
    observed_at: datetime


class OverviewInput(MCPModel):
    pass


class InventoryCount(MCPModel):
    kind: ObjectKind
    status: str | None
    count: int


class OverviewOutput(MCPModel):
    project_id: str
    observed_at: datetime
    counts: list[InventoryCount]
    recent: ListObjectsOutput
    discovery_tool: Literal["list_objects"] = "list_objects"


class LineageInput(MCPModel):
    training_job: UUID
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class LineageCell(MCPModel):
    role: str
    id: str
    dataset_id: str
    fingerprint: str
    rows: int | None


class LineageOutput(MCPModel):
    training_job_id: str
    project_id: str
    observed_at: datetime
    base_model: str
    cells: list[LineageCell]
    preparation_id: str | None
    deployment: ObjectSummary | None
    evaluations: list[dict]
    evaluation_total: int
    next_offset: int | None
    active_capabilities: list[str]
    active_capabilities_total: int
    limitations: list[str]
    resource: ResourceLinkContract


class DependenciesInput(MCPModel):
    kind: Literal["dataset", "deployment", "eval_set", "eval_row"]
    id: UUID
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class Dependency(MCPModel):
    kind: str
    id: str
    relationship: str
    resource: ResourceLinkContract


class DependenciesOutput(MCPModel):
    project_id: str
    observed_at: datetime
    items: list[Dependency] = Field(max_length=100)
    total: int
    next_offset: int | None
    limitations: list[str]


class SearchObjectsInput(ListObjectsInput):
    project_ids: list[UUID] = Field(min_length=1, max_length=8)


class SearchObjectsOutput(ListObjectsOutput):
    inaccessible_project_ids: list[str] = Field(max_length=8)
    scope_complete: bool
