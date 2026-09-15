"""The composite is the OUTCOME-role verdict dragged down only by STEP-role
verdicts the judge failed; evidence-role members land in ``step_results`` but
never weight the score, and neither does route-vs-contract alignment.
"""

from __future__ import annotations

import logging
from typing import Any

from overbae.models import Evaluator, TaskExecution
from overbae.services.behaviour import ledger
from overbae.services.behaviour.binder import anchor_matches
from overbae.services.behaviour.session_score import refresh_session_score
from overbae.services.eval.composition import compose

logger = logging.getLogger(__name__)

FLAG_UNUSUAL_ROUTE_GOOD_OUTCOME = "unusual_route_good_outcome"
FLAG_USUAL_ROUTE_BAD_OUTCOME = "usual_route_bad_outcome"

_GOOD_SCORE = 0.7
_BAD_SCORE = 0.5


def behaviour_binding(evaluator: Evaluator) -> dict[str, Any]:
    binding = (evaluator.config or {}).get("behaviour")
    return binding if isinstance(binding, dict) else {}


def segment_ran(segment: Any, observed: list[str]) -> bool:
    """``[a, b]`` ran when its anchors appear in order (b alone counts when a is
    the entry that emitted no span); suffix-tolerant so module-prefix drift never
    silences a step judge. Empty segment ⇒ always applicable."""
    anchors = [str(a) for a in segment or [] if str(a or "").strip()]
    if not anchors:
        return True
    cursor = 0
    for anchor in anchors:
        idx = next(
            (i for i in range(cursor, len(observed)) if anchor_matches(observed[i], anchor)),
            None,
        )
        if idx is None:
            return False
        cursor = idx
    return True


def route_evidence(observed_route: dict[str, Any] | None) -> list[str]:
    """Ancestor chain first (a turn unit runs INSIDE its enclosing pipeline steps)."""
    route = observed_route or {}
    return [*(route.get("ancestors") or []), *(route.get("anchors") or [])]


def behaviour_skip_reason(member, execution: TaskExecution | None) -> str:
    """Persisted on the skip verdict so a mis-skip is diagnosable from the row alone."""
    binding = behaviour_binding(member.evaluator)
    member_key = str(binding.get("behaviour_key") or "")
    if execution is None or not execution.behaviour_id:
        return (
            f"Skipped: member is bound to behaviour '{member_key}' but this "
            "execution is not bound to any behaviour."
        )
    bound_key = execution.behaviour.key
    if member_key != bound_key:
        return (
            f"Skipped: member is bound to behaviour '{member_key}'; this "
            f"execution is bound to '{bound_key}' "
            f"(binding_source={execution.binding_source})."
        )
    return (
        f"Skipped: step anchor segment {binding.get('anchor_segment') or []} "
        "did not run in this unit's observed route."
    )


def filter_members_for_execution(members: list, execution: TaskExecution | None) -> list:
    """Behaviour-bound members run only for their bound behaviour; step members
    only when their anchor segment actually ran."""
    if execution is None:
        return [m for m in members if not behaviour_binding(m.evaluator)]
    observed = route_evidence(execution.observed_route)
    key = execution.behaviour.key if execution.behaviour_id else None
    kept = []
    for member in members:
        binding = behaviour_binding(member.evaluator)
        if not binding:
            kept.append(member)
            continue
        if key is None or binding.get("behaviour_key") != key:
            continue
        if binding.get("role") == "step" and not segment_ran(
            binding.get("anchor_segment"), observed
        ):
            continue
        kept.append(member)
    return kept


