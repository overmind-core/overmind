"""Strict contracts for the project-scoped evaluation MCP tools."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AliasChoices, Field, field_validator, model_validator

from overbae.services.eval.decisions import policy_for
from overbae.services.mcp.contracts.common import (
    DatasetCellContract,
    MCPModel,
    ResourceLinkContract,
)

EvaluatorKind = Literal["llm_judge", "trajectory", "deterministic", "statistical", "agentic"]
EvaluatorScope = Literal["final_output", "turn", "step", "trajectory", "sample", "dataset"]
ScoreType = Literal["numeric", "categorical", "boolean"]
EvaluationMode = Literal["existing", "generate"]
EvaluationRole = Literal["generative", "trace_scoring"]

_VARIABLE_SOURCES = {
    "output",
    "final_output",
    "input",
    "last_user_input",
    "all_user_messages",
    "conversation",
    "reference",
    "expected",
    "expected_output",
    "messages",
    "trajectory",
    "tool_calls",
    "tool_definitions",
    "metadata",
    "structured",
    "sample",
}
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


class ChecklistItemContract(MCPModel):
    id: str = Field(min_length=1, max_length=128)
    q: str = Field(min_length=1, max_length=2_000)
    weight: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    gate: bool = False
    field: str = Field(default="", max_length=255)
    applies_when: dict[str, Any] | None = Field(default=None, max_length=50)


class VariableMappingContract(MCPModel):
    var: str = Field(min_length=1, max_length=128)
    source: str = Field(max_length=40)
    jsonpath: str = Field(default="", max_length=512)

    @field_validator("source")
    @classmethod
    def source_is_supported(cls, value: str) -> str:
        if value and value not in _VARIABLE_SOURCES:
            raise ValueError("source is not supported by the evaluator binder")
        return value


class ExistingVariantContract(MCPModel):
    mode: Literal["existing"]
    label: str = Field(default="Captured traces", min_length=1, max_length=255)
    model_ref: str | None = Field(default=None, max_length=80)
    model_name: str = Field(default="", max_length=255)
    prompt: str | None = Field(default=None, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict, max_length=50)
    is_baseline: bool = False
    order: int = Field(default=0, ge=0, le=100)

    @field_validator("params")
    @classmethod
    def params_are_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_safe_json(value, field="params")


class GenerateVariantContract(MCPModel):
    mode: Literal["generate"]
    label: str = Field(min_length=1, max_length=255)
    model_ref: str | None = Field(default=None, max_length=80)
    model_name: str = Field(default="", max_length=255)
    prompt: str | None = Field(default=None, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict, max_length=50)
    is_baseline: bool = False
    order: int = Field(default=0, ge=0, le=100)

    @field_validator("params")
    @classmethod
    def params_are_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_safe_json(value, field="params")


EvaluationVariantContract = Annotated[
    ExistingVariantContract | GenerateVariantContract,
    Field(discriminator="mode"),
]


class EvaluationContextVariantContract(ExistingVariantContract):
    mode: EvaluationMode = "generate"
    label: str = Field(default="", max_length=255)
    output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)


class CheckEvaluationReadinessInput(MCPModel):
    judge_model: str = Field(default="", max_length=255)
    dataset: str = Field(min_length=1, max_length=255)
    eval_set: str | None = Field(
        default=None,
        max_length=255,
        validation_alias=AliasChoices("eval_set", "eval_set_id", "eval_set_name"),
    )
    cell: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=32)
    mode: EvaluationMode = "existing"
    variants: list[EvaluationContextVariantContract] = Field(default_factory=list, max_length=20)


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


class BindingReadinessContract(MCPModel):
    mode: EvaluationMode
    status: Literal["green", "amber", "red", "unknown", "skipped"]
    checked_units: int = Field(ge=0)
    variables: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    failing_vars: list[str] = Field(default_factory=list, max_length=100)
    skipped_vars: list[str] = Field(default_factory=list, max_length=100)
    reason: str = Field(default="", max_length=500)


class EvaluationDatasetContract(MCPModel):
    id: str
    name: str
    intent: Literal["eval"]
    cell: DatasetCellContract | None = None


class EvaluatorReadinessContract(MCPModel):
    id: str
    name: str
    kind: EvaluatorKind
    roles: list[EvaluationRole] = Field(max_length=2)
    applicable: bool
    binding: BindingReadinessContract


class EvalSetReadinessContract(MCPModel):
    id: str
    name: str
    capability: str | None
    active: bool
    member_count: int = Field(ge=0)


class CreateEvalSetInput(MCPModel):
    name: str = Field(min_length=1, max_length=255)
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    evaluator_ids: list[UUID] = Field(min_length=1, max_length=100)


class CreateEvalSetOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    eval_set: EvalSetReadinessContract
    resource_links: list[ResourceLinkContract] = Field(max_length=1)


class CheckEvaluationReadinessOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    ready: bool
    context_checks: list[EvaluationContextCheckContract] = Field(default_factory=list)
    judge_models: list[EvaluationJudgeOptionContract] = Field(default_factory=list, max_length=50)
    dataset: EvaluationDatasetContract
    eval_set: EvalSetReadinessContract | None = None
    evaluators: list[EvaluatorReadinessContract] = Field(max_length=100)
    credits: CreditReadinessContract
    resource_links: list[ResourceLinkContract] = Field(max_length=4)


class EvaluatorUpsertInput(MCPModel):
    evaluator: str | None = Field(
        default=None,
        max_length=255,
        validation_alias=AliasChoices("evaluator", "evaluator_id"),
    )
    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: EvaluatorKind = "llm_judge"
    capability: str | None = Field(
        default=None,
        max_length=255,
        validation_alias=AliasChoices("capability", "capability_id"),
    )
    scope: EvaluatorScope = "final_output"
    score_type: ScoreType = "numeric"
    display_name: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=2_000)
    rubric_md: str = Field(default="", max_length=12_000)
    evaluation_prompt: str = Field(default="", max_length=12_000)
    checklist: list[ChecklistItemContract] = Field(default_factory=list, max_length=100)
    judge_model: str = Field(default="", max_length=255)
    score_min: float = Field(default=0.0, allow_inf_nan=False)
    score_max: float = Field(default=1.0, allow_inf_nan=False)
    pass_threshold: float | None = Field(default=None, allow_inf_nan=False)
    requires_reference: bool = False
    variable_mapping: list[VariableMappingContract] = Field(default_factory=list, max_length=100)
    config: dict[str, Any] = Field(
        default_factory=dict,
        max_length=100,
        description="config.decision selects backend generative (default) or jev (opt-in bounded decisions with generative fallback), min_confidence 0..1, and version 1. Qualify Jev against labelled examples for the rubric; confidence is not accuracy. The decision model must be a registered Jev release. judge_model remains the generative judge/fallback. Label-only categorical evaluators support Jev; categorical checklists and behaviour contracts retain generative review. Jev returns decision provenance without a generated explanation.",
    )
    applicable_roles: list[EvaluationRole] = Field(default_factory=list, max_length=2)
    surface: Literal["model", "harness", "any"] = "any"
    choices: dict[str, float] | None = Field(default=None, max_length=20)

    @field_validator("config")
    @classmethod
    def config_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        policy_for(config=value)
        return _validate_safe_json(value, field="config")

    @model_validator(mode="after")
    def create_has_name(self) -> EvaluatorUpsertInput:
        if not self.evaluator and not self.name:
            raise ValueError("name is required when evaluator is omitted")
        if self.score_max <= self.score_min:
            raise ValueError("score_max must be greater than score_min")
        if self.score_type == "categorical" and not self.evaluator:
            if not self.choices or len(self.choices) < 2:
                raise ValueError("categorical evaluators need at least two choices")
            if len({label.casefold() for label in self.choices}) != len(self.choices):
                raise ValueError("categorical choices must be unique")
        return self


class EvaluatorUpsertOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    status: Literal["created", "updated"]
    id: str
    name: str
    version: int = Field(ge=1)
    kind: EvaluatorKind
    scope: EvaluatorScope
    score_type: ScoreType
    resource: ResourceLinkContract
    resource_links: list[ResourceLinkContract] = Field(max_length=2)


class RunEvaluationInput(MCPModel):
    judge_model: str = Field(
        default="",
        max_length=255,
        description="Run-only generative judge/fallback; blank keeps saved choices. Jev policy unchanged.",
    )
    name: str = Field(min_length=1, max_length=255)
    dataset: str = Field(min_length=1, max_length=255)
    eval_set: str | None = Field(
        default=None,
        max_length=255,
        validation_alias=AliasChoices("eval_set", "eval_set_id", "eval_set_name"),
    )
    cell: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=32)
    evaluator_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
        validation_alias=AliasChoices("evaluator_ids", "evaluators"),
    )
    variants: list[EvaluationVariantContract] = Field(
        default_factory=list,
        max_length=20,
        validation_alias=AliasChoices("variants", "variants_input"),
    )
    max_items: int = Field(default=100, ge=1, le=100_000)
    sampling: float = Field(default=1.0, gt=0, le=1, allow_inf_nan=False)


class EvaluationJobContract(MCPModel):
    id: str
    kind: Literal["eval_run"]
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    resource: ResourceLinkContract


class EvaluationVariantSummaryContract(MCPModel):
    id: str
    label: str
    mode: EvaluationMode
    is_baseline: bool


class RunEvaluationOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    run_id: str
    job: EvaluationJobContract
    cell: DatasetCellContract | None = None
    variants: list[EvaluationVariantSummaryContract] = Field(max_length=20)
    resource: ResourceLinkContract
    resource_links: list[ResourceLinkContract] = Field(max_length=3)


class CompareEvaluationsInput(MCPModel):
    run: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("run", "eval_run", "eval_run_id"),
    )
    baseline: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("baseline", "baseline_run", "baseline_run_id"),
    )


class EvaluationAggregateContract(MCPModel):
    mean: float | None
    pass_rate: float | None
    n: int = Field(ge=0)
    variant_count: int = Field(ge=0)
    primary: float | None


class ComparisonRowContract(MCPModel):
    name: str
    current: EvaluationAggregateContract | None
    baseline: EvaluationAggregateContract | None
    delta: float | None
    status: Literal["improved", "regressed", "unchanged", "added", "removed"]


class ComparisonOverallContract(MCPModel):
    current: EvaluationAggregateContract | None
    baseline: EvaluationAggregateContract | None
    delta: float | None
    status: Literal["improved", "regressed", "unchanged", "added", "removed"]


class TrustContract(MCPModel):
    trusted: bool
    degraded: int = Field(ge=0)
    evaluator_errors: int = Field(ge=0)
    errored: int = Field(ge=0)
    not_applicable: int = Field(ge=0)


class ComparisonTrustContract(MCPModel):
    current: TrustContract
    baseline: TrustContract


class CompareEvaluationsOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    current_run_id: str
    baseline_run_id: str
    current_name: str
    baseline_name: str
    rows: list[ComparisonRowContract] = Field(max_length=100)
    overall: ComparisonOverallContract
    trust: ComparisonTrustContract
    not_applicable_by_evaluator: dict[str, Any] = Field(default_factory=dict, max_length=100)
    resource_links: list[ResourceLinkContract] = Field(max_length=2)


class AnnotateEvaluationSampleInput(MCPModel):
    annotation: str | None = Field(
        default=None,
        max_length=255,
        validation_alias=AliasChoices("annotation", "annotation_id"),
    )
    sample: str | None = Field(
        default=None,
        max_length=255,
        validation_alias=AliasChoices("sample", "sample_id"),
    )
    evaluator: str | None = Field(default=None, max_length=255)
    value: float | None = Field(default=None, allow_inf_nan=False)
    label: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=5_000)

    @model_validator(mode="after")
    def target_is_present(self) -> AnnotateEvaluationSampleInput:
        if not self.annotation and not self.sample:
            raise ValueError("sample is required when annotation is omitted")
        if not self.annotation and self.value is None and not self.label.strip():
            raise ValueError("provide value and/or label when creating an annotation")
        return self


class AnnotationOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    status: Literal["created", "updated"]
    id: str
    sample_id: str
    evaluator: str | None = None
    annotated_by: str
    value: float | None
    label: str
    note: str
    resource: ResourceLinkContract
    resource_links: list[ResourceLinkContract] = Field(max_length=2)
