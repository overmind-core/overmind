"""Semantic dataset analysis (cached on DatasetContext) and Tier-1 judge
authoring: with a resolved grounding context, ``EvaluatorSpec``-shaped LLM
judges are authored on top of the Tier 0 compiled list for the generative
(reference-grounded) suite. Live trace scoring uses Tier 0 card evaluators
and behaviour task/step judges — not Tier 1 LLM judges."""

from __future__ import annotations

import logging
import re
from collections.abc import Collection
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from overbae.core.llms import RETRY_DEADLINE_INTERACTIVE, call_llm, try_json_parsing
from overbae.core.model_registry import TaskType, model_chain, resolve_model
from overbae.services.eval.card_compiler import (
    SignalAllocation,
    allocate_signals,
    card_output_field_names,
    generate_observes_tool_calls,
    prepare_judge_checklist,
    resolve_construct,
)
from overbae.services.eval.grounding import EvalGroundingContext, render_grounding_pack
from overbae.services.eval.rubric_compiler import (
    AuthoredAppliesWhen,
    _dump_item_with_predicate,
    align_variable_mapping,
    schema_path_errors,
)
from overbae.services.eval.sanitation import (
    CHECKLIST_CLUSTER_JACCARD,
    checklist_jaccard,
    grades_stated_confidence,
    is_mechanical_field_compare,
    sanitize_authored_text,
)
from overbae.services.eval.specs import (
    AUTHORING_CONTRACT,
    SURFACE_AREAS,
    TIER1_GENERATOR,
    VARIABLE_SOURCES,
    EvaluatorSpec,
    SpecProvenance,
)
from overbae.services.eval.surface_binding import (
    card_claims_for_source,
    enforce_surface_bindings,
)

logger = logging.getLogger(__name__)

_SAMPLE_SIZE = 8
_MAX_VALUE_LEN = 400


class _EvaluatorScore(BaseModel):
    evaluator_id: str = Field(description="The evaluator's UUID string, exactly as provided")
    # No ge/le: Anthropic structured outputs reject minimum/maximum on numbers;
    # clamped in _run_analysis instead.
    score: float = Field(
        description=(
            "Relevance score for this dataset: "
            "0.9–1.0=critical, 0.6–0.8=useful, 0.3–0.5=marginal, 0.0–0.2=not useful"
        ),
    )
    reason: str = Field(
        description="One concise sentence explaining why this evaluator is or isn't valuable here"
    )


class _SuggestedRubric(BaseModel):
    name: str = Field(
        description=(
            "Concise human-readable Title Case or sentence-style name (spaces; "
            "max ~5 words). Never snake_case, kebab-case, or underscore slugs."
        )
    )
    rubric: str = Field(description="Natural-language rubric, 2–4 sentences")
    reason: str = Field(
        description="One sentence: why this quality dimension isn't covered by existing evaluators"
    )


class _SemanticAnalysis(BaseModel):
    domain: str = Field(
        description="Short domain label, e.g. 'customer support', 'medical QA', 'code generation', 'translation'"
    )
    task_description: str = Field(
        description="One sentence describing what the dataset is testing / the AI's job"
    )
    task_type: str = Field(
        default="",
        description=(
            "The single best-fit task type for this dataset. Must be exactly one of: "
            "classification, extraction, summarization, question_answering, "
            "code_generation, tool_calling, reasoning_math, translation, "
            "creative_writing, dialogue. Pick the closest match — never invent a "
            "value and never return more than one."
        ),
    )
    evaluator_scores: list[_EvaluatorScore] = Field(
        description="Score EVERY evaluator in the provided list — do not omit any"
    )
    suggested_rubrics: list[_SuggestedRubric] = Field(
        default_factory=list,
        description=(
            "Up to 2 custom evaluators worth creating for quality dimensions "
            "not well covered by the existing library. Omit if the existing set is sufficient."
        ),
    )


_SYSTEM = """\
You are a senior ML evaluation expert helping a team decide which metrics to use.

Your job: look at a sample of a dataset, understand its domain and task, then judge
which evaluators actually matter — and flag any quality dimensions the existing library misses.

Be specific and honest. A low score means "this metric adds little signal here", not that
it's broken. Think like a data scientist who has seen hundreds of evaluation setups.
"""

_PROMPT_TEMPLATE = """\
## Dataset context

{context}

## Available evaluators

{evaluators_block}

## Instructions

1. Identify the domain and task from the samples (be specific — e.g. "e-commerce product Q&A" not just "Q&A").
2. Score EVERY evaluator above (0.0–1.0) for how valuable it is for THIS specific dataset:
   - 0.9–1.0  critical — definitely run this
   - 0.6–0.8  useful — worth including
   - 0.3–0.5  marginal — only if bandwidth allows
   - 0.0–0.2  not useful or potentially misleading for this data
3. Include a one-sentence reason per evaluator explaining your score.
4. Suggest up to 2 custom evaluators for quality dimensions the library doesn't cover.
   Only suggest if genuinely valuable — leave the list empty if the existing set is complete.

Return JSON matching the schema exactly. Include ALL evaluator IDs — do not skip any.
"""


def analyze_dataset_semantically(
    dataset,
    evaluators: list,
    profile: dict[str, Any],
    grounding: EvalGroundingContext | None = None,
) -> dict[str, Any]:
    """Any failure returns ``{"available": False}`` so callers fall back to rules."""
    try:
        return _run_analysis(dataset, evaluators, profile, grounding=grounding)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Semantic evaluator analysis failed, falling back to rules: %s", exc)
        return {"available": False, "error": str(exc)}


