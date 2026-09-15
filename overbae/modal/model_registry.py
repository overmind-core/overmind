"""Typed accessors over models.json, the single source of truth for the model catalog — never add
model config here or in a consuming module. Every entry carries a ``backend`` field, and one model
appears once per backend it is available on.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

_JSON_PATH = Path(__file__).parent / "models.json"

TIER_ORDER: list[str] = ["compact", "small", "mid", "large"]


@functools.lru_cache(maxsize=1)
def _raw_doc() -> dict[str, Any]:
    with _JSON_PATH.open() as fh:
        return json.load(fh)


def _raw_all() -> list[dict[str, Any]]:
    return _raw_doc()["models"]


def all_model_entries() -> list[dict[str, Any]]:
    """Every entry, across all backends, disabled ones included."""
    return _raw_all()


def provider_display_names() -> dict[str, str]:
    """Display name per model-id prefix, e.g. ``{"qwen": "Qwen"}``. Add or rename a provider in
    ``models.json["providers"]["display_names"]``, never here."""
    return (_raw_doc().get("providers") or {}).get("display_names") or {}


def library_filters() -> dict[str, Any]:
    """Marketing /models filter definitions from ``models.json["library_filters"]``."""
    filters = _raw_doc().get("library_filters") or {}
    return filters if isinstance(filters, dict) else {}


@functools.lru_cache(maxsize=1)
def _raw() -> dict[str, dict[str, Any]]:
    """Together AI models only, keyed by model ID."""
    return {m["id"]: m for m in _raw_all() if m.get("backend") == "together"}


def all_models() -> dict[str, dict[str, Any]]:
    """Together AI models only, keyed by model ID."""
    return _raw()


def get_model(together_id: str) -> dict[str, Any] | None:
    return _raw().get(together_id)


def get_sft_context_length(model_id: str, *, backend: str | None = None) -> int | None:
    """With ``backend`` omitted, prefers ``settings.FINETUNING_BACKEND``, then any matching entry
    that has a finetuning sub-config."""
    if backend is None:
        from django.conf import settings

        backend = getattr(settings, "FINETUNING_BACKEND", None)
    backend = _catalog_backend(backend)

    preferred: int | None = None
    fallback: int | None = None
    for m in _raw_all():
        if m.get("id") != model_id or not m.get("finetuning"):
            continue
        ctx = m["finetuning"].get("context_length")
        if ctx is None:
            continue
        ctx_i = int(ctx)
        if backend and m.get("backend") == backend:
            preferred = ctx_i
            break
        if fallback is None:
            fallback = ctx_i
    return preferred if preferred is not None else fallback


def get_training_context_policy(backend: str) -> dict[str, Any]:
    """The provider's training-context snapping policy, shaped
    ``{"context_buckets": [int, ...], "context_headroom": int}``. No context value lives in code."""
    policy = (_raw_doc().get("finetuning_providers") or {}).get(backend)
    if not policy or not policy.get("context_buckets"):
        raise KeyError(f"models.json has no finetuning_providers entry for {backend!r}")
    return policy


def context_headroom(backend: str) -> int:
    """Training-context headroom (tokens) required above the longest dataset row.
    ``0`` when the backend has no policy entry (e.g. Together)."""
    try:
        return int(get_training_context_policy(backend)["context_headroom"])
    except (KeyError, TypeError, ValueError):
        return 0


def min_sft_context_length(backend: str) -> int | None:
    """Smallest max fine-tuning context across a backend's enabled models: rows under it fit every
    model, rows over it exclude at least one."""
    contexts = [
        int(m["finetuning"]["context_length"])
        for m in _raw_all()
        if m.get("backend") == backend
        and not m.get("disabled")
        and (m.get("finetuning") or {}).get("context_length")
    ]
    return min(contexts) if contexts else None


def _catalog_backend(backend: str | None) -> str | None:
    """models.json has no ``modal`` rows — Modal trains the Baseten catalog."""
    return "baseten" if backend == "modal" else backend


def _finetune_entry(model_id: str, *, backend: str | None = None) -> dict[str, Any] | None:
    if backend is None:
        from django.conf import settings

        backend = getattr(settings, "FINETUNING_BACKEND", None)
    backend = _catalog_backend(backend)
    fallback: dict[str, Any] | None = None
    for m in _raw_all():
        if m.get("id") != model_id:
            continue
        if backend and m.get("backend") == backend:
            return m
        if fallback is None:
            fallback = m
    return fallback


def get_hf_base(model_id: str, *, backend: str | None = None) -> str:
    """HF id for the active finetuning backend (Modal uses Baseten catalog rows)."""
    cfg = _finetune_entry(model_id, backend=backend)
    return cfg["hf_model_id"] if cfg and cfg.get("hf_model_id") else model_id


def get_unsloth_image(model_id: str) -> str:
    """Frozen train-stack id (``FamilySpec.train_image``). Catalog ``unsloth_image`` is
    documentation and must match — see tests/test_stacks.py."""
    from modal_shared.modelfam import resolve
    from modal_shared.stacks import normalize_train_stack

    return normalize_train_stack(resolve(model_id).train_image)


def _tier_model_entry(cfg: dict[str, Any]) -> dict[str, Any]:
    """Build a recommender/catalog dict from a single models.json entry."""
    from overbae.modal.training_type import normalize_training_type  # noqa: PLC0415

    ft = cfg["finetuning"]
    return {
        "id": cfg["id"],
        "display": cfg["display"],
        "params": cfg["params"],
        "total_params_b": cfg["total_params_b"],
        # The recommendation engine reads this external key name.
        "context_length_sft": ft["context_length"],
        "max_batch_size": ft["max_batch_size"],
        "min_batch_size": ft["min_batch_size"],
        "supports_tool_calling": cfg["supports_tool_calling"],
        # Same shape as models.json finetuning.training_type — not supports_*.
        "training_type": normalize_training_type(ft["training_type"]),
    }


def get_tier_models(*, backend: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Tier catalog read from the finetuning sub-config of ``backend`` (default
    ``settings.FINETUNING_BACKEND``). Disabled models are excluded — they cannot be
    recommended or used."""
    if backend is None:
        from django.conf import settings

        backend = _catalog_backend(getattr(settings, "FINETUNING_BACKEND", "baseten"))

    buckets: dict[str, list[dict[str, Any]]] = {t: [] for t in TIER_ORDER}
    for cfg in _raw_all():
        if cfg.get("backend") != backend or cfg.get("disabled") or not cfg.get("finetuning"):
            continue
        tier = cfg.get("tier")
        if tier not in buckets:
            continue
        buckets[tier].append(_tier_model_entry(cfg))
    return {tier: models for tier, models in buckets.items() if models}


