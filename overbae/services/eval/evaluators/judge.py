"""LLM-as-judge evaluator family. Long trajectories route through the cascade so
the judge never receives more than its context budget."""

from __future__ import annotations

import logging
from typing import Any

from overbae.services.eval import decisions, predicates
from overbae.services.eval import funnel as judging
from overbae.services.eval.evaluators.base import (
    OUTCOME_ABSTAINED,
    OUTCOME_ERROR,
    OUTCOME_NOT_APPLICABLE,
    OUTCOME_SCORED,
    EvalUnit,
    JudgeResult,
    ResolvedVariable,
    ScoreDraft,
    default_variable_mapping,
    insufficient_evidence_reason,
    map_choice,
    mark_low_provenance,
    normalize_numeric,
    resolve_one,
    resolve_variables_detailed,
    with_resolution,
)
from overbae.services.eval.rubric_compiler import build_judge_prompt, numeric_anchor_scale
from overbae.services.eval.span_evidence import render_span_tree

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = (
    "You are an impartial evaluation judge. Follow the checklist exactly, "
    "reason briefly per item, and return only valid JSON matching the schema. "
    "Judge substance, not verbosity or position. Score the delivered result "
    "against the rubric — one holistic judgment, not a mean of tool-payload "
    "fullness. If this is not a pass, set root_cause and root_cause_reason to "
    "the single miss; leave both empty on a pass. If the evidence contains "
    "nothing this rubric could grade in either direction — the behavior under "
    "evaluation never occurred at all — set abstained=true instead of awarding "
    "a vacuous score."
)

# The model's behavior, as opposed to context (input) or ground truth (reference).
_EVIDENCE_SOURCES = frozenset(
    {
        "output",
        "last_assistant_output",
        "trajectory",
        "messages",
        "conversation",
        "tool_calls",
        "tool_definitions",
        "final_output",
        "structured",
        "step",
    }
)
_BLANK_RESOLUTIONS = frozenset({"", "[]", "{}", "null", "none", '""'})


def _is_blank_resolution(value: str) -> bool:
    return (value or "").strip().lower() in _BLANK_RESOLUTIONS


def _explicit_rejection(unit: EvalUnit) -> bool:
    """An empty container / null is a rejection DECISION, not missing evidence;
    a flatly blank output stays low-provenance."""
    text = str((unit.trajectory or {}).get("final_output") or "").strip().lower()
    return text in ("[]", "{}", "null", "none")


def _unit_output_payloads_empty(unit: EvalUnit) -> bool:
    """The span tree still renders structure for such a unit, so the
    variable-resolution check never fires; payloads are what a grade needs."""
    trajectory = unit.trajectory or {}
    if str(trajectory.get("final_output") or "").strip():
        return False
    for message in trajectory.get("messages") or []:
        if message.get("role") != "assistant":
            continue
        if str(message.get("content") or "").strip():
            return False
        for call in message.get("tool_calls") or []:
            if call.get("arguments") not in (None, "", {}, []):
                return False
    stack = list(trajectory.get("span_tree") or [])
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("outputs") not in (None, "", {}, []):
            return False
        stack.extend(node.get("children") or [])
    return True


_MISSING_PAYLOAD_REASON = (
    "Abstained: this step's own spans record no output payload. Looked for a "
    "final output, assistant message text, tool-call arguments, and span "
    "output attributes (overmind.output.*) across the unit's span tree — all "
    "resolved empty. Structure alone cannot ground a quality grade, so no "
    "score is minted from this evidence (instrumentation gap, e.g. "
    'capture="none", or a no-LLM phase).'
)


def _evidence_resolved_empty(
    mapping: list[dict[str, Any]], resolved: dict[str, ResolvedVariable]
) -> bool:
    values = [
        resolved[e["var"]].value
        for e in mapping
        if (e.get("source") or "output") in _EVIDENCE_SOURCES and e.get("var") in resolved
    ]
    return bool(values) and all(_is_blank_resolution(v) for v in values)