def _run_analysis(
    dataset,
    evaluators: list,
    profile: dict[str, Any],
    grounding: EvalGroundingContext | None = None,
) -> dict[str, Any]:
    samples = _sample_data(dataset)
    context = render_grounding_pack(grounding, sample_rows=samples) if grounding else ""
    if not context:
        if not samples:
            return {"available": False, "error": "No datapoints to analyze"}
        context = f"### Dataset samples ({len(samples)} rows shown)\n\n{_format_samples(samples)}"
    evaluators_block = _format_evaluators(evaluators)

    prompt = _PROMPT_TEMPLATE.format(
        context=context,
        evaluators_block=evaluators_block,
    )

    model = resolve_model(TaskType.EVAL_RECOMMENDATION)
    # Reasoning tokens count against max_tokens on reasoning models; a small
    # cap truncates every answer.
    raw, _ = call_llm(
        prompt,
        system_prompt=_SYSTEM,
        response_format=_SemanticAnalysis,
        model=model,
        fallback_models=model_chain(TaskType.EVAL_RECOMMENDATION),
        retry_deadline=RETRY_DEADLINE_INTERACTIVE,
        max_tokens=_TIER1_MAX_TOKENS,
        reasoning_effort="low",
        request_kwargs={"timeout": _TIER1_LLM_TIMEOUT_S},
    )

    parsed = _SemanticAnalysis.model_validate_json(raw)

    scores: dict[str, dict[str, Any]] = {
        s.evaluator_id: {"score": max(0.0, min(1.0, s.score)), "reason": s.reason}
        for s in parsed.evaluator_scores
    }

    return {
        "available": True,
        "domain": parsed.domain,
        "task_description": parsed.task_description,
        "task_type": (parsed.task_type or "").strip().lower(),
        "evaluator_scores": scores,
        "suggested_rubrics": [r.model_dump() for r in parsed.suggested_rubrics],
    }


def _sample_data(dataset) -> list[dict[str, Any]]:
    from overbae.services.datasets.rows import sample_rows

    points = sample_rows(dataset, _SAMPLE_SIZE)
    out = []
    for p in points:
        inp = p.input
        if isinstance(inp, list):
            user_msg = next(
                (
                    m.get("content", "")
                    for m in reversed(inp)
                    if isinstance(m, dict) and m.get("role") == "user"
                ),
                str(inp),
            )
            inp = user_msg
        exp = p.expected_output or ""
        out.append(
            {
                "input": _clip_sample(str(inp)),
                "expected_output": _clip_sample(str(exp)) if exp else "(none)",
            }
        )
    return out


