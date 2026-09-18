"""Which models to fine-tune on a dataset, and the evidence behind the order.

Entry points read the database; everything they call is pure. No LLM takes part in any
answer here — the same dataset always produces the same recommendation, so there is
nothing to cache.
"""

from __future__ import annotations

import logging
from typing import Any

from .analysis import build_analysis
from .candidates import build_candidate, dataset_total_tokens
from .catalog import MODEL_MIN_BATCH, active_backend, find_catalog_model, tier_models

logger = logging.getLogger(__name__)

_CLASSIFIER_INPUTS = ("output_kind", "modality", "has_tool_calls")


def get_recommendation(dataset_id: str, capability_id: str | None = None) -> dict[str, Any]:
    """Ranked fine-tuning candidates for a dataset, grounded in its capability's context.

    Without ``capability_id`` the dataset's own capability is used.
    """
    from overbae.models import Capability, Dataset  # noqa: PLC0415
    from overbae.services.datasets.rows import dataset_stats  # noqa: PLC0415

    from .capability_context import collect_capability_context  # noqa: PLC0415

    dataset = Dataset.objects.get(pk=dataset_id)
    capability = None
    if capability_id:
        capability = Capability.objects.get(pk=capability_id, project_id=dataset.project_id)
    elif dataset.capability_id:
        capability = dataset.capability

    stats = dataset_stats(dataset)
    task_type, source = _resolve_task_type(dataset, stats)
    return build_analysis(
        stats,
        task_type=task_type,
        task_type_source=source,
        capability_context=collect_capability_context(capability)
        if capability is not None
        else None,
    )


def estimate_for_hyperparams(
    dataset_id: str,
    *,
    base_model: str,
    n_epochs: int,
    use_lora: bool,
    cell: Any = None,
) -> dict[str, Any]:
    """Re-estimate cost and duration for user-edited hyperparameters."""
    from overbae.modal.model_registry import context_headroom  # noqa: PLC0415
    from overbae.modal.training_type import (  # noqa: PLC0415
        training_context_length,
        training_enabled,
    )
    from overbae.models import Dataset  # noqa: PLC0415
    from overbae.services.datasets.rows import dataset_stats  # noqa: PLC0415
    from overbae.services.finetuning_pricing import estimate_training_run  # noqa: PLC0415

    dataset = Dataset.objects.get(pk=dataset_id)
    stats = dataset_stats(dataset, cell)
    entry = find_catalog_model(base_model)
    if entry is None:
        raise ValueError(f"Unknown base model: {base_model}")
    params_b = entry.get("total_params_b")
    if params_b is None:
        raise ValueError(f"Model {base_model!r} is missing total_params_b")
    kind = "lora" if use_lora else "full"
    kind_label = "LoRA" if use_lora else "full"
    if not training_enabled(entry, kind):
        raise ValueError(f"Model {base_model} does not support {kind_label} fine-tuning")
    max_tokens = int(stats.get("max_token_length") or 0)
    if max_tokens:
        model_max = training_context_length(entry, kind)
        headroom = context_headroom("baseten")
        if model_max is not None and model_max < max_tokens + headroom:
            raise ValueError(
                f"Longest dataset row is ≈{max_tokens:,} tokens but {base_model}'s "
                f"{kind_label} fine-tuning context is {model_max:,} tokens"
                + (f" (need {max_tokens + headroom:,} with headroom)" if headroom else "")
                + "."
            )
    return estimate_training_run(
        dataset_tokens=dataset_total_tokens(stats),
        n_epochs=n_epochs,
        total_params_b=float(params_b),
        use_lora=use_lora,
        backend=active_backend(),
    )


def recommend_hyperparams_for_model(
    dataset_id: str, base_model: str, cell: Any = None
) -> dict[str, Any]:
    """A candidate row for an arbitrary catalog model, ungraded — the same derivation the
    ranked rows use, for a model the user picked themselves.
    """
    from overbae.models import Dataset  # noqa: PLC0415
    from overbae.services.datasets.rows import dataset_stats  # noqa: PLC0415

    dataset = Dataset.objects.get(pk=dataset_id)
    stats = dataset_stats(dataset, cell)
    entry = find_catalog_model(base_model)
    if entry is None:
        raise ValueError(f"Unknown base model: {base_model}")
    max_tokens = int(stats.get("max_token_length") or 0)
    if max_tokens:
        from overbae.modal.model_registry import context_headroom
        from overbae.modal.training_type import training_context_length, usable_training_kinds

        headroom = context_headroom("baseten")
        if not usable_training_kinds(entry, max_tokens=max_tokens, headroom=headroom):
            model_max = max(training_context_length(entry, kind) or 0 for kind in ("lora", "full"))
            raise ValueError(
                f"Longest dataset row is ≈{max_tokens:,} tokens but {base_model} "
                f"only supports {model_max:,} tokens for fine-tuning"
                + (f" (need {max_tokens + headroom:,} with headroom)" if headroom else "")
                + "."
            )
    tier = next(
        (
            t
            for t, models in tier_models().items()
            if any(m.get("id") == base_model for m in models)
        ),
        "small",
    )
    return build_candidate(stats, model_entry=entry, tier=tier)


def _resolve_task_type(dataset, stats: dict[str, Any]) -> tuple[str, str]:
    """The dataset's task type and where it came from.

    A dataset that went straight to the wizard has no context, so it is classified inline
    from its profile and the semantic value is left to arrive with the queued refresh.
    """
    from overbae.models import DatasetContext  # noqa: PLC0415
    from overbae.services.benchmarks.classify import classify_task_type  # noqa: PLC0415
    from overbae.services.benchmarks.taxonomy import TaskType  # noqa: PLC0415
    from overbae.services.eval.context_extractor import enqueue_refresh  # noqa: PLC0415

    context = DatasetContext.objects.filter(dataset=dataset).first()
    if context is not None:
        if context.task_type in set(TaskType.values):
            return context.task_type, context.task_type_source or "heuristic"
        profile = context.profile or {}
        if not _carries_classifier_inputs(profile):
            profile = _profile(dataset)
        return classify_task_type(profile, stats), "heuristic"

    enqueue_refresh(dataset.id)
    return classify_task_type(_profile(dataset), stats), "heuristic"


def _carries_classifier_inputs(profile: dict[str, Any]) -> bool:
    """Stored profiles written before the classifier existed carry none of its fields, so
    classifying them collapses every dataset onto the same fallback task type."""
    return any(profile.get(key) is not None for key in _CLASSIFIER_INPUTS)


def _profile(dataset) -> dict[str, Any]:
    """Profiling reads datapoints and their spans; a failure degrades the task type rather
    than the wizard."""
    from overbae.services.eval import profiler  # noqa: PLC0415

    try:
        return profiler.profile_dataset(dataset)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dataset profiling failed for dataset %s: %s", dataset.id, exc)
        return {}


__all__ = [
    "MODEL_MIN_BATCH",
    "estimate_for_hyperparams",
    "find_catalog_model",
    "get_recommendation",
    "recommend_hyperparams_for_model",
    "tier_models",
]
