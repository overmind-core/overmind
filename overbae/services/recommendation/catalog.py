"""Reads over the models.json catalog. No filtering, no policy, no scoring.

The catalog lives in overbae/modal/models.json — add or remove models there, never here.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from overbae.modal.model_registry import get_all_models_by_backend, get_tier_models


def active_backend() -> str:
    return getattr(settings, "FINETUNING_BACKEND", "baseten")


def catalog_backend() -> str:
    """models.json backend key for FINETUNING_BACKEND — Modal reuses Baseten's rows."""
    backend = active_backend()
    return "baseten" if backend == "modal" else backend


def tier_models(*, backend: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Tier catalog for ``backend``, defaulting to the active one."""
    return get_tier_models(backend=backend or catalog_backend())


def find_catalog_model(model_id: str) -> dict[str, Any] | None:
    for models in tier_models().values():
        for entry in models:
            if entry.get("id") == model_id:
                return entry
    return None


def gpu_config(model_id: str, backend: str) -> dict[str, Any]:
    cfg = get_all_models_by_backend(backend).get(model_id) or {}
    inference = cfg.get("inference") or {}
    return {
        "gpu_type": inference.get("gpu_type"),
        # The catalog is sized for single-GPU BF16 serving (see models.json comment).
        "num_gpus": 1,
        "max_model_len": inference.get("max_model_len"),
    }


# Together-only lookup — used by TogetherAIRunner for min-batch coercion.
MODEL_MIN_BATCH: dict[str, int] = {
    m["id"]: m["min_batch_size"]
    for models in get_tier_models(backend="together").values()
    for m in models
}