def evaluate(unit: EvalUnit, evaluator, ctx: dict[str, Any]) -> list[ScoreDraft]:
    """Fit is decided at setup; at grade time this always scores, stamping
    low-provenance when the evidence resolved to empty."""
    from overbae.services.eval import cascade  # local import to avoid cycle

    mapping = evaluator.variable_mapping or default_variable_mapping()
    resolved = resolve_variables_detailed(unit, mapping)

    # Before cascade routing so escalated paths inherit it. An explicit
    # rejection IS evidence, judged against the intent, not low-provenance.
    low_provenance = insufficient_evidence_reason(unit, evaluator, mapping, resolved)
    rejection_evidence = _explicit_rejection(unit)
    if rejection_evidence:
        low_provenance = None

    # A step judge on payload-less evidence abstains in code, never in prose:
    # a judge improvises anything from 0.5 to 1.0 on it.
    behaviour_role = ((getattr(evaluator, "config", None) or {}).get("behaviour") or {}).get("role")
    if behaviour_role == "step" and not rejection_evidence and _unit_output_payloads_empty(unit):
        return [
            ScoreDraft(
                name=evaluator.name,
                data_type=evaluator.score_type,
                value=None,
                outcome=OUTCOME_ABSTAINED,
                reasoning=_MISSING_PAYLOAD_REASON,
                scope=evaluator.scope,
            )
        ]

    # Outcome/step judges are one concern; envelope expectations riding along
    # would mix a second concern into the score.
    trajectory = unit.trajectory or {}
    runtime = trajectory.get("runtime") or {}
    checklist = evaluator.checklist or []
    applicable, excluded = predicates.filter_checklist(checklist, runtime, trajectory)
    na_subs = predicates.not_applicable_sub_verdicts(excluded)
    intent_only = _intent_behaviour_role(evaluator)
    if intent_only:
        det_subs, synthetic_items, det_gated_fail = [], [], False
        prompt_runtime = {k: v for k, v in runtime.items() if k != "expectations"} or None
    else:
        det_subs, synthetic_items, det_gated_fail = predicates.expectation_prepass(
            runtime.get("expectations") or [], trajectory.get("final_output") or ""
        )
        prompt_runtime = runtime or None
    effective_checklist = [*applicable, *synthetic_items]

    if checklist and not applicable:
        # Synthetic runtime constraints are not judged alone: an orphan call
        # would duplicate (and maybe contradict) other evaluators' verdicts.
        return [
            ScoreDraft(
                name=evaluator.name,
                data_type=evaluator.score_type,
                value=None,
                outcome=OUTCOME_NOT_APPLICABLE,
                reasoning="All checklist items were excluded by applies_when predicates.",
                scope=evaluator.scope,
                sub_scores=[*det_subs, *na_subs],
            )
        ]

    # With output evidence empty, declared expectations decide: no
    # deterministic failure is a legitimate refusal, any failure is a broken
    # contract. A judge on emptiness only manufactures contradictory scores.
    declared = (
        []
        if intent_only
        else [e for e in runtime.get("expectations") or [] if e.get("scope") != "conversation"]
    )
    det_failed_ids = [str(s.get("id")) for s in det_subs if s.get("verdict") is False]
    if declared and _evidence_resolved_empty(mapping, resolved):
        if not det_failed_ids:
            declared_ids = ", ".join(str(e.get("id")) for e in declared)
            return [
                ScoreDraft(
                    name=evaluator.name,
                    data_type=evaluator.score_type,
                    value=None,
                    outcome=OUTCOME_NOT_APPLICABLE,
                    reasoning=(
                        "Output evidence resolved empty while the runtime envelope "
                        f"declares expectation(s) [{declared_ids}] with no deterministic "
                        "failure — emptiness is declared behavior, so there is nothing "
                        "for this evaluator to grade."
                    ),
                    scope=evaluator.scope,
                    sub_scores=[*det_subs, *na_subs, {"_runtime": {"envelope_present": True}}],
                )
            ]
        value, passed = normalize_numeric(evaluator.score_min, evaluator)
        return [
            ScoreDraft(
                name=evaluator.name,
                data_type=evaluator.score_type,
                value=value,
                passed=passed,
                outcome=OUTCOME_SCORED,
                reasoning=(
                    "Runtime contract failure: output evidence resolved empty and "
                    f"declared expectation(s) [{', '.join(det_failed_ids)}] failed "
                    "deterministically. The declaration decides this verdict; no "
                    "judge was invoked on the empty evidence."
                ),
                scope=evaluator.scope,
                sub_scores=[
                    *det_subs,
                    *na_subs,
                    {"_runtime_gate": {"gated_fail": det_gated_fail}},
                    {"_runtime": {"envelope_present": True}},
                ],
            )
        ]

    routed = cascade.maybe_route(unit, evaluator, ctx)
    if routed is not None:
        drafts = routed
    else:
        variables = {var: rv.value for var, rv in resolved.items()}
        if rejection_evidence:
            evidence_vars = {
                e["var"]
                for e in mapping
                if (e.get("source") or "output") in _EVIDENCE_SOURCES and e.get("var")
            }
            for var in evidence_vars & variables.keys():
                if _is_blank_resolution(variables[var]):
                    variables[var] = (
                        f"{variables[var].strip() or '[]'} — explicit empty output: the "
                        "agent's rejection decision (it deliberately emitted no records "
                        "for this input); grade whether rejecting served the user intent."
                    )
        prompt = build_judge_prompt(
            evaluator,
            variables,
            checklist=effective_checklist,
            runtime=prompt_runtime,
            grounding=ctx.get("grounding"),
            span_tree=render_span_tree((unit.trajectory or {}).get("span_tree")),
        )
        project_id = ctx.get("project_id")
        judge = (
            None
            if evaluator.judge_model
            else judging.resolve_default_judge(ctx.get("run_variant_models"))
        )

        def fallback():
            return judging.invoke_judge(
                prompt,
                response_format=JudgeResult,
                judge_model=evaluator.judge_model,
                judge=judge,
                project_id=project_id,
                system_prompt=JUDGE_SYSTEM_PROMPT,
            )

        if (
            behaviour_role == "step"
            and evaluator.score_type == "numeric"
            and evaluator.score_min == 0
            and evaluator.score_max == 1
        ):
            questions = {
                f"item_{index}": decisions.decision_question(
                    f"Rubric: {evaluator.rubric_md}\nCriterion: {item.get('q') or ''}"
                )
                for index, item in enumerate(effective_checklist)
            }
            questions["quality"] = decisions.decision_question(
                f"Rate only the named behaviour step using this rubric: {evaluator.rubric_md}\n"
                f"{numeric_anchor_scale(evaluator)}",
                {
                    "0.3": "Major quality failure under the rubric.",
                    "0.6": "Partial success with material quality problems.",
                    "0.9": "The step served its intended purpose with only minor imperfections.",
                    "1.0": "The step fully satisfied its intended purpose and quality requirements.",
                    "insufficient": "The evidence cannot establish step quality.",
                },
            )

            def convert(answers):
                return JudgeResult(
                    items=[
                        {
                            "id": item["id"],
                            "verdict": True,
                            "reasoning": f"Criterion {item['id']}: pass.",
                        }
                        for item in effective_checklist
                    ],
                    gates=[
                        {"id": item["id"], "passed": True}
                        for item in effective_checklist
                        if item.get("gate")
                    ],
                    score=float(answers["quality"].choice),
                    reasoning="The step satisfied the applicable criteria.",
                )

            # Failed steps need causal explanations; Jev only owns the bounded success path.
            outcome = decisions.invoke(
                {
                    "bound_evidence": variables,
                    "runtime": prompt_runtime,
                    "grounding": ctx.get("grounding"),
                },
                questions,
                convert=convert,
                fallback=fallback,
                project_id=project_id,
                workload="trace_behaviour_step",
                contract="behaviour_step_anchored@1",
                policy=decisions.policy_for(evaluator),
                uncertain_choices=frozenset({"fail", "insufficient", "0.3", "0.6"}),
            )
        else:
            outcome = fallback()
        draft = with_resolution(
            _draft_from_outcome(outcome, evaluator, checklist=effective_checklist), resolved
        )
        draft.sub_scores.extend(decisions.provenance(outcome))
        drafts = [draft]

    if intent_only:
        if na_subs:
            for d in drafts:
                d.sub_scores = [*d.sub_scores, *na_subs]
    elif runtime or det_subs or na_subs:
        drafts = [
            _apply_runtime_results(
                d, evaluator, det_subs, na_subs, det_gated_fail, envelope_present=bool(runtime)
            )
            for d in drafts
        ]
    if low_provenance:
        drafts = [mark_low_provenance(d, low_provenance) for d in drafts]
    return drafts


