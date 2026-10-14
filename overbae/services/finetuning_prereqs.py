"""Finetuning launch prerequisites — shared by REST and MCP launch surfaces."""

from __future__ import annotations

from typing import Any

from overbae.modal.model_registry import TIER_ORDER
from overbae.models import Capability, Dataset, EvalSet
from overbae.services.datasets import review
from overbae.services.datasets import rows as row_store
from overbae.services.datasets import use as dataset_use
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.finetuning_validator import validate_dataset
from overbae.services.recommendation import (
    get_recommendation,
    recommend_hyperparams_for_model,
    tier_models,
)

# The ranking covers the whole catalog; chat gets the head of it, with "catalog" below
# still naming every trainable model.
_MAX_CHAT_CANDIDATES = 8


def default_eval_dataset(capability: Capability | None) -> Dataset | None:
    """The capability's eval dataset when it owns exactly one whose active version fits."""
    if capability is None:
        return None
    owned = [
        dataset
        for dataset in Dataset.objects.filter(
            capability=capability, intent=Dataset.Intent.EVAL
        ).select_related("active")[:50]
        if dataset.active_cell is not None and dataset.active_cell.fits("eval")[0]
    ]
    return owned[0] if len(owned) == 1 else None


def default_eval_set(capability: Capability | None) -> EvalSet | None:
    if capability is None or capability.active_eval_set_id is None:
        return None
    return capability.active_eval_set


def slim_recommendation_row(row: dict[str, Any]) -> dict[str, Any]:
    cost = row.get("cost_estimate") or {}
    time_est = row.get("time_estimate") or {}
    return {
        "model": row.get("model"),
        "tier": row.get("tier"),
        "display_name": row.get("display_name"),
        "params": row.get("params"),
        "grade": row.get("grade"),
        "confidence": row.get("confidence"),
        "n_benchmarks": row.get("n_benchmarks", 0),
        "selected": bool(row.get("selected")),
        "use_lora": bool(row.get("use_lora", True)),
        "hyperparams": row.get("hyperparams") or {},
        "serving_context": row.get("serving_context"),
        "cost_usd": cost.get("usd"),
        "time_human": time_est.get("human"),
    }


_LORA = {
    "type": "Lora",
    "lora_r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "lora_trainable_modules": "all-linear",
}


def job_hyperparameters_from_recommendation(
    rec: dict[str, Any], training_type: str | None = None
) -> dict[str, Any]:
    """Turn a recommender experiment row into FinetuningJob.hyperparameters; an explicit
    ``training_type`` (lora or full) replaces the derived one."""
    from overbae.modal.training_type import training_enabled  # noqa: PLC0415

    hp = dict(rec.get("hyperparams") or {})
    lora_ok = training_enabled(rec, "lora") or bool(rec.get("supports_lora", False))
    if training_type == "lora" and not lora_ok:
        raise ValueError(f"{rec.get('model') or 'This model'} does not train with LoRA.")
    if training_type is not None:
        current = (hp.get("training_type") or {}).get("type")
        wanted = "Lora" if training_type == "lora" else "Full"
        if current != wanted:
            hp["training_type"] = dict(_LORA) if wanted == "Lora" else {"type": "Full"}
    elif "training_type" not in hp:
        # compute_hyperparams normally supplies this; floor for the rows it misses.
        lora = rec.get("use_lora", True) and lora_ok
        hp["training_type"] = dict(_LORA) if lora else {"type": "Full"}
    return hp


def default_finetune_name(*, display_name: str, dataset_name: str, capability_name: str) -> str:
    parts = [s.strip() for s in (display_name, dataset_name, capability_name) if s and s.strip()]
    return " · ".join(parts) if parts else "Chat finetune"


def catalog_by_tier(*, has_tool_calling: bool = False) -> dict[str, list[str]]:
    """Trainable model ids by tier for the active backend."""
    out: dict[str, list[str]] = {}
    for tier in TIER_ORDER:
        models = tier_models().get(tier) or []
        ids = [
            m["id"]
            for m in models
            if m.get("id")
            and not m.get("disabled")
            and (not has_tool_calling or m.get("supports_tool_calling"))
        ]
        if ids:
            out[tier] = ids
    return out


def finetune_prerequisite_report(
    dataset: Dataset,
    cell,
    *,
    capability: Capability | None = None,
    eval_dataset: Dataset | None = None,
    eval_cell=None,
) -> dict[str, Any]:
    """Readiness of the chosen train and eval versions (wizard-equivalent gates)."""
    missing: list[str] = []
    warnings: list[str] = []
    validation = None
    for ds, version, intent, label in (
        (dataset, cell, "train", "training dataset"),
        (eval_dataset, eval_cell, "eval", "eval dataset"),
    ):
        if ds is None:
            continue
        if version is None:
            missing.append(f"{label} — no version yet")
            continue
        try:
            dataset_use.check(ds, intent, cell=version)
        except DatasetError as exc:
            missing.append(f"{label} — {exc.detail}")
            continue
        warnings.extend(
            f"{label}: {finding}" for finding in review.warnings(ds, version, capability=capability)
        )
    train_ok = cell is not None and not any(m.startswith("training dataset") for m in missing)
    if train_ok:
        validation = validate_dataset(str(dataset.id), cell_id=str(cell.id))
        if not validation.valid:
            missing.append("training dataset — fix the rows the validator lists in a new version")
    eval_ok = eval_cell is not None and not any(m.startswith("eval dataset") for m in missing)

    overlap_count = None
    if train_ok and eval_ok:
        overlap_count = row_store.contamination(cell, eval_cell)["overlap_count"]
        if overlap_count:
            warnings.append(
                f"train/eval split — {overlap_count} overlapping training rows; review the split before training"
            )

    analysis: dict[str, Any] = {}
    recommendation_error = None
    if train_ok:
        try:
            analysis = get_recommendation(
                str(dataset.id),
                capability_id=str(capability.id) if capability is not None else None,
                cell=cell,
                eval_cell=eval_cell if eval_ok else None,
            )
        except Exception as exc:  # a checklist must still render without the ranking
            recommendation_error = str(exc)

    candidates = analysis.get("candidates") or []
    has_tool_calling = bool((analysis.get("dataset") or {}).get("has_tool_calling"))
    return {
        "missing": missing,
        "warnings": warnings,
        "validation": validation,
        "overlap_count": overlap_count,
        "recommendations": [slim_recommendation_row(r) for r in candidates[:_MAX_CHAT_CANDIDATES]],
        "n_candidates": len(candidates),
        "catalog": catalog_by_tier(has_tool_calling=has_tool_calling),
        "has_tool_calling": has_tool_calling,
        "task_type": analysis.get("task_type"),
        "task_type_source": analysis.get("task_type_source"),
        "excluded": analysis.get("excluded") or [],
        "recommendation_error": recommendation_error,
    }


def stamp_hyperparameters_for_model(
    dataset_id: str, base_model: str, cell: Any = None, training_type: str | None = None
) -> dict[str, Any]:
    rec = recommend_hyperparams_for_model(dataset_id, base_model, cell)
    return job_hyperparameters_from_recommendation(rec, training_type)
