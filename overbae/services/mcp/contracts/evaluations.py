"""Strict contracts for the project-scoped evaluation MCP tools."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator

from overbae.services.mcp.contracts.common import (
    DatasetCellContract,
    MCPModel,
    ResourceLinkContract,
)

MatrixType = Literal["deterministic", "llm"]
TargetMode = Literal["recorded", "generate"]
RunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
ComparisonStatus = Literal["improved", "regressed", "unchanged", "added", "removed"]
MAX_TARGETS = 6

_SENSITIVE_KEY_PARTS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}


def _validate_safe_json(value: dict[str, Any], *, field: str) -> dict[str, Any]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must contain JSON values") from error
    if len(encoded) > 12_000:
        raise ValueError(f"{field} is too large")

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = str(key).lower().replace("-", "_")
                if any(part in normalized.split("_") for part in _SENSITIVE_KEY_PARTS):
                    raise ValueError(f"{field} contains a sensitive field")
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return value


class EvalMatrixRowContract(MCPModel):
    id: str
    name: str
    type: MatrixType
    source: str = Field(max_length=20_000)
    definition_source: Literal["stock", "code", "config", "custom", "unknown"]
    capability: str | None = None
    span_type: str = ""
    span_name: str = ""
    enabled: bool


class CreateEvalMatrixRowInput(MCPModel):
    capability: str | None = Field(
        default=None,
        max_length=255,
        description="Scope the row to one capability; omit for a project-wide row.",
    )
    name: str = Field(min_length=1, max_length=255)
    type: MatrixType
    source: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "deterministic: a script defining evaluate(span) returning a score in [0, 1], "
            "None to abstain, or {score, reason}. llm: one sentence the output must meet."
        ),
    )
    span_type: Literal["", "llm_call", "tool_call", "retrieval", "workflow"] = ""
    span_name: str = Field(default="", max_length=255)


class CreateEvalMatrixRowOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    row: EvalMatrixRowContract
    resource_links: list[ResourceLinkContract] = Field(max_length=1)


class DeleteEvalMatrixRowInput(MCPModel):
    row: str = Field(
        min_length=1,
        max_length=255,
        description="Row id, or its name when the name is unique.",
        validation_alias=AliasChoices("row", "row_id"),
    )
    capability: str | None = Field(
        default=None,
        max_length=255,
        description="Capability to resolve a row name against.",
    )


class DeleteEvalMatrixRowOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    row: EvalMatrixRowContract


class EvalSetContract(MCPModel):
    id: str
    name: str
    capability: str | None
    active: bool
    row_count: int = Field(ge=0)


class CreateEvalSetInput(MCPModel):
    name: str = Field(min_length=1, max_length=255)
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    rows: list[str] = Field(
        min_length=1, max_length=200, description="Eval matrix row ids or names."
    )
    activate: bool = Field(default=False, description="Make it the capability's active set.")


class CreateEvalSetOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    eval_set: EvalSetContract
    resource_links: list[ResourceLinkContract] = Field(max_length=1)


class EvaluationTargetContract(MCPModel):
    label: str = Field(default="", max_length=255)
    mode: TargetMode = "generate"
    model_name: str = Field(default="", max_length=255)
    model_ref: str | None = Field(default=None, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict, max_length=50)
    is_baseline: bool = False
    output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)

    @field_validator("params")
    @classmethod
    def params_are_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_safe_json(value, field="params")


class EvaluationScopeInput(MCPModel):
    judge_model: str = Field(
        default="",
        max_length=255,
        description="Explanation judge for low scores; blank uses the configured default.",
    )
    dataset: str = Field(min_length=1, max_length=255)
    eval_set: str | None = Field(
        default=None,
        max_length=255,
        description="Defaults to the capability's active set, else every enabled row that applies.",
        validation_alias=AliasChoices("eval_set", "eval_set_id", "eval_set_name"),
    )
    cell: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=32)


class CheckEvaluationReadinessInput(EvaluationScopeInput):
    targets: list[EvaluationTargetContract] = Field(default_factory=list, max_length=MAX_TARGETS)


class EvaluationContextSuggestionContract(MCPModel):
    model: str
    name: str
    context_window: int
    max_output_tokens: int
    reserved_output_tokens: int
    estimated_cost_usd: float | None
    cost_delta_usd: float | None


class EvaluationContextCheckContract(MCPModel):
    role: str
    label: str
    model: str
    context_window: int | None
    max_output_tokens: int | None
    estimated_input_tokens: int
    total_input_tokens: int = 0
    configured_model: str = ""
    reserved_output_tokens: int
    required_context: int
    checked_rows: int
    affected_rows: int
    row_indices: list[int] = Field(max_length=10)
    estimated: bool
    status: Literal["fits", "warning", "unknown"]
    message: str
    estimated_cost_usd: float | None = None
    cost_basis: str = ""
    suggestions: list[EvaluationContextSuggestionContract] = Field(
        default_factory=list, max_length=3
    )
    suggestion_note: str = ""


class EvaluationJudgeOptionContract(MCPModel):
    model: str
    name: str
    status: Literal["fits", "warning", "unknown"]
    context_window: int | None
    max_output_tokens: int | None
    reserved_output_tokens: int
    estimated_cost_usd: float | None
    cost_delta_usd: float | None


class CreditReadinessContract(MCPModel):
    required: bool
    available: bool


class EvaluationDatasetContract(MCPModel):
    id: str
    name: str
    intent: Literal["eval"]
    cell: DatasetCellContract | None = None


class MatrixSummaryContract(MCPModel):
    name: str
    type: MatrixType


class CheckEvaluationReadinessOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    ready: bool
    context_checks: list[EvaluationContextCheckContract] = Field(default_factory=list)
    judge_models: list[EvaluationJudgeOptionContract] = Field(default_factory=list, max_length=50)
    dataset: EvaluationDatasetContract
    eval_set: EvalSetContract | None = None
    matrix: list[MatrixSummaryContract] = Field(max_length=200)
    credits: CreditReadinessContract
    resource_links: list[ResourceLinkContract] = Field(max_length=2)


class RunEvaluationInput(EvaluationScopeInput):
    name: str = Field(min_length=1, max_length=255)
    targets: list[EvaluationTargetContract] = Field(
        min_length=1,
        max_length=MAX_TARGETS,
        description=(
            "One run per target in one group. mode=recorded scores the dataset's own outputs; "
            "mode=generate replays each row through model_name or model_ref. Mark one target "
            "is_baseline to compare every other target against it."
        ),
    )
    max_items: int = Field(default=100, ge=1, le=100_000)
    sampling: float = Field(default=1.0, gt=0, le=1, allow_inf_nan=False)


class EvaluationRunContract(MCPModel):
    id: str
    label: str
    mode: TargetMode
    is_baseline: bool
    status: RunStatus
    job: ResourceLinkContract
    resource: ResourceLinkContract


class RunEvaluationOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    group: str
    runs: list[EvaluationRunContract] = Field(max_length=MAX_TARGETS)
    cell: DatasetCellContract | None = None
    resource_links: list[ResourceLinkContract] = Field(max_length=2 * MAX_TARGETS + 1)


class CompareEvaluationsInput(MCPModel):
    run: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("run", "eval_run", "eval_run_id"),
    )
    baseline: str | None = Field(
        default=None,
        max_length=255,
        description="Defaults to the completed baseline in the run's group.",
        validation_alias=AliasChoices("baseline", "baseline_run", "baseline_run_id"),
    )


class MetricComparisonContract(MCPModel):
    name: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    status: ComparisonStatus


class OverallComparisonContract(MCPModel):
    baseline: float | None
    candidate: float | None
    delta: float | None
    status: ComparisonStatus


class CompareEvaluationsOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    baseline_run_id: str
    candidate_run_id: str
    baseline_label: str
    candidate_label: str
    overall: OverallComparisonContract
    paired: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    ties: int = Field(ge=0)
    metrics: list[MetricComparisonContract] = Field(max_length=200)
    trust: dict[str, Any] = Field(default_factory=dict)
    resource_links: list[ResourceLinkContract] = Field(max_length=2)