def _apply_runtime_results(
    draft: ScoreDraft,
    evaluator,
    det_subs: list[dict[str, Any]],
    na_subs: list[dict[str, Any]],
    det_gated_fail: bool,
    *,
    envelope_present: bool,
) -> ScoreDraft:
    """A failed gated expectation caps a boolean score like a failed gated item."""
    draft.sub_scores = [*draft.sub_scores, *det_subs, *na_subs]
    if det_gated_fail and evaluator.score_type == "boolean" and draft.value is not None:
        value, passed = normalize_numeric(evaluator.score_min, evaluator)
        draft.value = value
        draft.passed = passed
        draft.sub_scores = [*draft.sub_scores, {"_runtime_gate": {"gated_fail": True}}]
    # Segments aggregates by instrumentation so mixed populations stay comparable.
    draft.sub_scores = [*draft.sub_scores, {"_runtime": {"envelope_present": envelope_present}}]
    return draft


def _draft_from_outcome(
    outcome: judging.JudgeOutcome, evaluator, checklist: list[dict[str, Any]] | None = None
) -> ScoreDraft:
    if checklist is None:
        checklist = evaluator.checklist or []
    result: JudgeResult | None = outcome.parsed  # type: ignore[assignment]
    cost = float(outcome.stats.get("response_cost", 0) or 0)
    latency = float(outcome.stats.get("response_ms", 0) or 0)
    if result is None:
        return ScoreDraft(
            name=evaluator.name,
            data_type=evaluator.score_type,
            value=None,
            outcome=OUTCOME_ERROR,
            reasoning=judging.failure_reason(outcome),
            scope=evaluator.scope,
            judge_trace_id=outcome.judge_trace_id,
            cost=cost,
            latency_ms=latency,
        )

    # No score, so vacuous satisfaction never inflates composites.
    if result.abstained:
        return ScoreDraft(
            name=evaluator.name,
            data_type=evaluator.score_type,
            value=None,
            outcome=OUTCOME_ABSTAINED,
            reasoning=result.reasoning or "Abstained: no evidence in the trace for this signal.",
            scope=evaluator.scope,
            judge_trace_id=outcome.judge_trace_id,
            cost=cost,
            latency_ms=latency,
        )

    # Cached results may have filled both; an absent-action pass must not leak
    # into gates or sub-verdicts.
    for item in result.items:
        if item.not_applicable:
            item.verdict = None
            item.score = None

    # Fields come from config so the LLM never invents field names.
    checklist_fields = {
        str(item.get("id")): str(item.get("field") or "").strip()
        for item in checklist
        if isinstance(item, dict) and str(item.get("field") or "").strip()
    }
    sub_scores = [
        {
            "id": i.id,
            "verdict": i.verdict,
            "score": i.score,
            "reasoning": i.reasoning,
            **({"outcome": "not_applicable"} if i.not_applicable else {}),
            **({"field": checklist_fields[i.id]} if i.id in checklist_fields else {}),
        }
        for i in result.items
    ]

    # Whatever overall score the judge emitted, no applicable item means no evidence.
    checklist_ids = {
        str(item.get("id"))
        for item in checklist
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    na_ids = {i.id for i in result.items if i.not_applicable}
    if checklist_ids and checklist_ids <= na_ids:
        return ScoreDraft(
            name=evaluator.name,
            data_type=evaluator.score_type,
            value=None,
            outcome=OUTCOME_NOT_APPLICABLE,
            reasoning=(
                "Every checklist item concerns an action type absent from this "
                "unit's evidence; nothing applies, so no score is minted."
            ),
            sub_scores=sub_scores,
            scope=evaluator.scope,
            judge_trace_id=outcome.judge_trace_id,
            cost=cost,
            latency_ms=latency,
        )
    # Boolean and outcome-role gates cap the score (the miss IS the score).
    # Step-role gates only set ``passed`` — quality stays graded.
    role = ((getattr(evaluator, "config", None) or {}).get("behaviour") or {}).get("role")
    cap_on_gate = evaluator.score_type == "boolean" or role == "outcome"
    gated_fail = cap_on_gate and _gate_failed(result, checklist)

    if evaluator.score_type == "categorical":
        value, passed = map_choice(result.label, evaluator)
        return _stamp_root_cause(
            ScoreDraft(
                name=evaluator.name,
                data_type="categorical",
                value=value,
                # An unmappable label is a contract failure, counted not hidden.
                outcome=OUTCOME_ERROR if value is None else OUTCOME_SCORED,
                string_value=result.label,
                passed=passed,
                reasoning=result.reasoning,
                sub_scores=sub_scores,
                scope=evaluator.scope,
                judge_trace_id=outcome.judge_trace_id,
                cost=cost,
                latency_ms=latency,
            ),
            result,
        )

    # ``result.delivery``/``clears_outstanding`` stay parseable for cached
    # results but are not consumed: delivery is a ledger transition.
    raw = evaluator.score_min if gated_fail else result.score
    value, passed = normalize_numeric(raw, evaluator)
    if evaluator.score_type != "boolean":
        gate_passed = _gate_passed(result, checklist)
        if gate_passed is not None:
            passed = gate_passed
    data_type = "boolean" if evaluator.score_type == "boolean" else "numeric"
    # Only boolean evaluators are hard gates in the composite; the outcome cap
    # is this draft alone.
    is_boolean = evaluator.score_type == "boolean"
    sub_scores = sub_scores + [
        {
            "_threshold": {
                "pass_threshold": evaluator.pass_threshold if is_boolean else None,
                "gated_fail": is_boolean and gated_fail,
            }
        }
    ]
    return _stamp_root_cause(
        ScoreDraft(
            name=evaluator.name,
            data_type=data_type,
            value=value,
            passed=passed,
            reasoning=result.reasoning,
            sub_scores=sub_scores,
            scope=evaluator.scope,
            judge_trace_id=outcome.judge_trace_id,
            cost=cost,
            latency_ms=latency,
        ),
        result,
    )


def _stamp_root_cause(draft: ScoreDraft, result: JudgeResult) -> ScoreDraft:
    cause = (result.root_cause or "").strip()
    if not cause:
        return draft
    draft.sub_scores = [
        *draft.sub_scores,
        {
            "id": cause,
            "tool": cause,
            "failure_role": "root_cause",
            "reasoning": (result.root_cause_reason or "").strip(),
        },
    ]
    draft.failure_role = "root_cause"
    return draft


def _intent_behaviour_role(evaluator) -> bool:
    role = ((getattr(evaluator, "config", None) or {}).get("behaviour") or {}).get("role")
    return role in ("outcome", "step")


def _gate_failed(result: JudgeResult, checklist: list[dict[str, Any]]) -> bool:
    return _gate_passed(result, checklist) is False


def _gate_passed(result: JudgeResult, checklist: list[dict[str, Any]]) -> bool | None:
    """Structured ``gates`` win; item verdicts fill ids left out (cached pre-gates results)."""
    gate_ids = {item.get("id") for item in checklist if item.get("gate")}
    if not gate_ids:
        return None
    by_id = {item.id: item.verdict for item in result.items if item.id in gate_ids}
    for gate in getattr(result, "gates", None) or []:
        if gate.id in gate_ids and gate.passed is not None:
            by_id[gate.id] = gate.passed
    if any(by_id.get(gid) is False for gid in gate_ids):
        return False
    if all(by_id.get(gid) is True for gid in gate_ids):
        return True
    return None


def emit_prediction(unit: EvalUnit, evaluator) -> list[ScoreDraft]:
    """The task aggregation pass computes the dataset-level metric over these."""
    config = getattr(evaluator, "config", None) or {}
    prediction_rv = resolve_one(
        unit,
        config.get("prediction_field") or "output",
        source=None if config.get("prediction_field") else "output",
        jsonpath=config.get("prediction_jsonpath"),
    )
    reference_rv = resolve_one(
        unit,
        config.get("reference_field") or "reference",
        source=None if config.get("reference_field") else "reference",
        jsonpath=config.get("reference_jsonpath"),
    )
    prediction = prediction_rv.value
    reference = reference_rv.value
    return [
        ScoreDraft(
            name=f"{evaluator.name}__prediction",
            data_type="categorical",
            string_value=str(prediction)[:256],
            reasoning="",
            scope="sample",
            # The aggregation pass reads ``sub_scores[0]``.
            sub_scores=[
                {"prediction": prediction, "reference": reference},
                {
                    "_resolution": {
                        "prediction": {
                            "strategy": prediction_rv.strategy,
                            "shape": prediction_rv.shape,
                        },
                        "reference": {
                            "strategy": reference_rv.strategy,
                            "shape": reference_rv.shape,
                        },
                    }
                },
            ],
        )
    ]