def _clip_sample(text: str, max_len: int = _MAX_VALUE_LEN) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _format_samples(samples: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for i, s in enumerate(samples, 1):
        lines.append(f"[{i}] Input:    {s['input']}")
        lines.append(f"    Expected: {s['expected_output']}")
        lines.append("")
    return "\n".join(lines)


def _format_evaluators(evaluators: list) -> str:
    lines: list[str] = []
    for ev in evaluators:
        ev_id = str(getattr(ev, "id", ""))
        name = getattr(ev, "name", "")
        desc = getattr(ev, "description", "") or ""
        kind = getattr(ev, "kind", "")
        lines.append(f"- id={ev_id} | {name} ({kind}): {desc}")
    return "\n".join(lines)


# The token cap scales with the judge cap; a mid-object truncation is salvaged
# per-judge by _parse_suite.
_MAX_AUTHORED_JUDGES = 10
_TIER1_MAX_TOKENS = 12000
# Per-attempt LiteLLM timeout — keeps a single slow provider call bounded.
_TIER1_LLM_TIMEOUT_S = 90
# generate-evals runs synchronously in the request; past this Tier 0 is
# returned alone.
_TIER1_BUDGET_S = 480.0

SUITE_GENERATIVE = "generative"

TRACE_NO_GOLD_RULE = (
    "NEVER require or compare against a reference / expected / gold column — "
    "there is none on a live trace. Judge from input / trajectory / tool_calls / "
    "final output evidence only."
)


class _AuthoredChecklistItem(BaseModel):
    id: str = Field(description="Short snake_case item id, e.g. 'keys_align'")
    q: str = Field(description="The yes/no question the judge answers")
    weight: float = 1.0
    gate: bool = Field(
        default=False,
        description=(
            "Boolean judges only: True marks a catastrophic, inherently binary "
            "contract violation that must hard-fail the sample. Must stay false "
            "on numeric/categorical judges (graded scores are never gated)."
        ),
    )
    applies_when: AuthoredAppliesWhen | None = Field(
        default=None,
        description=(
            "ONLY for branch-conditional checks: set EXACTLY ONE leaf "
            "(context_equals, context_present, checkpoint_reached, "
            "output_present or tool_called). context keys must be RUNTIME "
            "context keys the card names — an output field or path is never a "
            "context key; to gate on 'a non-empty deliverable was produced' "
            "use output_present: true; to gate an action-specific check on the "
            "unit actually calling a tool, use tool_called with that tool's "
            "name. Leave null for unconditional checks."
        ),
    )


class _AuthoredVariable(BaseModel):
    var: str = Field(description="Template variable name used in the rubric")
    source: str = Field(description=f"One of: {', '.join(VARIABLE_SOURCES)}")
    jsonpath: str = Field(default="", description="Optional JSONPath into the source object")


class _AuthoredJudge(BaseModel):
    name: str = Field(
        description=(
            "Concise human-readable Title Case or sentence-style name for the "
            "eval library (spaces between words). Never snake_case, kebab-case, "
            "underscores, hyphens, or machine slugs. GOOD: 'Task Success', "
            "'Failure Mode Avoidance', 'Output Contract Semantics'. BAD: "
            "'task_success', 'failure-mode-avoidance', 'output_contract_semantics'."
        )
    )
    description: str = ""
    scope: str = Field(
        default="final_output", description="final_output|turn|step|trajectory|sample"
    )
    score_type: str = Field(default="numeric", description="numeric|categorical|boolean")
    rubric_md: str = Field(description="Natural-language rubric the judge follows")
    checklist: list[_AuthoredChecklistItem] = Field(default_factory=list)
    variable_mapping: list[_AuthoredVariable] = Field(default_factory=list)
    judge_model: str = Field(
        default="", description="Suggested judge model id; '' = platform default"
    )
    requires_reference: bool = False
    pass_threshold: float | None = Field(
        default=None,
        description=(
            "Boolean judges only. Leave null for numeric/categorical judges — "
            "graded scores carry the signal without a pass/fail cut-off."
        ),
    )
    surface_area: str = Field(
        default="output_contract", description=f"One of: {', '.join(SURFACE_AREAS)}"
    )
    grounding_citation: str = Field(
        default="",
        description="The grounding artifact value this eval is lifted from, at its real "
        "path, e.g. 'codebase_card.success_criteria[2]', 'codebase_card.failure_modes[0]' "
        "or 'codebase_card.expected_output.quality_signals[1]'",
    )
    limitation: str = Field(
        default="",
        description="One sentence: what this judge structurally cannot detect. '' if none.",
    )
    gaming_path: str = Field(
        default="",
        description=(
            "One sentence: the easiest way an agent could satisfy this judge "
            "without real quality. '' if none."
        ),
    )


class _AuthoredJudgeSuite(BaseModel):
    evals: list[_AuthoredJudge] = Field(default_factory=list)


_TIER1_SYSTEM = """\
You are a senior ML evaluation engineer authoring the GENERATIVE (reference-grounded)
LLM-judge suite for an AI agent. These judges run when the capability generates over a
dataset that carries a curated golden reference / expected output. Generate-mode without
a tool loop grades the report against gold — not harness runtime, routing, or retriever
state.

You are given grounded context artifacts (a dataset capability card, the capability's codebase
capability card, and a workshop analysis report) plus the list of deterministic evals that
were already compiled from those artifacts.

Index a thorough evaluation SUITE against the capability's intended behaviours AND failure
states — not one mega-judge that vaguely covers everything. Author one focused judge per
distinct grounded surface (success/happy-path criteria, output-contract semantics,
failure modes, quality signals, tool/trajectory/safety when present), each citing the
exact grounding artifact it covers. Author ONLY evals that need semantic judgment —
never re-implement anything a regex or JSON-key check already covers. Every checklist
item must be independently verifiable. Evaluator names must be concise human-readable
Title Case or sentence-style library labels (spaces between words; never snake_case,
kebab-case, hyphens, or underscores).
"""

_TIER1_TEMPLATE = """\
## Grounding context

{grounding_pack}

{example_unit}

## Already-compiled deterministic/statistical evals — do NOT duplicate these

{tier0_block}
{matrix_hint}{construct_block}
## Coverage contract — uncovered signal cells (your authoring allocation)

{allocation_block}

## Authoring rules

1. COVERAGE CONTRACT — the uncovered signal cells above are your allocation. Author
   ONE focused `llm_judge` per cell, setting `grounding_citation` to that cell's
   `cite:` path (at most {max_evals} total — when cells exceed the cap, cover the
   task-success cluster first, then failure modes, then trajectory, then the
   remaining quality clusters). A cell whose NOTE
   says a structural check already exists still needs its SEMANTIC judgment — never
   re-implement the structural check itself. NEVER author two judges for one cell:
   a parsed spec citing an already-covered cell is REJECTED post-parse regardless of
   its name. Skip a cell only when it genuinely needs no semantic judgment; never pad
   with generic ungrounded judges. Guidance per cell family:
   - Task success / happy path: card `success_criteria` (and reference compares when
     an Example sample shows gold leaves).
   - Failure-mode avoidance: card `failure_modes` — one judge (or small group) per
     distinct failure state, not a single catch-all. These judges are DIAGNOSTIC:
     always graded `numeric`, never boolean/gated — structural failure detection is
     the deterministic Tier-0 gates' job, and an LLM re-judging it must not decide
     headline pass/fail.
   - Quality signals: `expected_output.quality_signals` themes that need judgment.
   - Output-contract semantics: fields whose MEANING needs judgment — free text, and
     consistency between fields. Never a field the "already covers" list names.
   - Tool selection / usage quality (only when the Example sample shows tool_calls).
   - Trajectory route selection + adherence versus the mapped paths — only when
     the Example sample shows tool_calls. A generate-mode run with no tool loop
     cannot observe routes; those cells belong to trace scoring.
   - Trajectory efficiency: no redundant/looping steps (only for multi-step capabilities).
   - Constraint / safety compliance: stated constraints that apply to the OUTPUT
     string (PII, credentials in the report). Harness control-plane constraints
     (concurrency, tool budgets, empty-retriever routing) belong to trace scoring
     when the Example sample has no tool_calls.
   - Input faithfulness: grounded in the user's input, no invented facts.
   Only dimensions that genuinely require semantic judgment belong here.
   NAME each evaluator in concise human-readable Title Case or sentence-style with
   spaces (e.g. "Task Success", "Failure Mode Avoidance") — never snake_case,
   kebab-case, hyphens, or underscore slugs.
2. Lift `quality_signals` and `success_criteria` from the context VERBATIM into checklist
   items wherever possible; do not paraphrase away their specifics. Do NOT lift internal
   symbol names (ALL-CAPS identifiers like `_LLM_OUTPUT_KEYS`) or template placeholders
   (`{{total_rows}}`) into a checklist question — they are not real fields. When the
   Example sample has no tool_calls, do not lift criteria that require harness runtime
   (route/mode selection, empty retriever or gathered context, concurrency/semaphores,
   tool budgets); those cannot be decided from INPUT, OUTPUT, and REFERENCE.
3. SCORING — default every judge to graded `numeric` scoring; quality is a continuum
   and the score itself carries the signal. Reserve `boolean` for the rare check that
   is inherently binary AND whose failure must hard-fail the sample (e.g. leaked
   credentials/PII, fabricated facts, invalid output structure). `gate: true` is
   allowed ONLY on a boolean judge's checklist, and only for those catastrophic
   violations. NEVER set `gate: true` or `pass_threshold` on a numeric or categorical
   judge — graded scores are never gated.
4. VARIABLE MAPPING — every judge MUST declare a `variable_mapping` that binds each piece
   of evidence its rubric grades, and reference those bindings in the rubric/checklist as
   `{{var}}` (e.g. judge `{{summary}}` and map a `summary` variable). Prefer the specific
   output field you grade (source `output` + jsonpath into that field) over the whole
   `{{output}}`. Every source must be one of: {sources}. Bind against the REAL shapes
   shown in the Example sample above:
   - A jsonpath only works on a STRUCTURED source. Sources `input`, `last_user_input`,
     `all_user_messages`, `conversation` resolve to plain TEXT — NEVER attach a jsonpath
     to them (it cannot traverse a string and the eval will be rejected).
   - For tool/trajectory evidence use source `tool_calls` (canonical tool graph) with
     NO jsonpath — never `messages` + `$.messages.tool_calls`.
   - For a structured field of the capability's output use source `output` with a jsonpath
     into that field; do NOT assume a wrapper key (e.g. write `$.recommendations`, not
     `$.analysis_compact.recommendations`) — the resolver finds the field wherever it lives.
   - A per-row output field (e.g. this row's `summary.rows`) is NOT a dataset-level total
     (e.g. `total_rows` across the whole dataset). Never bind one to the other.
5. REFERENCE-GROUNDED CHECKS — a field the "already covers" list above names is compared
   EXACTLY by a deterministic eval; it is not yours to grade and a judge that re-checks it
   only re-scores the same thing more expensively and less accurately. Skip those fields.
   Judging whether the right VALUE was chosen (a total rather than a subtotal, a payable
   invoice rather than a paid receipt) is still yours — that needs reading the email, not
   comparing two strings. For every field NOT already covered, a judge that grades
   correctness MUST name one check per field, each with an explicit compare formula.
   Document paths as `{{output.<path>}}` / `{{reference.<path>}}`
   (path INSIDE the braces — never `{{output}}.<path>`), using the OBSERVED leaf from
   the Example sample's surface/shape table for that field:
   "N) <field> — Compare {{output.<observed_output_leaf>}} to
   {{reference.<observed_reference_leaf>}}. PASS if <explicit condition>."
   - numeric fields: absolute-tolerance compare; STATE the tolerance.
   - string fields: trimmed, case-insensitive equality; state any allowed normalization.
   - null rules: state the verdict when a side is null/absent (e.g. PASS iff both null,
     FAIL when exactly one is).
   Never invent a flat reference path from card prose when the surface/shape table shows
   a nested observed leaf for that field.
   Fields the card marks REQUIRED already get a deterministic reference compare
   (exact/tolerance) from Tier 0 — do NOT re-author exact-equality checks for them.
   Author LLM compare judges only for SEMANTIC fields where equivalence needs
   judgment (paraphrase, summary quality, meaning-level agreement).
6. Each judge owns ONE clear part of the capability surface and its rubric is a NUMBERED list
   of named checks ("1) <field> — …", "2) <field> — …"), not a paragraph of prose. A
   quality-signal lifted verbatim still needs its concrete pass/fail condition spelled
   out under it — vague rubric prose that restates the signal is not a check.
7. `scope` must be one of: final_output, turn, step, trajectory, sample.
   `surface_area` must be one of: {areas}.
8. Set `requires_reference: true` when the rubric grades against the reference column.
9. Cite the grounding artifact in `grounding_citation` at its REAL path
   (e.g. "codebase_card.success_criteria[2]", "codebase_card.failure_modes[0]",
   "codebase_card.expected_output.quality_signals[1]",
   "report.agenda_coverage.uncovered_intents[0]") — the grounding context labels each
   list with its citation path.
10. Suggest a `judge_model` only when the eval needs an unusually strong judge; leave ""
   for the platform default.
11. SURFACE DISCIPLINE — grade the surface the Example sample actually shows, NOT the
   source-code schema. A capability's real deliverable often drops or renames fields the code
   declares (e.g. a harness maps a raw model JSON into a different record, dropping keys
   like a boolean classification flag). NEVER author a checklist item that requires a key
   which is absent from the Example sample's output; that field belongs to a different
   layer and enforcing it produces false failures. Generate-mode metadata is runner
   telemetry only (cost, tokens, latency, model, steps, replay_*). Never bind or grade
   any other metadata key — those are harness or agent state this surface cannot observe.
   When the Example sample has no tool_calls, grade only what INPUT / OUTPUT / REFERENCE
   contain. Do not ask about how the run was produced.
12. NEVER encode a confidence-based penalty keyed on schema-field presence (e.g. "if the
   output is confident but a required key is missing, lower the score"). Structural
   key-presence is the deterministic evals' job; a judge that re-penalizes a missing key
   just amplifies wrong-surface failures. Judge MEANING, not structure.
13. BRANCH-CONDITIONAL CHECKS — when a checklist item only applies on a branch the card
   NAMES (a runtime context key or checkpoint, e.g. a document-type flag), set
   the item's `applies_when` to a single-key predicate over the runtime envelope:
   {{"context_equals": {{"key": "<key>", "value": <value>}}}},
   {{"context_present": "<key>"}} or {{"checkpoint_reached": "<name>"}}. The item is
   then skipped as not-applicable instead of scored 0 on the other branch. Leave
   `applies_when` null for unconditional checks — never guess context keys the card
   does not name. An output FIELD or path is NEVER a runtime context key; to gate an
   item on "a record/deliverable was actually produced", set
   {{"output_present": true}} — an empty deliverable ("", [], {{}}, null) does not
   satisfy it. An ACTION-SPECIFIC check (one that grades how a particular tool call
   was performed, e.g. click/input safety) must carry {{"tool_called": "<tool>"}} so
   it never grades a unit that performed no such action.
14. TRAJECTORY COVERAGE — when the grounding pack carries a "Trajectory map" section
   AND the Example sample shows tool_calls, cover the trajectory surface: author
   trajectory-scoped judges (scope `trajectory`, `surface_area: "trajectory"`) that
   grade ROUTE SELECTION (did the run take the path its input called for) and ROUTE ADHERENCE
   (did the run follow that path's declared sequence and tools) against the
   NAMED paths. Embed the relevant path definitions (id, routing, sequence, tools,
   terminal) VERBATIM in the rubric/checklist so the judge grades the trajectory
   evidence it receives against the mapped routes. Use the terminal kinds to separate
   a DECLARED refusal (a `returns_empty`/`escalates` path) from broken output — an
   EMPTY deliverable ("", [], {{}}, null) IS that declared terminal when the map
   names one: grade it as correct route behavior, never as malformed/broken output.
   Only names in the card's "Declared tools" (`tool_spec`) are observable as TOOL
   CALLS; a name in a path's `tools`/`sequence` outside that list is an internal
   callable — never require it as a tool call, judge that path from its
   terminal/output evidence. Cite `codebase_card.trajectory_map[i]`. If the Example
   sample has no tool_calls, do not author route or tool-loop judges.

Return JSON matching the schema exactly.
"""


def _example_dataset(grounding: EvalGroundingContext) -> Any:
    """Capability-first grounding carries no dataset and would leave Tier 1
    blind to real shapes."""
    if grounding.dataset is not None:
        return grounding.dataset
    capability = getattr(grounding, "capability", None)
    if capability is None or getattr(capability, "pk", None) is None:
        return None
    for dataset in capability.datasets.order_by("-updated_at")[:10]:
        cell = dataset.active_cell
        if cell is not None and cell.rows > 0:
            return dataset
    return None


def _render_example_unit_section(grounding: EvalGroundingContext) -> str:
    """Empty on any failure; authoring proceeds on the abstract pack."""
    try:
        from overbae.services.eval.binding_check import sample_units_for_dataset  # noqa: PLC0415
        from overbae.services.eval.grounding import render_example_unit  # noqa: PLC0415

        dataset = _example_dataset(grounding)
        if dataset is None:
            return ""
        units = sample_units_for_dataset(dataset, limit=1)
        if not units:
            return ""
        return render_example_unit(
            units[0], output_field_names=card_output_field_names(grounding.codebase_card)
        )
    except Exception as exc:  # noqa: BLE001 — example unit is additive
        logger.warning("Tier 1 example-unit render failed: %s", exc)
        return ""


def covered_field_names(spec: EvaluatorSpec) -> list[str]:
    """The output fields a compiled check already owns.

    The authoring model cannot tell that ``output-field-accuracy`` covers
    ``amount`` unless the field is written down.
    """
    config = spec.config or {}
    fields = [
        str(f.get("name"))
        for f in (config.get("fields") or [])
        if isinstance(f, dict) and f.get("name")
    ]
    fields += [str(k) for k in (config.get("required_keys") or [])]
    if config.get("prediction_field"):
        fields.append(str(config["prediction_field"]))
    return list(dict.fromkeys(f for f in fields if f))


def _tier0_covers(spec: EvaluatorSpec) -> str:
    unique = sorted(covered_field_names(spec))
    rules = [
        str(e.get("rule") or "").strip()
        for e in (spec.config.get("constraints") or [])
        if isinstance(e, dict) and str(e.get("rule") or "").strip()
    ]
    extra = unique + [r for r in rules if r not in unique]
    return f" — already covers: {', '.join(extra)}" if extra else ""


def _coverage_block(coverage: list[EvaluatorSpec]) -> str:
    if not coverage:
        return "(none compiled)"
    # The card and managed-card compilers both emit the contract and constraint
    # checks, so the caller's concatenation lists them twice.
    seen: set[str] = set()
    lines = []
    for spec in coverage:
        if spec.name in seen:
            continue
        seen.add(spec.name)
        check = spec.config.get("check") or spec.config.get("metric") or ""
        lines.append(
            f"- {spec.name} ({spec.kind}/{check}) — from {spec.provenance.source}"
            f"{_tier0_covers(spec)}"
        )
    lines.append(
        "Any field named above is checked EXACTLY and for free. Do not author a judge "
        "for it — grade only what needs judgement. A self-reported confidence is never "
        "graded per sample: calibration is measured across a whole run."
    )
    return "\n".join(lines)


def _construct_block(construct: dict[str, str]) -> str:
    label = construct.get("construct") or "unknown"
    if construct.get("family") == "open":
        return (
            f"\n## Construct: {label} (open/analytical deliverable)\n\n"
            "This agent's deliverable is analytical/free-form: its envelope fields are "
            "narration, not contractual data, so NO per-field contract gates were "
            "compiled (by design). Do NOT author per-field contract checks, "
            "key-presence checks, or schema-shape semantics. Author graded holistic "
            "judges — task outcome, faithfulness, quality themes, trajectory — always "
            "graded `numeric`.\n"
        )
    return (
        f"\n## Construct: {label} (contract-shaped deliverable)\n\n"
        "This agent's deliverable is a structured record; required keys, schema "
        "conformance and required-field compares are already enforced by "
        "deterministic Tier-0 gates. Author judges for MEANING — semantic "
        "correctness, faithfulness, quality — never structure.\n"
    )


def _matrix_hint_block(matrix_hint: Any) -> str:
    """A hint, never a spec: Tier 1 refines the scan's proposal against the pack."""
    entries = matrix_hint if isinstance(matrix_hint, list) else []
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("managed_name") or "").strip()
        measures = str(entry.get("measures") or "").strip()
        if not (name or measures):
            continue
        label = name or measures
        lines.append(f"- {label}" + (f" — {measures}" if measures and name else ""))
    if not lines:
        return ""
    return (
        "\n## Prior proposed eval intents (HINT — refine against the grounding above; "
        "do not copy verbatim, do not duplicate the Tier 0 evals)\n\n" + "\n".join(lines) + "\n"
    )


