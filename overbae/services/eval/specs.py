"""The validated contract between eval generators and the runner. The
vocabulary is derived from the runner's real code so the two cannot drift.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from overbae.models.evaluation import Evaluator
from overbae.services.eval.evaluators.deterministic import CHECKS
from overbae.services.eval.evaluators.statistical import METRICS
from overbae.services.eval.evidence import infer_evidence_requirement
from overbae.services.eval.roles import GENERATIVE, TRACE_SCORING, trace_scoring_applicable

logger = logging.getLogger(__name__)

EVAL_SET_ROLES: tuple[str, ...] = (GENERATIVE, TRACE_SCORING)
EVALUATOR_SURFACES: tuple[str, ...] = tuple(Evaluator.Surface.values)
EVALUATOR_KINDS: tuple[str, ...] = tuple(Evaluator.Kind.values)
EVALUATOR_SCOPES: tuple[str, ...] = tuple(Evaluator.Scope.values)
SCORE_TYPES: tuple[str, ...] = tuple(Evaluator.ScoreType.values)
DETERMINISTIC_CHECKS: tuple[str, ...] = tuple(sorted(CHECKS))
STATISTICAL_METRICS: tuple[str, ...] = tuple(sorted(METRICS))

# Supersede archives only these generators, never a hand-written rubric.
TIER0_GENERATOR = "card_compiler@v1"
TIER1_GENERATOR = "tier1_llm@v1"
AUTHORED_GENERATORS: frozenset[str] = frozenset({TIER0_GENERATOR, TIER1_GENERATOR})

# Bump on any change to allocation or routing rules; an incumbent stamped with
# an older version (unstamped reads 0) loses its cell claim on the next scan.
AUTHORING_CONTRACT = 1

# The explicit branches of ``base._source_object``; its generic fallthrough is excluded.
VARIABLE_SOURCES: tuple[str, ...] = (
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
)

# ``text`` never traverses a jsonpath; ``text_or_json`` may carry stringified
# JSON the resolver parses; ``varies`` is dataset-dependent.
SOURCE_TYPES: dict[str, str] = {
    "output": "text_or_json",
    "final_output": "text_or_json",
    "input": "text",
    "last_user_input": "text",
    "all_user_messages": "text",
    "conversation": "text",
    "reference": "varies",
    "expected": "varies",
    "expected_output": "varies",
    "messages": "list",
    "trajectory": "list",
    "tool_calls": "list",
    "tool_definitions": "list",
    "metadata": "object",
    "structured": "object",
    "sample": "object",
}

TEXT_SOURCES: frozenset[str] = frozenset(
    src for src, kind in SOURCE_TYPES.items() if kind == "text"
)

SURFACE_AREAS: tuple[str, ...] = (
    "output_contract",
    "failure_mode",
    "tool_surface",
    "cohort",
    "reference",
    "trajectory",
)

SurfaceArea = Literal[
    "output_contract", "failure_mode", "tool_surface", "cohort", "reference", "trajectory"
]

CLAIM_TYPES: tuple[str, ...] = (
    "grounding",
    "verification",
    "quality",
    "conformance",
    "progress",
    "safety",
)
# Ascending: an envelope at tier N satisfies any warrant requiring <= N.
DETAIL_TIERS: tuple[str, ...] = ("summary", "compacted", "full")

ClaimType = Literal["grounding", "verification", "quality", "conformance", "progress", "safety"]
ClaimGrain = Literal["unit", "trajectory", "terminal", "session"]
ProvenanceClass = Literal["user", "agent", "environment", "harness"]
DetailTier = Literal["summary", "compacted", "full"]


class Claim(BaseModel):
    """Authority is never stored; the composition compiler derives it from the type."""

    type: ClaimType
    grain: ClaimGrain


class Warrant(BaseModel):
    """Checked at mint time against the evidence profile and at dispatch
    against the envelope's labels."""

    provenance: list[ProvenanceClass] = Field(default_factory=lambda: ["agent"])
    detail: DetailTier = "summary"
    requires: list[str] = Field(default_factory=list)

    @field_validator("provenance", "requires")
    @classmethod
    def _sorted_unique(cls, value: list) -> list:
        return sorted(set(value))


