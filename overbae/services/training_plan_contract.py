from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(min_length=1, max_length=4000)]
Name = Annotated[str, Field(min_length=1, max_length=200)]


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrainingTask(PlanModel):
    mode: Literal["generative", "decision"]
    objective: Text
    prediction_unit: Text
    target_meaning: Text
    allowed_inputs: list[Name] = Field(min_length=1, max_length=100)
    availability_evidence: Text
    group_by: list[Name] = Field(max_length=20)
    slices: list[Name] = Field(max_length=20)
    primary_metric: str = Field(
        min_length=2,
        max_length=200,
        pattern=r"^/",
        description="JSON Pointer into retained development-check metrics, e.g. /hard_label_accuracy or /generation/accuracy. Loss alone is not task success unless explicitly the task metric.",
    )
    success_criterion: Text


class TrainingEvidence(PlanModel):
    kind: Literal["audit", "baseline", "sanity_check"]
    summary: Text
    method: Text
    cells: list[UUID] = Field(min_length=1, max_length=8)
    limitations: list[Text] = Field(max_length=10)
    metrics: dict[Name, Annotated[float, Field(allow_inf_nan=False)]] = Field(
        default_factory=dict, max_length=20
    )


class PilotSelection(PlanModel):
    check: UUID
    rationale: Text
    budget_fields: list[Literal["max_steps", "n_epochs", "runtime_limit_seconds"]] = Field(
        max_length=3
    )


class TrainingPlanRequest(PlanModel):
    name: str = Field(min_length=1, max_length=255)
    request_key: str = Field(min_length=1, max_length=128)
    source_cell: UUID
    task: TrainingTask
    supersedes: UUID | None = None
    prepared_cell: UUID | None = None
    partition: UUID | None = None
    evidence: list[TrainingEvidence] = Field(default_factory=list, max_length=12)
    selection: PilotSelection | None = Field(
        default=None,
        description="Optional measured pilot selection; full training does not require a pilot.",
    )
    unresolved: list[Text] = Field(default_factory=list, max_length=20)