def _record_drop(
    drops: list[dict[str, Any]] | None, *, suite: str, name: str, stage: str, reason: str
) -> None:
    logger.warning("Tier 1 %s judge %r dropped (%s): %s", suite, name, stage, reason)
    if drops is not None:
        drops.append({"suite": suite, "name": name, "stage": stage, "reason": reason[:500]})


def _to_spec(
    judge: _AuthoredJudge,
    grounding: EvalGroundingContext,
    *,
    suite: str = SUITE_GENERATIVE,
    drops: list[dict[str, Any]] | None = None,
    covered_fields: Collection[str] = (),
) -> EvaluatorSpec | None:
    surface = judge.surface_area if judge.surface_area in SURFACE_AREAS else "output_contract"
    if surface == "reference":
        surface = "output_contract"
    scope = judge.scope
    # The verbatim-lift rule can drag an internal symbol or placeholder out of
    # the grounding text.
    rubric_md, rubric_leaks = sanitize_authored_text(judge.rubric_md)
    checklist: list[dict[str, Any]] = []
    leaked_tokens = list(rubric_leaks)
    dropped_items: list[str] = []
    for item in judge.checklist:
        cleaned_q, q_leaks = sanitize_authored_text(item.q)
        leaked_tokens.extend(q_leaks)
        entry = _dump_item_with_predicate(item)
        entry["q"] = cleaned_q
        if grades_stated_confidence(entry) or is_mechanical_field_compare(
            cleaned_q, covered_fields
        ):
            logger.info("Dropping checklist item from %r: %s", judge.name, cleaned_q)
            dropped_items.append(cleaned_q)
            continue
        checklist.append(entry)
    if judge.checklist and not checklist:
        logger.info("Dropping judge %r: every checklist item was filtered", judge.name)
        return None
    if dropped_items:
        rebuilt, rebuild_leaks = sanitize_authored_text(
            "\n".join(f"{i}. {entry['q']}" for i, entry in enumerate(checklist, start=1))
        )
        leaked_tokens.extend(rebuild_leaks)
        rubric_md = rebuilt
    # An item naming exactly one card output field gets a ``field`` ref, never
    # LLM-invented, so a failed verdict joins its schema_field graph node.
    # Items a deterministic check already owns are dropped.
    checklist, dropped = prepare_judge_checklist(checklist, grounding.codebase_card)
    if dropped and not checklist:
        _record_drop(
            drops,
            suite=suite,
            name=judge.name,
            stage="restatement",
            reason="every item restated a deterministic or reference-equality check",
        )
        return None
    incoming = checklist
    source = judge.grounding_citation or ""
    checklist, authored_mapping, surface_notes, drop_spec = enforce_surface_bindings(
        checklist=checklist,
        variable_mapping=[entry.model_dump() for entry in judge.variable_mapping],
        rubric_md=rubric_md,
        card=grounding.codebase_card,
        grades_live_surface=False,
        generate_observes_tools=generate_observes_tool_calls(grounding),
        sourced_claims=card_claims_for_source(source, grounding.codebase_card),
    )
    if drop_spec:
        _record_drop(
            drops,
            suite=suite,
            name=judge.name,
            stage="surface_misbinding",
            reason="; ".join(surface_notes),
        )
        return None
    if surface_notes and checklist and len(checklist) < len(incoming):
        rebuilt, rebuild_leaks = sanitize_authored_text(
            "\n".join(f"{i}. {entry['q']}" for i, entry in enumerate(checklist, start=1))
        )
        leaked_tokens.extend(rebuild_leaks)
        rubric_md = rebuilt
    path_probe = rubric_md + " " + " ".join(str(i.get("q") or "") for i in checklist)
    path_errors = schema_path_errors(path_probe, grounding.codebase_card)
    if path_errors:
        _record_drop(
            drops,
            suite=suite,
            name=judge.name,
            stage="schema_path",
            reason="; ".join(path_errors),
        )
        return None
    variable_mapping = align_variable_mapping(
        rubric_md,
        checklist,
        authored_mapping,
        seed_reference=True,
    )
    requires_reference = bool(judge.requires_reference)
    applicable_roles = [SUITE_GENERATIVE]
    config: dict[str, Any] = {}
    if leaked_tokens:
        config["_sanitized_tokens"] = sorted(set(leaked_tokens))
    all_dropped = dropped_items + list(dropped)
    if all_dropped:
        config["_dropped_items"] = all_dropped
    if surface_notes:
        config["_surface_repairs"] = surface_notes
    if judge.limitation.strip():
        config["limitation"] = judge.limitation.strip()[:300]
    if judge.gaming_path.strip():
        config["gaming_path"] = judge.gaming_path.strip()[:300]
    payload = {
        "name": (judge.name or "").strip(),
        "display_name": (judge.name or "").strip()[:255],
        "description": judge.description,
        "kind": "llm_judge",
        "scope": scope,
        "score_type": judge.score_type,
        "rubric_md": rubric_md,
        "checklist": checklist,
        "judge_model": judge.judge_model,
        "pass_threshold": judge.pass_threshold,
        "requires_reference": requires_reference,
        "applicable_roles": applicable_roles,
        "variable_mapping": variable_mapping,
        "config": config,
        "provenance": SpecProvenance(
            source=judge.grounding_citation or f"tier1_authoring:{suite}",
            data_version=grounding.data_version,
            codebase_commit=grounding.codebase_commit,
            generator=TIER1_GENERATOR,
            authoring_contract=AUTHORING_CONTRACT,
            surface_area=surface,
            authored_for_prompt=getattr(grounding, "prompt_id", "") or "",
        ),
    }
    try:
        return EvaluatorSpec.model_validate(payload)
    except ValidationError as exc:
        detail = "; ".join(e.get("msg", "") for e in exc.errors()[:3])
        _record_drop(drops, suite=suite, name=judge.name, stage="validation", reason=detail)
        return None


