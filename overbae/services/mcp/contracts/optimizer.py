"""Strict contracts for the user-facing optimizer MCP tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from overbae.services.mcp.contracts.common import (
    DatasetCellContract,
    MCPModel,
    ResourceLinkContract,
)

OptimizerMode = Literal["optimize", "model_comparison", "hybrid"]


class OptimizerDatasetReadiness(MCPModel):
    id: str
    name: str
    intent: str | None
    cell: DatasetCellContract | None = None
    usable: bool
    issue: str | None = Field(default=None, max_length=500)


class OptimizerEvalSetReadiness(MCPModel):
    id: str
    name: str
    capability: str
    active: bool
    member_count: int = Field(ge=0)


class OptimizerExecutionerState(MCPModel):
    connected: bool
    hostname: str = Field(default="", max_length=255)
    cli_version: str = Field(default="", max_length=40)
    heartbeat_at: datetime | None = None


class OptimizerModelReadiness(MCPModel):
    mode: OptimizerMode
    requested: list[str] = Field(default_factory=list, max_length=5)
    validated: list[str] = Field(default_factory=list, max_length=5)
    valid: bool
    issue: str | None = Field(default=None, max_length=500)


class OptimizerCreditReadiness(MCPModel):
    required: bool = True
    available: bool


class OptimizerPlanReadiness(MCPModel):
    quota: Literal["optimize_runs"] = "optimize_runs"
    available: bool


class OptimizerNextAction(MCPModel):
    state: Literal[
        "ready", "connect_executioner", "fix_prerequisites", "run_executioner", "inspect_result"
    ]
    message: str = Field(min_length=1, max_length=500)
    command: str | None = Field(default=None, max_length=500)


class CheckOptimizerReadinessInput(MCPModel):
    capability: str = Field(min_length=1, max_length=255)
    dataset: str | None = Field(default=None, max_length=255)
    eval_set: str | None = Field(default=None, max_length=255)
    cell: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=32)
    mode: OptimizerMode = "optimize"
    model_ids: list[str] = Field(default_factory=list, max_length=5)


class CheckOptimizerReadinessOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    ready: bool
    missing: list[str] = Field(default_factory=list, max_length=20)
    capability: ResourceLinkContract
    dataset: OptimizerDatasetReadiness | None = None
    eval_set: OptimizerEvalSetReadiness | None = None
    executioner: OptimizerExecutionerState
    models: OptimizerModelReadiness
    credits: OptimizerCreditReadiness
    plan: OptimizerPlanReadiness
    next_action: OptimizerNextAction
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=4)


class StartOptimizerInput(MCPModel):
    capability: str = Field(min_length=1, max_length=255)
    dataset: str = Field(min_length=1, max_length=255)
    eval_set: str | None = Field(default=None, max_length=255)
    cell: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=32)
    mode: OptimizerMode = "optimize"
    model_ids: list[str] = Field(default_factory=list, max_length=5)
    entrypoint: str = Field(default="", max_length=512)
    code_trigger: str = Field(default="", max_length=10_000)
    num_iterations: int = Field(default=5, ge=2, le=5)
    num_candidates_per_iteration: int = Field(default=3, ge=2, le=3)
    max_iterations_without_improvement: int = Field(default=3, ge=1, le=10)


class OptimizerExperimentReference(MCPModel):
    id: str
    status: str
    mode: OptimizerMode
    capability: ResourceLinkContract
    cell: DatasetCellContract | None = None
    resource: ResourceLinkContract


class OptimizerJobReference(MCPModel):
    id: str
    kind: Literal["optimizer_experiment"] = "optimizer_experiment"
    status: str
    resource: ResourceLinkContract


class StartOptimizerOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    experiment_id: str
    experiment: OptimizerExperimentReference
    job: OptimizerJobReference
    executioner: OptimizerExecutionerState
    next_action: OptimizerNextAction
    resource_links: list[ResourceLinkContract] = Field(max_length=4)


class InspectOptimizerResultInput(MCPModel):
    experiment: str = Field(min_length=1, max_length=255)
    max_iterations: int = Field(default=50, ge=1, le=50)
    max_candidates_per_iteration: int = Field(default=50, ge=1, le=50)


class OptimizerCandidateResult(MCPModel):
    id: str
    index: int = Field(ge=0)
    status: str
    is_baseline: bool
    target_model: str | None = None
    score: float
    coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    graded_rows: int | None = Field(default=None, ge=0)
    total_rows: int | None = Field(default=None, ge=0)
    suite_incomplete: bool = False
    uncovered_card_claims: list[str] = Field(default_factory=list, max_length=50)
    scores: dict[str, Any] = Field(default_factory=dict, max_length=100)
    patch: str | None = Field(default=None, max_length=4_000)
    patch_truncated: bool = False
    eval_run: ResourceLinkContract | None = None


class OptimizerIterationResult(MCPModel):
    id: str
    order: int = Field(ge=0)
    name: str
    status: str
    scores: dict[str, Any] = Field(default_factory=dict, max_length=100)
    candidates: list[OptimizerCandidateResult] = Field(max_length=50)
    candidates_truncated: bool = False


class OptimizerWinner(MCPModel):
    candidate_id: str | None = None
    target_model: str | None = None
    score: float
    kind: Literal["candidate", "incumbent"]


class OptimizerResultSummary(MCPModel):
    id: str
    status: str
    mode: OptimizerMode
    capability: ResourceLinkContract
    cell: DatasetCellContract | None = None
    current_iteration: int = Field(ge=0)
    num_iterations: int = Field(ge=0)
    scores: dict[str, Any] = Field(default_factory=dict, max_length=100)
    failure_reason: str | None = Field(default=None, max_length=1_000)
    resource: ResourceLinkContract


class InspectOptimizerResultOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    experiment: OptimizerResultSummary
    iterations: list[OptimizerIterationResult] = Field(max_length=50)
    iterations_truncated: bool = False
    winner: OptimizerWinner | None = None
    executioner: OptimizerExecutionerState
    next_action: OptimizerNextAction
    resource_links: list[ResourceLinkContract] = Field(max_length=102)
