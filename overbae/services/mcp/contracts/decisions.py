"""Strict contracts for decision jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from overbae.services.mcp.contracts.common import (
    MCPModel,
    PinnedVersions,
    ResourceLinkContract,
    VersionRef,
    request_key_field,
    version_field,
)
from overbae.services.mcp.contracts.finetuning import FineTuneCreditReadiness


class StartDecisionJobInput(MCPModel):
    model: str = Field(min_length=1, max_length=255)
    dataset: str = Field(min_length=1, max_length=255)
    version: str | None = version_field()
    eval_dataset: str | None = Field(default=None, min_length=1, max_length=255)
    eval_version: str | None = version_field()
    request_key: str | None = request_key_field()


class DecisionTraining(MCPModel):
    examples: int
    unit: Literal["rows", "questions"]
    holdout: int | None = None
    skipped: int | None = None
    steps: int | None = None
    calibration: dict[str, Any] | None = None


class DecisionJobContract(MCPModel):
    id: str
    model: str
    status: str
    versions: PinnedVersions
    error: str | None = None
    evaluation: dict[str, Any] = Field(default_factory=dict)
    evaluation_status: str | None = None
    checkpoint_ready: bool = False
    loss: list[float] = Field(default_factory=list, max_length=2)
    training: DecisionTraining | None = None
    cost_usd: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    resource: ResourceLinkContract


class StartDecisionJobOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    job: DecisionJobContract
    resource_links: list[ResourceLinkContract] = Field(max_length=2)


class DecisionCandidate(MCPModel):
    model: str
    fits: bool
    why_not: str | None = None
    decision_index: float | None = None
    decision_index_edition: str | None = None
    params: int = Field(ge=0)


class DecisionPick(MCPModel):
    labels: list[str] = Field(min_length=1, max_length=3)
    model: str
    reason: str


class DecisionDatasetReference(MCPModel):
    id: str
    name: str = Field(max_length=255)
    intent: str
    version: VersionRef | None = None


class CheckDecisionReadinessInput(MCPModel):
    dataset: str = Field(min_length=1, max_length=255)
    version: str | None = version_field()
    eval_dataset: str | None = Field(default=None, min_length=1, max_length=255)
    eval_version: str | None = version_field()


class CheckDecisionReadinessOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    ready: bool
    missing: list[str] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=100)
    dataset: DecisionDatasetReference
    eval_dataset: DecisionDatasetReference | None = None
    picks: list[DecisionPick] = Field(default_factory=list, max_length=3)
    recommendations: list[DecisionCandidate] = Field(default_factory=list, max_length=100)
    excluded: list[DecisionCandidate] = Field(default_factory=list, max_length=100)
    credits: FineTuneCreditReadiness
    resource_links: list[ResourceLinkContract] = Field(max_length=2)


class RunDecisionModelInput(MCPModel):
    model: str | None = Field(default=None, min_length=1, max_length=255)
    job: str | None = Field(default=None, min_length=1, max_length=255)
    state: str = Field(max_length=32_000)
    questions: dict[str, Any] = Field(min_length=1, max_length=100)


class DecisionUsage(MCPModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class RunDecisionModelOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    model: str
    answers: dict[str, Any] = Field(default_factory=dict, max_length=100)
    usage: DecisionUsage
    latency_ms: float = Field(ge=0)
    is_cold: bool
    resource: ResourceLinkContract