_GRAIN_BY_SCOPE: dict[str, str] = {
    "final_output": "terminal",
    "turn": "unit",
    "step": "unit",
    "trajectory": "trajectory",
    "sample": "terminal",
    "dataset": "terminal",
}

_TOOL_EVIDENCE_SOURCES = frozenset(
    {"tool_calls", "tool_definitions", "span_tree", "spans", "execution", "trajectory"}
)


def derive_claim(
    *,
    kind: str,
    scope: str,
    score_type: str,
    config: dict[str, Any] | None,
    surface_area: str = "",
) -> Claim:
    """Also the backfill rule for rows minted before claims were first-class."""
    config = config or {}
    grain = _GRAIN_BY_SCOPE.get(scope, "terminal")
    if config.get("grounding_node"):
        return Claim(type="grounding", grain="terminal")
    if surface_area == "failure_mode":
        return Claim(type="safety", grain=grain)
    if kind in ("deterministic", "statistical"):
        return Claim(type="conformance", grain=grain)
    behaviour_role = (config.get("behaviour") or {}).get("role")
    if behaviour_role == "outcome":
        return Claim(type="verification", grain="terminal")
    if behaviour_role == "step":
        return Claim(type="progress", grain="trajectory" if grain == "terminal" else grain)
    if score_type == "boolean" and grain == "terminal":
        return Claim(type="verification", grain="terminal")
    if grain == "unit":
        return Claim(type="progress", grain="unit")
    return Claim(type="quality", grain=grain)


def derive_warrant(
    *,
    claim: Claim,
    variable_mapping: list[dict[str, Any]] | None,
    requires_reference: bool = False,
) -> Warrant:
    if claim.type == "grounding":
        return Warrant(
            provenance=["environment"],
            detail="full",
            requires=["environment_evidence", "final_output"],
        )
    provenance: set[str] = {"agent"}
    requires: set[str] = set()
    sources = {str(e.get("source") or "") for e in variable_mapping or []}
    if sources & _TOOL_EVIDENCE_SOURCES:
        provenance.add("environment")
        requires.add("tool_io")
    if claim.grain == "terminal":
        requires.add("final_output")
    if requires_reference:
        requires.add("reference")
    if claim.type == "verification":
        # A declared fact is never checked against self-report alone.
        provenance.add("environment")
        requires.add("tool_io")
    detail = "full" if claim.grain in ("unit", "trajectory") else "compacted"
    return Warrant(provenance=sorted(provenance), detail=detail, requires=sorted(requires))


def mintable_claim_types(construct_family: str) -> frozenset[str]:
    """Open constructs have no contract to verify."""
    if construct_family == "open":
        return frozenset({"quality", "progress", "safety", "conformance", "grounding"})
    return frozenset(CLAIM_TYPES)


class SpecProvenance(BaseModel):
    source: str = Field(description="Grounding artifact path, e.g. 'dataset_card.failure_modes[1]'")
    data_version: str = ""
    codebase_commit: str = ""
    baseline_match_rate: float | None = None
    generator: str = Field(description="Producer tag, e.g. 'card_compiler@v1'")
    authoring_contract: int = 0  # 0 = pre-stamping
    blocked_on: list[str] = Field(default_factory=list)  # e.g. generate-mode only
    surface_area: SurfaceArea
    authored_for_prompt: str = ""  # empty for prompt-agnostic evals


class ChecklistItem(BaseModel):
    id: str
    q: str
    weight: float = 1.0
    gate: bool = False
    # output_schema key; lets the context graph draw a violates edge without parsing prose.
    field: str = ""
    # ``predicates.py`` grammar; the item is NOT_APPLICABLE when it does not hold.
    applies_when: dict[str, Any] | None = None