def _call_authoring_llm(prompt: str, *, system_prompt: str = _TIER1_SYSTEM) -> str:
    model = resolve_model(TaskType.EVAL_RECOMMENDATION)
    raw, _ = call_llm(
        prompt,
        system_prompt=system_prompt,
        response_format=_AuthoredJudgeSuite,
        model=model,
        fallback_models=model_chain(TaskType.EVAL_RECOMMENDATION),
        retry_deadline=RETRY_DEADLINE_INTERACTIVE,
        max_tokens=_TIER1_MAX_TOKENS,
        # Low reasoning effort halves latency on reasoning models without hurting quality.
        reasoning_effort="low",
        request_kwargs={"timeout": _TIER1_LLM_TIMEOUT_S},
    )
    return raw


def _parse_suite(
    raw: str, *, suite: str = "", drops: list[dict[str, Any]] | None = None
) -> _AuthoredJudgeSuite:
    """A max_tokens cut-off loses only the trailing entry after json_repair;
    per-judge validation salvages the rest and records a ``truncated`` drop."""
    try:
        return _AuthoredJudgeSuite.model_validate_json(raw)
    except ValidationError:
        repaired = try_json_parsing(raw)
        entries = repaired.get("evals", []) if isinstance(repaired, dict) else []
        judges: list[_AuthoredJudge] = []
        for entry in entries:
            try:
                judges.append(_AuthoredJudge.model_validate(entry))
            except ValidationError:
                continue
        logger.warning(
            "Tier 1 response was malformed/truncated; salvaged %d of %d judges",
            len(judges),
            len(entries),
        )
        _record_drop(
            drops,
            suite=suite,
            name="",
            stage="truncated",
            reason=f"response malformed/truncated; salvaged {len(judges)} of {len(entries)}",
        )
        return _AuthoredJudgeSuite(evals=judges)


