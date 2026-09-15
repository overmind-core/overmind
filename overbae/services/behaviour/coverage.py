"""A behaviour's suite is the capability's evaluators whose ``config["behaviour"]``
binds to its key.
"""

from __future__ import annotations

from typing import Any

from overbae.models import Behaviour, Capability, Evaluator
from overbae.services.eval.card_compiler import judged_backbone_steps


def _segments(contract: dict[str, Any]) -> list[list[str]]:
    """Same cells the compiler mints step judges for — never consecutive
    anchor pairs, which invented coverage gaps the suite does not contain."""
    return [list(s.get("anchors") or []) for s in judged_backbone_steps(contract)]


def _bindings_by_key(capability_id: Any) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    rows = Evaluator.objects.filter(
        capability_id=capability_id, is_archived=False, config__has_key="behaviour"
    ).values_list("name", "config")
    for name, config in rows:
        binding = (config or {}).get("behaviour") or {}
        key = str(binding.get("behaviour_key") or "")
        if key:
            out.setdefault(key, []).append({"evaluator": name, **binding})
    return out


def behaviour_coverage(capability: Capability) -> list[dict[str, Any]]:
    bindings = _bindings_by_key(capability.id)
    out: list[dict[str, Any]] = []
    behaviours = Behaviour.objects.filter(
        capability=capability, status=Behaviour.Status.ACTIVE
    ).order_by("created_at")
    for behaviour in behaviours:
        version = behaviour.versions.order_by("-created_at").first()
        contract = version.contract if version else {}
        suite = bindings.get(behaviour.key, [])
        outcome_evals = [b["evaluator"] for b in suite if b.get("role") != "step"]
        steps = []
        for segment in _segments(contract):
            step_evals = [
                b["evaluator"]
                for b in suite
                if b.get("role") == "step" and list(b.get("anchor_segment") or []) == segment
            ]
            steps.append(
                {"segment": segment, "evaluators": step_evals, "covered": bool(step_evals)}
            )
        out.append(
            {
                "behaviour_id": str(behaviour.id),
                "outcome_evaluators": outcome_evals,
                "outcome_covered": bool(outcome_evals),
                "steps": steps,
            }
        )
    return out
