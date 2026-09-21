"""Finetuning launch prerequisites — shared by REST and MCP launch surfaces."""

from __future__ import annotations

from typing import Any

from django.db.models import Q

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


def default_eval_dataset(project, capability: Capability | None) -> Dataset | None:
    """Wizard seeding: capability-owned eval dataset with a version that fits, else any in the project."""
    qs = Dataset.objects.filter(project=project, intent=Dataset.Intent.EVAL).order_by("-updated_at")
    if capability is not None:
        for own in qs.filter(capability=capability)[:10]:
            cell = own.active_cell
            if cell is not None and cell.fits("eval")[0]:
                return own
    for row in qs[:20]:
        cell = row.active_cell
        if cell is not None and cell.fits("eval")[0]:
            return row
    return None


def default_eval_set(project, capability: Capability | None) -> EvalSet | None:
    if capability is not None and capability.active_eval_set_id is not None:
        return capability.active_eval_set
    sets = EvalSet.objects.filter(
        Q(capability__status=Capability.Status.CURRENT) | Q(capability__isnull=True),
        project=project,
    )
    if capability is not None:
        sets = sets.filter(Q(capability=capability) | Q(capability__isnull=True))
    return sets.select_related("capability").order_by("-created_at").first()


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
        "cost_usd": cost.get("usd"),
        "time_human": time_est.get("human"),
    }


def job_hyperparameters_from_recommendation(rec: dict[str, Any]) -> dict[str, Any]:
    """Turn a recommender experiment row into FinetuningJob.hyperparameters."""
    from overbae.modal.training_type import training_enabled  # noqa: PLC0415

    hp = dict(rec.get("hyperparams") or {})
    if "training_type" not in hp:
        lora_ok = training_enabled(rec, "lora") or bool(rec.get("supports_lora", False))
        if rec.get("use_lora", True) and lora_ok:
            # compute_hyperparams normally supplies this; floor for the rows it misses.
            hp["training_type"] = {
                "type": "Lora",
                "lora_r": 16,
                "lora_alpha": 32,
                "lora_dropout": 0.05,
                "lora_trainable_modules": "all-linear",
            }
        else:
            hp["training_type"] = {"type": "Full"}
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
    project,
    dataset: Dataset,
    *,
    capability: Capability | None = None,
) -> dict[str, Any]:
    """Readiness checklist before create_finetune_job (wizard-equivalent gates)."""
    validation = validate_dataset(str(dataset.id))
    eval_dataset = default_eval_dataset(project, capability)
    eval_set = default_eval_set(project, capability)

    missing: list[str] = []
    warnings: list[str] = []
    product = dataset.active_cell
    if product is None:
        missing.append("training dataset — no version has run yet")
    elif not product.fits("train")[0]:
        missing.append(f"training dataset — {product.fits('train')[1]}")
    elif not validation.valid:
        missing.append(
            "training dataset — fix the rows the validator lists, then run the notebook again"
        )
    if eval_dataset is None:
        missing.append(
            "eval dataset — create an eval dataset whose version fits the eval contract "
            "(wizard needs it for in-training judge evals)"
        )
    if eval_set is None:
        missing.append("eval set — create an eval set with generative evaluators in this project")

    overlap_count = None
    eval_product = eval_dataset.active_cell if eval_dataset is not None else None
    for ds, cell, intent, label in (
        (dataset, product, "train", "training dataset"),
        (eval_dataset, eval_product, "eval", "eval dataset"),
    ):
        if ds is not None and cell is not None:
            try:
                dataset_use.check(ds, intent, cell=cell)
            except DatasetError as exc:
                missing.append(f"{label} — {exc.detail}")
            warnings.extend(
                f"{label}: {finding}"
                for finding in review.warnings(ds, cell, capability=capability)
            )
    if product is not None and eval_product is not None:
        overlap_count = row_store.contamination(product, eval_product)["overlap_count"]
        if overlap_count:
            warnings.append(
                f"train/eval split — {overlap_count} overlapping training rows; review the split before training"
            )

    analysis: dict[str, Any] = {}
    recommendation_error = None
    try:
        analysis = get_recommendation(
            str(dataset.id),
            capability_id=str(capability.id) if capability is not None else None,
        )
    except Exception as exc:  # a checklist must still render without the ranking
        recommendation_error = str(exc)

    candidates = analysis.get("candidates") or []
    has_tool_calling = bool((analysis.get("dataset") or {}).get("has_tool_calling"))
    recommendations = [slim_recommendation_row(r) for r in candidates[:_MAX_CHAT_CANDIDATES]]

    hint = None
    if missing:
        hint = "call finetune_prerequisites again after fixing: " + "; ".join(missing)

    return {
        "ready": not missing,
        "missing": missing,
        "warnings": warnings,
        "hint": hint,
        "dataset": dataset.name or str(dataset.id)[:8],
        "capability": capability.slug if capability is not None else None,
        "validation": {
            "valid": validation.valid,
            "errors": validation.errors,
            "warnings": validation.warnings,
            "num_examples": validation.num_examples,
            "format": validation.format,
        },
        "eval_dataset": (
            {
                "name": eval_dataset.name or str(eval_dataset.id)[:8],
                "rows": eval_product.rows if eval_product is not None else 0,
            }
            if eval_dataset is not None
            else None
        ),
        "eval_set": eval_set.name if eval_set is not None else None,
        "overlap_count": overlap_count,
        "recommendations": recommendations,
        "n_candidates": len(candidates),
        "catalog": catalog_by_tier(has_tool_calling=has_tool_calling),
        "has_tool_calling": has_tool_calling,
        "task_type": analysis.get("task_type"),
        "task_type_source": analysis.get("task_type_source"),
        "excluded": analysis.get("excluded") or [],
        "recommendation_error": recommendation_error,
    }


def stamp_hyperparameters_for_model(
    dataset_id: str, base_model: str, cell: Any = None
) -> dict[str, Any]:
    rec = recommend_hyperparams_for_model(dataset_id, base_model, cell)
    return job_hyperparameters_from_recommendation(rec)