def author_grounded_judges(
    grounding: EvalGroundingContext,
    existing_coverage: list[EvaluatorSpec],
    *,
    max_evals: int = _MAX_AUTHORED_JUDGES,
    time_budget_s: float = _TIER1_BUDGET_S,
    matrix_hint: Any = None,
    suite: str = SUITE_GENERATIVE,
    raise_on_timeout: bool = False,
    drops: list[dict[str, Any]] | None = None,
    allocation: SignalAllocation | None = None,
) -> list[EvaluatorSpec]:
    """``[]`` in every degraded case so Tier 0 stands alone; ``raise_on_timeout``
    lets the caller distinguish a timeout from an empty authoring."""
    pack = render_grounding_pack(grounding)
    if not pack:
        return []

    if allocation is None:
        allocation = allocate_signals(grounding, existing_coverage)
    prompt = _TIER1_TEMPLATE.format(
        grounding_pack=pack,
        example_unit=_render_example_unit_section(grounding),
        tier0_block=_coverage_block(existing_coverage),
        matrix_hint=_matrix_hint_block(matrix_hint),
        construct_block=_construct_block(allocation.construct),
        allocation_block=allocation.residual_block(suite),
        max_evals=max_evals,
        sources=", ".join(VARIABLE_SOURCES),
        areas=", ".join(SURFACE_AREAS),
    )
    # A hung provider must never stall the synchronous request past the budget.
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        raw = executor.submit(_call_authoring_llm, prompt, system_prompt=_TIER1_SYSTEM).result(
            timeout=time_budget_s
        )
        authored = _parse_suite(raw, suite=suite, drops=drops)
    except FutureTimeoutError:
        logger.warning(
            "Tier 1 %s judge authoring exceeded %.0fs budget (Tier 0 output stands)",
            suite,
            time_budget_s,
        )
        if raise_on_timeout:
            raise
        return []
    except Exception as exc:  # noqa: BLE001 — Tier 1 is additive on top of Tier 0
        logger.warning("Tier 1 %s judge authoring failed (Tier 0 output stands): %s", suite, exc)
        return []
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    taken = {spec.name for spec in existing_coverage}
    covered_fields = frozenset(
        name for spec in existing_coverage for name in covered_field_names(spec)
    )
    specs: list[EvaluatorSpec] = []
    for judge in authored.evals[:max_evals]:
        spec = _to_spec(judge, grounding, suite=suite, drops=drops, covered_fields=covered_fields)
        if spec is None:
            continue
        if spec.name in taken:
            _record_drop(
                drops,
                suite=suite,
                name=spec.name,
                stage="collision",
                reason="name collides with an existing spec in this suite",
            )
            continue
        # Identity is the cited cell, never the name.
        accepted, reason = allocation.claim(suite, spec)
        if not accepted:
            _record_drop(
                drops, suite=suite, name=spec.name, stage="duplicate_signal", reason=reason
            )
            continue
        # The spec validator strips a role the shape cannot support, and an empty
        # ``applicable_roles`` reads downstream as "infer from shape" — so a judge
        # that lost its only role is not roleless, it silently becomes generative.
        # A judge written for one surface is not a judge for the other: drop it.
        if suite not in spec.applicable_roles:
            logger.warning(
                "Tier 1 %s judge %r cannot serve its own suite (scope=%s, evidence=%s); dropped",
                suite,
                spec.name,
                spec.scope,
                spec.effective_evidence(),
            )
            continue
        overlap = overlapping_prior(spec, specs)
        if overlap is not None:
            logger.info(
                "Dropping overlapping judge %r (Jaccard %.2f with %r)",
                spec.name,
                overlap[1],
                overlap[0].name,
            )
            continue
        taken.add(spec.name)
        specs.append(spec)
    _bind_behaviour_provenance(specs, grounding)
    return specs


