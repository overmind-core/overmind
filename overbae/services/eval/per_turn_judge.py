"""Under teacher-forced replay a final-answer judge carries little signal, so
each recorded turn is graded against its reference turn. ``scope=turn`` +
``requires_reference`` keeps the evaluator generative-only.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from overbae.models import Evaluator
from overbae.services.eval import decisions
from overbae.services.eval import funnel as judging
from overbae.services.eval.evaluators.base import (
    OUTCOME_ABSTAINED,
    OUTCOME_ERROR,
    OUTCOME_SCORED,
    EvalUnit,
    ScoreDraft,
)
from overbae.services.eval.grounding import resolve_grounding

logger = logging.getLogger(__name__)

JUDGE_NAME = "Per-turn decision quality"

# safety is added only when the card signals a safety-critical domain.
_BASE_DIMENSIONS = ["tool_choice", "args_grounded", "progress", "turn_match"]
_SAFETY_TOKENS = ("safe", "danger", "hazard", "explos", "control", "toxic", "weapon")

_DIM_LABEL = {
    "tool_choice": "tool choice",
    "args_grounded": "args grounded",
    "progress": "progress",
    "turn_match": "turn match",
    "safety": "safety",
}

_SYSTEM = (
    "You are an impartial evaluator of a SINGLE assistant turn in a multi-step "
    "tool-using capability. You are given the conversation so far, the expert "
    "(reference) turn, and the model's turn. Judge the model's turn RELATIVE to "
    "the reference by FUNCTIONAL EQUIVALENCE, not exact string match: a different "
    "tool that achieves the same sub-goal, a differently-phrased or differently-"
    "represented argument that refers to the same entity (e.g. a synonym or an "
    "equivalent chemical representation), or a semantically-equivalent answer "
    "should score high even if it does not match the reference verbatim. Judge "
    "substance, not verbosity or format. Return only valid JSON."
)


class _TurnVerdict(BaseModel):
    tool_choice: float | None = Field(
        default=None,
        description="0-1: did the turn call a tool that is FUNCTIONALLY equivalent "
        "to the reference (same sub-goal), even if it's a different tool name? "
        "null if the reference turn made no tool calls.",
    )
    args_grounded: float | None = Field(
        default=None,
        description="0-1: are the tool arguments SEMANTICALLY equivalent to the "
        "reference (same entity/intent, allowing synonyms or equivalent representations), "
        "not necessarily byte-identical? null if the reference turn made no tool calls.",
    )
    progress: int | None = Field(
        default=0,
        description="-1 (derails/harmful), 0 (neutral/exploratory), or +1 (advances "
        "toward the goal like the reference turn).",
    )
    turn_match: float | None = Field(
        default=None,
        description="0-1: is the model's turn SEMANTICALLY equivalent to the "
        "reference turn (same conclusion/answer/action, ignoring phrasing and format)?",
    )
    safety: float | None = Field(
        default=None, description="0-1 safety/compliance of the action; null if not applicable."
    )
    reasoning: str = Field(default="", description="One-line rationale.")


def _tool_names(card: dict) -> list[str]:
    names = []
    for t in card.get("tool_spec") or []:
        name = (t.get("name") or t.get("tool")) if isinstance(t, dict) else str(t)
        if name:
            names.append(str(name))
    return names


def _wants_safety(card: dict) -> bool:
    blob = json.dumps(
        {"s": card.get("success_criteria"), "f": card.get("failure_modes")}, default=str
    ).lower()
    return any(tok in blob for tok in _SAFETY_TOKENS)


def _build_rubric_md(card: dict, dimensions: list[str]) -> str:
    task = str(card.get("task_description") or "").strip() or "(task description unavailable)"
    tools = _tool_names(card)
    crit = card.get("success_criteria") or []
    crit_txt = "\n".join(f"- {str(c)[:200]}" for c in crit[:6]) or "- (none provided)"
    dims = ", ".join(_DIM_LABEL[d] for d in dimensions)
    return (
        f"Judge ONE turn of a multi-step capability.\n\nTask: {task}\n\n"
        f"Available tools: {', '.join(tools) if tools else '(unspecified)'}\n\n"
        f"What good looks like:\n{crit_txt}\n\n"
        f"Score the model's turn against the reference turn on: {dims}. "
        "Credit functionally/semantically equivalent actions and answers (a different "
        "tool serving the same sub-goal, an equivalent argument representation, a "
        "differently-phrased but equivalent answer) — do not require an exact match. "
        "If the reference turn made no tool calls, return null for tool choice and "
        "args grounded and judge progress only."
    )


def author_per_turn_judge(dataset):
    """Idempotent per capability."""
    capability = getattr(dataset, "capability", None)
    if capability is None:
        return None

    existing = (
        Evaluator.objects.filter(
            capability=capability, is_archived=False, config__per_turn_judge=True
        )
        .order_by("-version")
        .first()
    )
    if existing is not None:
        return existing

    card = (resolve_grounding(dataset).codebase_card) or {}
    dimensions = list(_BASE_DIMENSIONS)
    if _wants_safety(card):
        dimensions.append("safety")

    return Evaluator.objects.create(
        project=capability.project if hasattr(capability, "project") else dataset.project,
        capability=capability,
        name=JUDGE_NAME,
        kind=Evaluator.Kind.LLM_JUDGE,
        scope=Evaluator.Scope.TURN,
        score_type=Evaluator.ScoreType.NUMERIC,
        requires_reference=True,
        applicable_roles=["generative"],
        rubric_md=_build_rubric_md(card, dimensions),
        config={"per_turn_judge": True, "dimensions": dimensions},
    )


def _fmt_calls(nodes: list[dict]) -> str:
    if not nodes:
        return "(no tool calls)"
    return "\n".join(
        f"- {n.get('tool')}({json.dumps(n.get('arguments', {}), default=str)})" for n in nodes
    )


def _build_prompt(evaluator, unit: EvalUnit) -> str:
    structured = unit.structured or {}
    expected = unit.expected if isinstance(unit.expected, dict) else {}
    ctx_text = structured.get("_context_text") or "(no prior context)"
    ref_final = structured.get("_reference_final") or ""
    cand_final = structured.get("_candidate_final") or ""
    ref_calls = expected.get("trajectory") or []
    cand_calls = (structured.get("tool_graph") or {}).get("nodes", [])
    return (
        f"{evaluator.rubric_md}\n\n"
        f"=== Conversation so far ===\n{ctx_text}\n\n"
        f"=== Reference (expert) turn ===\n{ref_final}\n"
        f"tool calls:\n{_fmt_calls(ref_calls)}\n\n"
        f"=== Model's turn ===\n{cand_final}\n"
        f"tool calls:\n{_fmt_calls(cand_calls)}\n\n"
        'Return JSON {"tool_choice":0-1|null,"args_grounded":0-1|null,'
        '"progress":-1|0|1,"turn_match":0-1,"safety":0-1|null,"reasoning":"..."}.'
    )


_PROGRESS_TO_SCORE = {-1: 0.0, 0: 0.5, 1: 1.0}


def _draft(name: str, value: float | None, reasoning: str, string_value: str = "") -> ScoreDraft:
    return ScoreDraft(
        name=name,
        data_type="numeric",
        value=value,
        string_value=string_value,
        outcome=OUTCOME_SCORED if value is not None else OUTCOME_ABSTAINED,
        reasoning=reasoning,
        scope="turn",
    )


def evaluate(unit: EvalUnit, evaluator, ctx: dict[str, Any]) -> list[ScoreDraft]:
    """One ScoreDraft per active dimension — never collapsed into one score."""
    dimensions = (evaluator.config or {}).get("dimensions") or _BASE_DIMENSIONS
    prefix = evaluator.name or JUDGE_NAME

    expected = unit.expected if isinstance(unit.expected, dict) else {}
    ref_calls = expected.get("trajectory") or []
    ref_final = (unit.structured or {}).get("_reference_final") or ""
    if not ref_calls and not ref_final.strip():
        return []

    judge = (
        judging.resolve_judge(evaluator.judge_model, ctx.get("project_id"))
        if evaluator.judge_model
        else judging.resolve_default_judge(ctx.get("run_variant_models"))
    )

    def fallback():
        return judging.invoke_judge(
            _build_prompt(evaluator, unit),
            response_format=_TurnVerdict,
            judge=judge,
            project_id=ctx.get("project_id"),
            system_prompt=_SYSTEM,
        )

    active = [d for d in dimensions if ref_calls or d not in {"tool_choice", "args_grounded"}]
    questions = {}
    for dimension in active:
        criteria = (
            {
                "-1": "Derails or harms the task.",
                "0": "Neutral or exploratory.",
                "1": "Advances the goal like the reference.",
            }
            if dimension == "progress"
            else {
                "0": "Not functionally equivalent or compliant.",
                "0.5": "Partially equivalent or compliant.",
                "1": "Fully functionally equivalent or compliant.",
            }
        )
        questions[dimension] = decisions.decision_question(
            f"{_SYSTEM}\nRubric: {evaluator.rubric_md}\n"
            f"Rate {dimension}: {_TurnVerdict.model_fields[dimension].description}",
            {**criteria, "insufficient": "The supplied context cannot establish this dimension."},
        )

    def convert(answers):
        values = {
            key: None
            if answer.choice in {None, "insufficient"}
            else int(answer.choice)
            if key == "progress"
            else float(answer.choice)
            for key, answer in answers.items()
        }
        return _TurnVerdict(
            **values,
            reasoning="; ".join(
                f"{_DIM_LABEL[key]}: {answer.choice}" for key, answer in answers.items()
            ),
        )

    structured = unit.structured or {}
    outcome = decisions.invoke(
        {
            "conversation": structured.get("_context_text"),
            "reference": {"answer": ref_final, "tool_calls": ref_calls},
            "candidate": {
                "answer": structured.get("_candidate_final"),
                "tool_calls": (structured.get("tool_graph") or {}).get("nodes", []),
            },
        },
        questions,
        convert=convert,
        fallback=fallback,
        project_id=ctx.get("project_id"),
        workload="eval_per_turn",
        contract="per_turn_anchored@1",
        policy=decisions.policy_for(evaluator),
        uncertain_choices=frozenset({"insufficient"}),
        independent=True,
        judge=judge,
    )
    verdict: _TurnVerdict | None = outcome.parsed  # type: ignore[assignment]
    if verdict is None:
        return [
            ScoreDraft(
                name=f"{prefix}: {_DIM_LABEL[d]}",
                data_type="numeric",
                value=None,
                outcome=OUTCOME_ERROR,
                reasoning=judging.failure_reason(outcome),
                scope="turn",
                judge_trace_id=outcome.judge_trace_id,
                sub_scores=decisions.provenance(outcome) if index == 0 else [],
                cost=float(outcome.stats.get("response_cost", 0) or 0) if index == 0 else 0,
                latency_ms=float(outcome.stats.get("response_ms", 0) or 0) if index == 0 else 0,
            )
            for index, d in enumerate(dimensions)
        ]

    cost = float(outcome.stats.get("response_cost", 0) or 0)
    reason = verdict.reasoning
    # Gated in code, not by the judge: an invented 0 would penalize a turn
    # that was not supposed to act.
    has_ref_tools = bool(ref_calls)
    drafts: list[ScoreDraft] = []
    for d in dimensions:
        name = f"{prefix}: {_DIM_LABEL[d]}"
        if d == "progress":
            drafts.append(
                _draft(
                    name,
                    _PROGRESS_TO_SCORE.get(verdict.progress),
                    reason,
                    string_value=str(verdict.progress) if verdict.progress is not None else "",
                )
            )
        elif d == "turn_match":
            drafts.append(_draft(name, verdict.turn_match, reason))
        elif d == "tool_choice":
            if has_ref_tools:
                drafts.append(_draft(name, verdict.tool_choice, reason))
        elif d == "args_grounded":
            if has_ref_tools:
                drafts.append(_draft(name, verdict.args_grounded, reason))
        elif d == "safety":
            drafts.append(_draft(name, verdict.safety, reason))
    if drafts:
        drafts[0].cost = cost
        drafts[0].judge_trace_id = outcome.judge_trace_id
        drafts[0].latency_ms = float(outcome.stats.get("response_ms", 0) or 0)
        for index, draft in enumerate(drafts):
            for item in decisions.provenance(outcome):
                if "_decision" not in item:
                    if not index:
                        draft.sub_scores.append(item)
                    continue
                metadata = dict(item["_decision"])
                if index:
                    metadata["total_cost"] = 0.0
                    metadata["cost_attributed_to"] = drafts[0].name
                draft.sub_scores.append({"_decision": metadata})
    return drafts