class VariableMappingEntry(BaseModel):
    var: str
    source: str
    jsonpath: str = ""

    @field_validator("source")
    @classmethod
    def _source_known(cls, value: str) -> str:
        # "" is a real wire value: the grade-time resolver's schema/alias tiers
        # extract the var as a FIELD of the output (a named source would return
        # the whole source object verbatim).
        if value and value not in VARIABLE_SOURCES:
            raise ValueError(f"unknown variable source {value!r}; valid: {VARIABLE_SOURCES}")
        return value

    @model_validator(mode="after")
    def _jsonpath_legal_for_source(self) -> VariableMappingEntry:
        # Would otherwise resolve to nothing and grade a blank at run time.
        if self.jsonpath and self.source in TEXT_SOURCES:
            raise ValueError(
                f"source {self.source!r} resolves to plain text, so jsonpath "
                f"{self.jsonpath!r} cannot traverse it. Bind a structured source "
                "(output/structured/reference/messages/tool_calls) or drop the jsonpath."
            )
        return self


# Hashed into ``config["content_hash"]`` so a grading change is detectable per score.
_CONTENT_HASH_FIELDS = (
    "kind",
    "scope",
    "score_type",
    "rubric_md",
    "checklist",
    "variable_mapping",
    "pass_threshold",
    "config",
)
_CONTENT_HASH_VOLATILE_CONFIG_KEYS = frozenset({"provenance", "content_hash", "behaviour"})


def content_hash_for_kwargs(kwargs: dict[str, Any]) -> str:
    payload: dict[str, Any] = {}
    for field in _CONTENT_HASH_FIELDS:
        value = kwargs.get(field)
        if field == "config" and isinstance(value, dict):
            value = {k: v for k, v in value.items() if k not in _CONTENT_HASH_VOLATILE_CONFIG_KEYS}
        payload[field] = value
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