_TRAJECTORY_SOURCE_RE = re.compile(r"codebase_card\.trajectory_map\[(\w[\w-]*)\]")


def _bind_behaviour_provenance(specs: list[EvaluatorSpec], grounding: EvalGroundingContext) -> None:
    """A judge citing one mapped path is that behaviour's outcome judge."""
    paths = [
        p
        for p in (grounding.codebase_card or {}).get("trajectory_map") or []
        if isinstance(p, dict) and p.get("id")
    ]
    if not paths:
        return
    ids = {str(p["id"]) for p in paths}
    for spec in specs:
        if spec.config.get("behaviour"):
            continue
        match = _TRAJECTORY_SOURCE_RE.search(spec.provenance.source or "")
        if not match:
            continue
        ref = match.group(1)
        if ref.isdigit():
            index = int(ref)
            key = str(paths[index]["id"]) if index < len(paths) else ""
        else:
            key = ref if ref in ids else ""
        if key:
            spec.config["behaviour"] = {
                "behaviour_key": key,
                "role": "outcome",
                "anchor_segment": [],
            }


def _existing_tier1_judges(grounding: EvalGroundingContext) -> list[Any]:
    """Set membership matters as much as the capability FK: rows minted with
    ``capability=null`` live on only as eval-set members."""
    from django.db.models import Q  # noqa: PLC0415 — grouped with the model import below

    from overbae.models import Evaluator  # noqa: PLC0415 — avoid import cycle at module load

    capability = grounding.capability or getattr(grounding.dataset, "capability", None)
    if capability is None or getattr(capability, "pk", None) is None:
        return []
    return list(
        Evaluator.objects.filter(
            Q(capability=capability) | Q(set_memberships__eval_set__capability=capability),
            is_archived=False,
            kind="llm_judge",
            config__provenance__generator=TIER1_GENERATOR,
        ).distinct()
    )


