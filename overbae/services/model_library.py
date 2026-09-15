"""Public model-library catalog — powers the marketing site's `/library` pages.

Open-weight models from models.json (fine-tuning + inference on Overmind's infra)
plus closed-source frontier models from ``overbae.frontier_models`` (inference only,
via OpenRouter). Never reads FinetuningJob/DeployedModel — a user's own fine-tuned
instances are private and project-scoped.
"""

from __future__ import annotations

import re
from typing import Any

from overbae.frontier_models import external_frontier_models
from overbae.modal.model_registry import (
    TIER_ORDER,
    all_model_entries,
    library_filters,
    provider_display_names,
)

CATEGORY_OPEN_WEIGHT = "open-weight"
CATEGORY_FRONTIER = "frontier"


def _provider_from_id(model_id: str) -> str:
    prefix = model_id.split("/", 1)[0] if "/" in model_id else model_id
    display = provider_display_names().get(prefix.lower())
    if display:
        return display
    name = model_id.split("/", 1)[1] if "/" in model_id else model_id
    lowered = name.lower()
    if lowered.startswith("muse"):
        return "Meta"
    if "nemotron" in lowered:
        return "NVIDIA"
    return prefix.replace("-", " ").replace("_", " ").title()


def _display_from_id(model_id: str) -> str:
    """Humanize a bare id — frontier models carry no ``display`` field."""
    name = model_id.split("/", 1)[1] if "/" in model_id else model_id
    return name.replace("-", " ").replace("_", " ").title()


def slugify_model_id(model_id: str) -> str:
    """URL-safe slug for a model id (``Qwen/Qwen3.5-9B`` -> ``qwen-3-5-9b``).

    A dash goes between a multi-letter token and a following digit (``Qwen3`` ->
    ``qwen-3``) but never splits MoE markers like ``A3B``.
    """
    name = model_id.split("/", 1)[-1]
    slug = name.lower().replace(".", "-")
    slug = re.sub(r"([a-z]{2,})(\d)", r"\1-\2", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-")


def _pricing(cfg: dict[str, Any]) -> dict[str, float] | None:
    """Marketing 'from' floors from models.json, or None when unset."""
    pricing = cfg.get("pricing")
    if not isinstance(pricing, dict):
        return None
    train = pricing.get("train_from_usd")
    run = pricing.get("run_from_usd_per_1m_output")
    if train is None or run is None:
        return None
    return {
        "train_from_usd": float(train),
        "run_from_usd_per_1m_output": float(run),
    }


def _marketing(cfg: dict[str, Any]) -> dict[str, Any] | None:
    model_m = cfg.get("marketing")
    if not isinstance(model_m, dict):
        return None

    good_for = model_m.get("good_for") or []
    if not isinstance(good_for, list):
        good_for = []

    pitch = model_m.get("pitch")
    overview = model_m.get("overview")
    description = model_m.get("description")

    return {
        "pitch": pitch,
        "overview": overview,
        "description": description,
        "good_for": [str(item) for item in good_for],
    }


def _use_cases(cfg: dict[str, Any]) -> list[str]:
    raw = cfg.get("use_cases") or []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _open_weight_entry(cfg: dict[str, Any]) -> dict[str, Any]:
    finetuning = cfg.get("finetuning") or {}
    # models.json always ships finetuning.training_type.{lora,full}.enabled.
    training_type = finetuning["training_type"] if finetuning else None
    supports_finetuning = bool(
        training_type and (training_type["lora"]["enabled"] or training_type["full"]["enabled"])
    )

    return {
        "id": cfg["id"],
        "slug": slugify_model_id(cfg["id"]),
        "display": cfg.get("display") or cfg["id"],
        "provider": _provider_from_id(cfg["id"]),
        "group": cfg.get("group"),
        "category": CATEGORY_OPEN_WEIGHT,
        "hf_model_id": cfg.get("hf_model_id"),
        "tier": cfg.get("tier"),
        "params": cfg.get("params"),
        "total_params_b": cfg.get("total_params_b"),
        "context_length": cfg.get("context_length"),
        "supports_tool_calling": bool(cfg.get("supports_tool_calling")),
        "use_cases": _use_cases(cfg),
        "type": cfg.get("type"),
        "hardware": (cfg.get("inference") or {}).get("gpu_type"),
        "available_for": {
            "finetuning": supports_finetuning,
            # Every models.json entry carries an inference config, fine-tunable or not.
            "inference": True,
        },
        "finetuning": {
            "context_length": finetuning.get("context_length"),
            "training_type": training_type,
        }
        if finetuning
        else None,
        "pricing": _pricing(cfg),
        "marketing": _marketing(cfg),
    }


def _frontier_entry(cfg: dict[str, Any]) -> dict[str, Any]:
    model_id = cfg["id"]
    return {
        "id": model_id,
        "slug": slugify_model_id(model_id),
        "display": _display_from_id(model_id),
        "provider": provider_display_names().get(
            (cfg.get("owned_by") or "").lower(), cfg.get("owned_by", "")
        ),
        "group": None,
        "category": CATEGORY_FRONTIER,
        "hf_model_id": None,
        "tier": None,
        "params": None,
        "total_params_b": None,
        "context_length": None,
        "supports_tool_calling": True,
        "use_cases": [],
        "type": "chat",
        "hardware": None,
        "available_for": {"finetuning": False, "inference": True},
        "finetuning": None,
        "pricing": None,
        "marketing": None,
    }


def public_library_filters() -> dict[str, Any]:
    filters = library_filters()
    return {
        "use_case": filters.get("use_case") or [],
        "tier": filters.get("tier") or [],
        "context": filters.get("context") or [],
    }


def public_model_catalog() -> list[dict[str, Any]]:
    """Every model available on the platform: open-weight + frontier.

    One entry per enabled models.json id, deduped across backends — the ``baseten``
    entry wins over ``together`` because it is the live serving backend
    (``settings.FINETUNING_BACKEND``).
    """
    by_id: dict[str, dict[str, Any]] = {}
    for cfg in all_model_entries():
        if cfg.get("disabled"):
            continue
        existing = by_id.get(cfg["id"])
        if existing is None or (
            cfg.get("backend") == "baseten" and existing.get("backend") != "baseten"
        ):
            by_id[cfg["id"]] = cfg

    open_weight = [_open_weight_entry(cfg) for cfg in by_id.values()]
    frontier = [_frontier_entry(cfg) for cfg in external_frontier_models()]

    def _sort_key(entry: dict[str, Any]) -> tuple[int, int, float]:
        if entry["category"] == CATEGORY_FRONTIER:
            return (1, 0, 0.0)
        tier_index = (
            TIER_ORDER.index(entry["tier"]) if entry.get("tier") in TIER_ORDER else len(TIER_ORDER)
        )
        return (0, tier_index, -(entry.get("total_params_b") or 0))

    return sorted(open_weight + frontier, key=_sort_key)


def get_public_model(slug: str) -> dict[str, Any] | None:
    return next((m for m in public_model_catalog() if m["slug"] == slug), None)