def _route_alignment(observed_route: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    observed = list(observed_route.get("anchors") or [])
    expected = list(contract.get("anchor_sequence") or [])
    if not expected:
        return {"completion": None, "order_ok": None, "terminal_match": None}
    hit = [a for a in expected if any(anchor_matches(q, a) for q in observed)]
    completion = len(hit) / len(expected)
    order_ok = segment_ran(hit, observed)
    expected_terminal = str((contract.get("terminal") or {}).get("kind") or "")
    observed_terminal = str(observed_route.get("terminal") or "")
    terminal_match = bool(expected_terminal) and expected_terminal == observed_terminal
    return {
        "completion": round(completion, 4),
        "order_ok": order_ok,
        "terminal_match": terminal_match,
    }


def score_execution(execution: TaskExecution, scored_block: dict[str, Any]) -> TaskExecution:
    """Deterministic over the span's ``trace_scoring`` block, so re-runs converge."""
    bindings: dict[str, dict[str, Any]] = {}
    display_names: dict[str, str] = {}
    if execution.behaviour_id:
        for evaluator in Evaluator.objects.filter(
            capability_id=execution.capability_id, is_archived=False, config__has_key="behaviour"
        ):
            binding = behaviour_binding(evaluator)
            if binding.get("behaviour_key") == execution.behaviour.key:
                bindings[evaluator.name] = binding
                display_names[evaluator.name] = evaluator.display_name or ""

    observed = route_evidence(execution.observed_route)
    step_results: list[dict[str, Any]] = []
    for name, binding in bindings.items():
        segment = binding.get("anchor_segment") or []
        role = str(binding.get("role") or "outcome")
        display = display_names.get(name, "")
        if role == "step" and not segment_ran(segment, observed):
            step_results.append(
                {
                    "evaluator": name,
                    "display_name": display,
                    "role": role,
                    "segment": segment,
                    "outcome": "segment_not_run",
                }
            )
            continue
        entry = scored_block.get(name)
        if not isinstance(entry, dict):
            step_results.append(
                {
                    "evaluator": name,
                    "display_name": display,
                    "role": role,
                    "segment": segment,
                    "outcome": "unscored",
                }
            )
            continue
        result = {
            "evaluator": name,
            "display_name": display,
            "role": role,
            "segment": segment,
            "score": entry.get("score"),
            "passed": entry.get("passed"),
            "outcome": entry.get("outcome"),
            "rationale": (entry.get("rationale") or "")[:500],
        }
        if role == "outcome":
            # Descriptive only: a ledger note never rewrites the judge's verdict,
            # or the step row and the composite tell two different stories.
            verdict = ledger.outcome_ledger_verdict(execution)
            result["delivery"] = verdict["delivery"]
            if verdict["gate_note"]:
                result["ledger_note"] = verdict["gate_note"]
        step_results.append(result)

    # The claim-typed composition already applied cap and grounding semantics;
    # behaviour step results stay descriptive.
    evidence_names = {
        name for name, binding in bindings.items() if str(binding.get("role") or "") == "evidence"
    }
    composable = {k: v for k, v in scored_block.items() if k not in evidence_names}
    composed = composable.get("_execution")
    if not isinstance(composed, dict) or not isinstance(composed.get("score"), (int, float)):
        composed = compose(composable) or {}
    success_score = (
        float(composed["score"]) if isinstance(composed.get("score"), (int, float)) else None
    )

    # A safety cap can drag the composite down from a verdict no behaviour
    # binding surfaces; append it so the UI shows WHY the turn reads low.
    zeroing = [str(composed["cap_evaluator"])] if composed.get("cap_evaluator") else []
    for name in dict.fromkeys(zeroing):
        entry = scored_block.get(name)
        if not isinstance(entry, dict) or any(r.get("evaluator") == name for r in step_results):
            continue
        step_results.append(
            {
                "evaluator": name,
                "display_name": display_names.get(name) or name,
                "role": "failure",
                "segment": [],
                "score": entry.get("score"),
                "passed": False,
                "outcome": entry.get("outcome"),
                "rationale": (entry.get("rationale") or "")[:500],
            }
        )

    flags = [
        f
        for f in execution.route_flags or []
        if f not in (FLAG_UNUSUAL_ROUTE_GOOD_OUTCOME, FLAG_USUAL_ROUTE_BAD_OUTCOME)
    ]
    observed_route = dict(execution.observed_route or {})
    if execution.behaviour_version_id:
        alignment = _route_alignment(observed_route, execution.behaviour_version.contract or {})
        observed_route["alignment"] = alignment
        conforms = alignment.get("completion") == 1.0 and alignment.get("order_ok")
        if isinstance(success_score, (int, float)):
            if not conforms and success_score >= _GOOD_SCORE:
                flags.append(FLAG_UNUSUAL_ROUTE_GOOD_OUTCOME)
            if conforms and success_score < _BAD_SCORE:
                flags.append(FLAG_USUAL_ROUTE_BAD_OUTCOME)

    execution.step_results = step_results
    execution.success_score = success_score
    execution.route_flags = flags
    execution.observed_route = observed_route
    execution.save(
        update_fields=[
            "step_results",
            "success_score",
            "route_flags",
            "observed_route",
            "updated_at",
        ]
    )
    if execution.conversation_id:
        refresh_session_score(execution)
    return execution