def overlapping_prior(
    spec: EvaluatorSpec, kept: list[EvaluatorSpec]
) -> tuple[EvaluatorSpec, float] | None:
    questions = [item.q for item in spec.checklist]
    if not questions:
        return None
    for prior in kept:
        prior_qs = [item.q for item in prior.checklist]
        if not prior_qs:
            continue
        score = checklist_jaccard(questions, prior_qs)
        if score >= CHECKLIST_CLUSTER_JACCARD:
            return prior, score
    return None


def author_tier1_suites(
    grounding: EvalGroundingContext,
    existing_coverage: list[EvaluatorSpec],
    *,
    max_evals: int = _MAX_AUTHORED_JUDGES,
    time_budget_s: float = _TIER1_BUDGET_S,
    matrix_hint: Any = None,
    drops: list[dict[str, Any]] | None = None,
) -> tuple[list[EvaluatorSpec], list[str], dict[str, Any] | None]:
    """``(specs, suites_timed_out, allocation_report)``. Tier 1 authors the
    generative suite only; trace_scoring uses Tier 0 + behaviour judges."""
    pack = render_grounding_pack(grounding)
    if not pack:
        return [], [], None

    construct = resolve_construct(grounding)
    allocation = allocate_signals(
        grounding,
        existing_coverage,
        existing_judges=_existing_tier1_judges(grounding),
        construct=construct,
    )

    timed_out: list[str] = []
    try:
        specs = author_grounded_judges(
            grounding,
            existing_coverage,
            max_evals=max_evals,
            time_budget_s=time_budget_s,
            matrix_hint=matrix_hint,
            suite=SUITE_GENERATIVE,
            raise_on_timeout=True,
            drops=drops,
            allocation=allocation,
        )
    except FutureTimeoutError:
        logger.warning(
            "Tier 1 %s suite exceeded %.0fs budget (Tier 0 output stands)",
            SUITE_GENERATIVE,
            time_budget_s,
        )
        timed_out.append(SUITE_GENERATIVE)
        specs = []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tier 1 %s suite failed: %s", SUITE_GENERATIVE, exc)
        specs = []

    return specs, timed_out, allocation.report()
