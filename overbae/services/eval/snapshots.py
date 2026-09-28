"""Grading config frozen onto ``RunEvaluator`` so a run stays reproducible
after library edits or deletes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from overbae.services.eval.decisions import freeze_config
from overbae.services.eval.evidence import infer_evidence_requirement
from overbae.services.eval.judge_selection import uses_generative_judge

# ``description`` is display-only; frozen so the run view never re-fetches the library row.
SNAPSHOT_FIELDS = (
    "name",
    "display_name",
    "version",
    "description",
    "kind",
    "scope",
    "rubric_md",
    "checklist",
    "judge_model",
    "score_type",
    "score_min",
    "score_max",
    "choices",
    "pass_threshold",
    "requires_reference",
    "evidence_requirement",
    "variable_mapping",
    "config",
)


class UngradableEvaluatorError(ValueError):
    """An evaluator was attached to a generative run in a state that cannot
    produce a meaningful score."""


def build_snapshot(evaluator, *, judge_model: str = "") -> dict[str, Any]:
    """Every generative attach path funnels through here, so this is where an
    evaluator that cannot produce a meaningful score is refused. Failing at
    attach is deliberate: a run whose judge grades invented items, or answers
    questions about evidence it was never given, yields numbers that aren't
    comparable to each other or to anything else."""
    if evaluator.requires_checklist() and not (evaluator.checklist or []):
        raise UngradableEvaluatorError(
            f"Evaluator '{evaluator.name}' is an LLM judge with no compiled checklist. "
            "Compile its rubric into checklist items before running it."
        )
    unbound = evaluator.unbound_checklist_variables()
    if unbound:
        raise UngradableEvaluatorError(
            f"Evaluator '{evaluator.name}' has checklist items referencing "
            f"{', '.join('{{' + v + '}}' for v in unbound)}, which nothing binds. "
            "Add the variable to its mapping, or rewrite the items against the "
            "variables it does bind."
        )
    snap = {f: getattr(evaluator, f, None) for f in SNAPSHOT_FIELDS}
    if evaluator.kind in {"llm_judge", "agentic", "trajectory"}:
        snap["config"] = freeze_config(snap.get("config"))
    snap["evaluator_id"] = str(getattr(evaluator, "id", "") or "")
    if judge_model and uses_generative_judge(evaluator):
        snap["judge_model"] = judge_model
    return snap


def snapshot_to_obj(snapshot: dict[str, Any], *, scope_override: str = "") -> SimpleNamespace:
    data = dict(snapshot or {})
    # Partial snapshots still need every key.
    data.setdefault("name", "evaluator")
    data.setdefault("kind", "llm_judge")
    data.setdefault("scope", "final_output")
    data.setdefault("config", {})
    data.setdefault("score_min", 0.0)
    data.setdefault("score_max", 1.0)
    data.setdefault("choices", [])
    data.setdefault("checklist", [])
    data.setdefault("variable_mapping", [])
    data.setdefault("score_type", "numeric")
    data.setdefault("rubric_md", "")
    data.setdefault("judge_model", "")
    data.setdefault("pass_threshold", None)
    data.setdefault("requires_reference", False)
    # Snapshots frozen before this field existed infer it from their shape.
    if not data.get("evidence_requirement"):
        data["evidence_requirement"] = infer_evidence_requirement(
            kind=data.get("kind", "llm_judge"),
            scope=data.get("scope", "final_output"),
            variable_mapping=data.get("variable_mapping", []),
            requires_reference=data.get("requires_reference", False),
            config=data.get("config", {}),
        )
    if scope_override:
        data["scope"] = scope_override
    return SimpleNamespace(**data)
