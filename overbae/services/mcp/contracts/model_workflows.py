from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel, ResourceLinkContract
from overbae.services.training_policies import TrainingConstraints


class HoldoutRule(MCPModel):
    field: str = Field(min_length=1, max_length=200)
    values: list[str | int | float | bool | None] = Field(min_length=1)
    role: Literal["train", "development", "calibration", "final"]


class PartitionRecipe(MCPModel):
    seed: int = Field(ge=0, le=4294967295)
    fractions: dict[Literal["train", "development", "calibration", "final"], float]
    group_by: list[str] = Field(default_factory=list, max_length=20)
    stratify_by: str | None = None
    holdouts: list[HoldoutRule] = Field(default_factory=list, max_length=20)


class InferenceSettings(MCPModel):
    context_length: int = Field(default=8192, ge=128, le=131072)
    batch_size: int = Field(default=64, ge=1, le=256)
    padded_tokens: int = Field(default=32768, ge=128, le=262144)
    concurrency: int = Field(default=8, ge=1, le=16)


class PerformanceWorkload(MCPModel):
    sample_size: int = Field(ge=1, le=64)
    repetitions: int = Field(ge=1, le=10)
    concurrency: int = Field(ge=1, le=16)
    seed: int = Field(ge=0, le=4294967295)
    questions_per_request: int = Field(ge=1, le=16)
    amortization_decisions: int = Field(default=1000000, ge=1, le=1000000000000)


class WorkflowReceipt(MCPModel):
    id: UUID
    name: str
    state: str
    kind: str
    next_actions: list[str]
    action_requirements: dict[str, Any]
    input_required: bool
    poll_after_seconds: int | None


class CreatePartitionInput(MCPModel):
    name: str = Field(min_length=1, max_length=255)
    request_key: str = Field(min_length=1, max_length=128)
    source_cell: UUID
    recipe: PartitionRecipe


class ParticipantInput(MCPModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["foundation", "trained", "external"]
    model: str | None = None
    job: UUID | None = None
    served_model: str | None = None


class CreateComparisonInput(MCPModel):
    name: str = Field(min_length=1, max_length=255)
    request_key: str = Field(min_length=1, max_length=128)
    final_cell: UUID
    calibration_cell: UUID | None = None
    participants: list[ParticipantInput] = Field(min_length=1, max_length=8)
    baseline: str
    bootstrap_samples: int = Field(default=1000, ge=2, le=10000)
    seed: int = Field(default=73491, ge=0, le=4294967295)
    inference: InferenceSettings | None = None


class ResumeComparisonInput(MCPModel):
    evaluation: UUID
    stage: str | None = None
    call_id: str | None = None


class CandidateInput(MCPModel):
    name: str = Field(min_length=1, max_length=255)
    base_model: str
    cell: UUID
    development_cell: UUID | None = None
    hyperparameters: dict[str, Any] = Field(
        description='Model recipe; for LoRA use training_type={"type":"Lora"}. Common fields include n_epochs, learning_rate, lora_r and seed.'
    )


class CreateExperimentInput(MCPModel):
    reuse_existing_predictions: bool = False
    constraints: TrainingConstraints | None = None
    name: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=20000)
    request_key: str = Field(min_length=1, max_length=128)
    variants: list[CandidateInput] = Field(min_length=1, max_length=6)
    evaluation: UUID | None = None


class LaunchExperimentInput(MCPModel):
    experiment: UUID
    quote_id: str | None = Field(default=None, max_length=64)


class ListWorkflowsInput(MCPModel):
    kind: Literal[
        "data_exploration",
        "data_partition",
        "native_evaluation",
        "training_experiment",
        "decision_performance",
    ]
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class WorkflowOutput(MCPModel):
    summary: str
    workflow: WorkflowReceipt
    resource_links: list[ResourceLinkContract]


class WorkflowListOutput(MCPModel):
    summary: str
    total: int
    workflows: list[dict[str, Any]]
    resource_links: list[ResourceLinkContract]


class CreatePerformanceInput(MCPModel):
    evaluation: UUID
    name: str = Field(min_length=1, max_length=255)
    request_key: str = Field(min_length=1, max_length=128)
    workload: PerformanceWorkload


class ResumePerformanceInput(MCPModel):
    performance_run: UUID


class DecisionCatalogInput(MCPModel):
    pass


class DecisionCatalogOutput(MCPModel):
    models: list[dict[str, Any]]


class RetryPartitionInput(MCPModel):
    partition: UUID


class SamplingInput(MCPModel):
    rows: int = Field(ge=1, le=1000000)
    seed: int = Field(ge=0, le=4294967295)
    stratify_by: list[str] = Field(default_factory=list, max_length=8)
    target_type: bool = False
    minimum_per_stratum: int = Field(default=1, ge=1)


class DeriveDatasetInput(MCPModel):
    source_cell: UUID
    name: str = Field(min_length=1, max_length=255)
    request_key: str = Field(min_length=1, max_length=128)


class ExploreDatasetInput(DeriveDatasetInput):
    sampling: SamplingInput | None = None


class LaunchComparisonInput(MCPModel):
    evaluation: UUID


class PrepareExperimentInput(MCPModel):
    experiment: UUID


class CreateProfileInput(MCPModel):
    name: str = Field(min_length=1, max_length=255)
    request_key: str = Field(min_length=1, max_length=128)
    variant: CandidateInput
    max_steps: int = Field(ge=1, le=128)
    max_seconds: int = Field(ge=60, le=3600)


class ReusePredictionsInput(MCPModel):
    evaluation: UUID
    source_evaluation: UUID
    participant: str = Field(min_length=1, max_length=64)
    source_participant: str = Field(min_length=1, max_length=64)
