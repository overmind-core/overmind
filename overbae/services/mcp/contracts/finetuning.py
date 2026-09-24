"""Strict contracts for fine-tuning and model lifecycle tools."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from overbae.services.mcp.contracts.common import (
    DatasetCellContract,
    JobReceipt,
    MCPModel,
    ResourceLinkContract,
)


class CheckFinetuneReadinessInput(MCPModel):
    dataset: str = Field(min_length=1, max_length=255)
    capability: str | None = Field(
        default=None, max_length=255, description="Optional capability; omit or null for none."
    )
    cell: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=32)


class FineTuneDatasetReadiness(MCPModel):
    id: str
    name: str
    intent: Literal["train"] | None
    cell: DatasetCellContract | None = None
    validation: dict[str, Any]


class FineTuneEvalDatasetReadiness(MCPModel):
    id: str
    name: str
    intent: Literal["eval"]
    cell: DatasetCellContract | None = None


class FineTuneCapabilityReadiness(MCPModel):
    id: str
    name: str
    slug: str


class FineTuneEvalSetReadiness(MCPModel):
    id: str
    name: str
    capability: str | None
    active: bool
    member_count: int = Field(ge=0)


class FineTuneEvaluatorReadiness(MCPModel):
    ready: bool
    eval_set: FineTuneEvalSetReadiness | None = None
    evaluators: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class FineTuneCreditReadiness(MCPModel):
    required: bool
    available: bool


class CheckFinetuneReadinessOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    ready: bool
    missing: list[str] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list)
    dataset: FineTuneDatasetReadiness
    capability: FineTuneCapabilityReadiness | None = None
    eval_dataset: FineTuneEvalDatasetReadiness | None = None
    eval_set: FineTuneEvalSetReadiness | None = None
    overlap_count: int | None = Field(default=None, ge=0)
    recommendations: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    n_candidates: int = Field(ge=0)
    catalog: dict[str, list[str]]
    has_tool_calling: bool
    task_type: str | None = None
    task_type_source: str | None = None
    excluded: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    recommendation_error: str | None = Field(default=None, max_length=500)
    evaluator_readiness: FineTuneEvaluatorReadiness
    credits: FineTuneCreditReadiness
    resource_links: list[ResourceLinkContract] = Field(max_length=4)


class EstimateFinetuneInput(MCPModel):
    dataset: str = Field(min_length=1, max_length=255)
    base_model: str = Field(min_length=1, max_length=255)
    n_epochs: int = Field(default=3, ge=1, le=20)
    use_lora: bool = True
    cell: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=32)


class FineTuneTimeEstimate(MCPModel):
    seconds: int = Field(ge=0)
    human: str = Field(min_length=1, max_length=80)


class EstimateFinetuneOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    cost_estimate: dict[str, Any] | None = None
    time_estimate: FineTuneTimeEstimate
    trained_tokens: int = Field(ge=0)
    cell: DatasetCellContract | None = None
    resource_links: list[ResourceLinkContract] = Field(max_length=1)


class PrepareTrainingInput(MCPModel):
    retry_failed: bool = False
    training_type: Literal["lora", "full"] = "lora"
    dataset: str = Field(min_length=1, max_length=255)
    base_model: str = Field(min_length=1, max_length=255)
    context_length: int = Field(ge=128, le=2_000_000)
    validation_dataset: str | None = None
    cell: str | None = None


class PrepareTrainingOutput(MCPModel):
    job: JobReceipt
    id: str
    state: str
    report: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    config: dict[str, Any]


class StartFinetuneInput(MCPModel):
    dataset: str = Field(min_length=1, max_length=255)
    base_model: str = Field(min_length=1, max_length=255)
    baseline_model: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Run-specific benchmark: codebase incumbent or ready trained serving model ID in this project. Omit for capability default.",
    )
    capability: str | None = Field(
        default=None, max_length=255, description="Optional capability; omit or null for none."
    )
    eval_dataset: str | None = Field(default=None, max_length=255)
    eval_set: str | None = Field(default=None, max_length=255)
    eval_judge_model: str = Field(default="", max_length=255)
    validation_dataset: str | None = Field(default=None, max_length=255)
    cell: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=32)
    eval_cell: str | None = Field(default=None, max_length=255)
    eval_version: str | None = Field(default=None, max_length=32)
    validation_cell: str | None = Field(default=None, max_length=255)
    validation_version: str | None = Field(default=None, max_length=32)
    validation_enabled: bool = True
    eval_incumbent_before: bool | None = Field(
        default=None,
        description=("Evaluate the incumbent separately alongside training; defaults off."),
    )
    eval_incumbent_after: bool = False
    eval_model_before: bool | None = Field(
        default=None,
        description="Evaluate the untouched training base model for a matched comparison; defaults on.",
    )
    eval_model_after: bool = True
    validation_split_ratio: float = Field(default=0.2, ge=0.05, le=0.5)
    split_method: Literal["random", "ordered"] = "random"
    name: str | None = Field(default=None, max_length=255)
    use_case: str = Field(default="", max_length=10_000)
    hyperparameters: dict[str, Any] | None = None
    group_id: UUID | None = None


class FineTuneJobReference(JobReceipt):
    kind: Literal["finetune_job"] = "finetune_job"
    name: str


class StartFinetuneOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    finetune: FineTuneJobReference
    job: FineTuneJobReference
    cell: DatasetCellContract | None = None
    resource_links: list[ResourceLinkContract] = Field(max_length=3)


class RetryDeploymentInput(MCPModel):
    deployment: str = Field(min_length=1, max_length=255)


class DeploymentReference(MCPModel):
    id: str
    model_id: str
    status: str
    inference_url: str | None = None
    resource: ResourceLinkContract


class RetryDeploymentOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    deployment: DeploymentReference
    retry: JobReceipt
    resource_links: list[ResourceLinkContract] = Field(max_length=2)


class SetActiveModelInput(MCPModel):
    capability: str = Field(min_length=1, max_length=255)
    deployment: str | None = Field(default=None, max_length=255)


class SetActiveModelOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    capability: ResourceLinkContract
    active_model: DeploymentReference | None = None
    cleared: bool
    resource_links: list[ResourceLinkContract] = Field(max_length=2)


class SetBenchmarkModelInput(MCPModel):
    capability: str = Field(min_length=1, max_length=255)
    deployment: str | None = Field(default=None, max_length=255)


class SetBenchmarkModelOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    capability: ResourceLinkContract
    benchmark_model: DeploymentReference | None = None
    model_id: str
    source: Literal["codebase", "trained"]
    resource_links: list[ResourceLinkContract] = Field(max_length=2)