class EvaluatorSpec(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    display_name: str = Field(default="", max_length=255)
    description: str = ""
    kind: str
    scope: str = "final_output"
    score_type: str = "numeric"

    rubric_md: str = ""
    checklist: list[ChecklistItem] = Field(default_factory=list)
    judge_model: str = ""

    score_min: float = 0.0
    score_max: float = 1.0
    pass_threshold: float | None = None
    requires_reference: bool = False

    # Derived when omitted; authority is never stored.
    claim: Claim | None = None
    warrant: Warrant | None = None

    # Classification judges: the LLM emits a label, the platform looks the score up.
    choices: dict[str, float] | None = None
    direction: Literal["maximize", "minimize", "neutral"] = "maximize"

    variable_mapping: list[VariableMappingEntry] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    provenance: SpecProvenance
    # Empty => inferred from shape at materialization time (``eval.evidence``).
    evidence_requirement: str = ""
    # Empty => derived from scope at routing time (``eval.roles``).
    applicable_roles: list[str] = Field(default_factory=list)
    # ``model`` (raw extraction contract), ``harness`` (assembled deliverable) or ``any``.
    surface: str = "any"

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, value: str) -> str:
        if value not in EVALUATOR_KINDS:
            raise ValueError(f"unknown kind {value!r}; valid: {EVALUATOR_KINDS}")
        return value

    @field_validator("scope")
    @classmethod
    def _scope_known(cls, value: str) -> str:
        if value not in EVALUATOR_SCOPES:
            raise ValueError(f"unknown scope {value!r}; valid: {EVALUATOR_SCOPES}")
        return value

    @field_validator("score_type")
    @classmethod
    def _score_type_known(cls, value: str) -> str:
        if value not in SCORE_TYPES:
            raise ValueError(f"unknown score_type {value!r}; valid: {SCORE_TYPES}")
        return value

    @field_validator("applicable_roles")
    @classmethod
    def _roles_known(cls, value: list[str]) -> list[str]:
        unknown = [r for r in value if r not in EVAL_SET_ROLES]
        if unknown:
            raise ValueError(f"unknown applicable_roles {unknown!r}; valid: {EVAL_SET_ROLES}")
        return list(dict.fromkeys(value))

    @field_validator("surface")
    @classmethod
    def _surface_known(cls, value: str) -> str:
        if value not in EVALUATOR_SURFACES:
            raise ValueError(f"unknown surface {value!r}; valid: {EVALUATOR_SURFACES}")
        return value

    @model_validator(mode="after")
    def _contract_present(self) -> EvaluatorSpec:
        if self.claim is None:
            self.claim = derive_claim(
                kind=self.kind,
                scope=self.scope,
                score_type=self.score_type,
                config=self.config,
                surface_area=self.provenance.surface_area,
            )
        if self.warrant is None:
            self.warrant = derive_warrant(
                claim=self.claim,
                variable_mapping=[entry.model_dump() for entry in self.variable_mapping],
                requires_reference=self.requires_reference,
            )
        return self

    @model_validator(mode="after")
    def _choices_in_unit_interval(self) -> EvaluatorSpec:
        if self.choices:
            bad = {k: v for k, v in self.choices.items() if not 0.0 <= float(v) <= 1.0}
            if bad:
                raise ValueError(f"choices scores must be in [0, 1]; got {bad}")
        return self

    @model_validator(mode="after")
    def _graded_scores_carry_no_failure_gates(self) -> EvaluatorSpec:
        # A ``pass_threshold`` on a graded score becomes a hard fail downstream.
        # Behaviour outcome/step judges keep ``gate: true``: the verdict becomes ``passed``.
        if self.score_type != "boolean":
            self.pass_threshold = None
            role = (self.config or {}).get("behaviour", {}).get("role")
            if role not in ("step", "outcome"):
                for item in self.checklist:
                    item.gate = False
        return self

    @model_validator(mode="after")
    def _roles_fit_evidence(self) -> EvaluatorSpec:
        # Such an evaluator would always abstain in the trace_scoring role.
        if TRACE_SCORING in self.applicable_roles and not trace_scoring_applicable(
            self.scope, self.effective_evidence(), self.requires_reference
        ):
            self.applicable_roles = [r for r in self.applicable_roles if r != TRACE_SCORING]
        return self

    def effective_evidence(self) -> str:
        return self.evidence_requirement or infer_evidence_requirement(
            kind=self.kind,
            scope=self.scope,
            variable_mapping=[entry.model_dump() for entry in self.variable_mapping],
            requires_reference=self.requires_reference,
            config=self.config,
        )

    @model_validator(mode="after")
    def _config_runnable(self) -> EvaluatorSpec:
        if self.kind == "deterministic":
            check = self.config.get("check")
            if check not in DETERMINISTIC_CHECKS:
                raise ValueError(
                    f"deterministic config['check'] {check!r} not in {DETERMINISTIC_CHECKS}"
                )
            if check == "regex":
                pattern = self.config.get("pattern", "")
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"invalid regex pattern {pattern!r}: {exc}") from exc
        if self.kind == "statistical":
            metric = self.config.get("metric")
            if metric not in STATISTICAL_METRICS:
                raise ValueError(
                    f"statistical config['metric'] {metric!r} not in {STATISTICAL_METRICS}"
                )
            if self.scope != "dataset":
                raise ValueError("statistical evaluators must use scope='dataset'")
        if self.kind in ("llm_judge", "agentic") and not (self.rubric_md or self.checklist):
            raise ValueError(f"{self.kind} evaluators need a rubric_md or a checklist")
        return self

    def provenance_key(self) -> tuple[str, str]:
        return (self.provenance.source, self.provenance.data_version)

    def to_evaluator_kwargs(self) -> dict[str, Any]:
        config = dict(self.config)
        config["provenance"] = self.provenance.model_dump(exclude_none=True)
        variable_mapping = [entry.model_dump() for entry in self.variable_mapping]
        requirement = self.evidence_requirement or infer_evidence_requirement(
            kind=self.kind,
            scope=self.scope,
            variable_mapping=variable_mapping,
            requires_reference=self.requires_reference,
            config=config,
        )
        kwargs = {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "kind": self.kind,
            "scope": self.scope,
            "score_type": self.score_type,
            "rubric_md": self.rubric_md,
            # exclude_none keeps ``applies_when`` off items that don't carry one.
            "checklist": [item.model_dump(exclude_none=True) for item in self.checklist],
            "judge_model": self.judge_model,
            "score_min": self.score_min,
            "score_max": self.score_max,
            "pass_threshold": self.pass_threshold,
            "requires_reference": self.requires_reference,
            "evidence_requirement": requirement,
            "surface": self.surface,
            "applicable_roles": list(self.applicable_roles),
            "variable_mapping": variable_mapping,
            "config": config,
        }
        config["content_hash"] = content_hash_for_kwargs(kwargs)
        if self.choices:
            kwargs["choices"] = [{"label": k, "value": v} for k, v in self.choices.items()]
        kwargs["spec_data"] = self.model_dump(mode="json", exclude_none=True)
        return kwargs