def get_model_config_any_backend(model_id: str) -> dict[str, Any] | None:
    """The first non-disabled match across backends, or ``None`` when uncatalogued."""
    cfg = _raw().get(model_id)
    if cfg:
        return cfg
    for m in _raw_all():
        aliases = {m.get("id"), m.get("hf_model_id"), *(m.get("benchmark_hf_model_ids") or [])}
        if model_id in aliases:
            return m
    return None


def _finetuning_catalog(
    backend: str, *, include_disabled: bool = False
) -> dict[str, list[dict[str, Any]]]:
    """Fine-tuning catalog for a backend, grouped by tier (shared shape)."""
    from overbae.modal.training_type import normalize_training_type  # noqa: PLC0415

    buckets: dict[str, list[dict[str, Any]]] = {t: [] for t in TIER_ORDER}
    for m in _raw_all():
        if m.get("backend") != backend or not m.get("finetuning"):
            continue
        if not include_disabled and m.get("disabled"):
            continue
        tier = m.get("tier")
        if tier not in buckets:
            continue
        ft = m["finetuning"]
        entry: dict[str, Any] = {
            "id": m["id"],
            "display": m["display"],
            "params": m["params"],
            "total_params_b": m["total_params_b"],
            "context_length": ft["context_length"],
            # Alias under the field name the Together catalog uses.
            "context_length_sft": ft["context_length"],
            "max_batch_size": ft["max_batch_size"],
            "min_batch_size": ft["min_batch_size"],
            "supports_tool_calling": m["supports_tool_calling"],
            "training_type": normalize_training_type(ft["training_type"]),
        }
        if m.get("disabled"):
            entry["disabled"] = True
            entry["disabled_reason"] = m.get("disabled_reason", "")
        buckets[tier].append(entry)
    return {tier: models for tier, models in buckets.items() if models}


def baseten_finetuning_catalog(
    *, include_disabled: bool = False
) -> dict[str, list[dict[str, Any]]]:
    return _finetuning_catalog("baseten", include_disabled=include_disabled)


def get_all_models_by_backend(
    backend: str, *, include_disabled: bool = True
) -> dict[str, dict[str, Any]]:
    return {
        m["id"]: m
        for m in _raw_all()
        if m.get("backend") == backend and (include_disabled or not m.get("disabled"))
    }