def spec_for_evaluator(evaluator) -> EvaluatorSpec:
    """The validated spec for an ``Evaluator`` row. ``spec_data`` is the single
    representation: spec-first writers set it via :meth:`EvaluatorSpec.to_evaluator_kwargs`,
    column-wise writers via :func:`derive_spec_data` at save, and migration 0138
    backfilled the rows that predate it."""
    return EvaluatorSpec.model_validate(getattr(evaluator, "spec_data", None) or {})


def derive_spec_data(evaluator) -> dict[str, Any]:
    """``spec_data`` payload for a column-authored row (the generic evaluator
    API and direct ORM creation). Raises ``ValidationError`` when the columns
    don't form a valid spec."""
    config = dict(evaluator.config or {})
    provenance = config.get("provenance")
    if not isinstance(provenance, dict) or "source" not in provenance:
        provenance = {
            "source": f"evaluator:{evaluator.name}",
            "generator": "spec_backfill@v1",
            "surface_area": "output_contract",
        }
    choices = {
        str(c.get("label")): float(c.get("value", 0.0))
        for c in (evaluator.choices or [])
        if isinstance(c, dict) and c.get("label")
    } or None
    spec = EvaluatorSpec.model_validate(
        {
            "name": evaluator.name,
            "display_name": evaluator.display_name or "",
            "description": evaluator.description or "",
            "kind": evaluator.kind,
            "scope": evaluator.scope,
            "score_type": evaluator.score_type,
            "rubric_md": evaluator.rubric_md or "",
            "checklist": evaluator.checklist or [],
            "judge_model": evaluator.judge_model or "",
            "score_min": evaluator.score_min,
            "score_max": evaluator.score_max,
            "pass_threshold": evaluator.pass_threshold,
            "requires_reference": evaluator.requires_reference,
            "evidence_requirement": evaluator.evidence_requirement or "",
            "applicable_roles": evaluator.applicable_roles or [],
            "surface": evaluator.surface,
            "variable_mapping": evaluator.variable_mapping or [],
            "config": config,
            "provenance": provenance,
            "choices": choices,
        }
    )
    return spec.model_dump(mode="json", exclude_none=True)


def validate_spec_payloads(
    payloads: list[dict[str, Any]],
) -> tuple[list[EvaluatorSpec], list[str]]:
    """Invalid entries are dropped so one bad spec never sinks the batch."""
    specs: list[EvaluatorSpec] = []
    errors: list[str] = []
    for i, payload in enumerate(payloads or []):
        try:
            specs.append(EvaluatorSpec.model_validate(payload))
        except ValidationError as exc:
            name = payload.get("name", "?") if isinstance(payload, dict) else "?"
            detail = "; ".join(e.get("msg", "") for e in exc.errors()[:3])
            errors.append(f"specs[{i}] ({name}): {detail}")
            logger.warning("Dropping invalid EvaluatorSpec at index %d (%s): %s", i, name, detail)
    return specs, errors
