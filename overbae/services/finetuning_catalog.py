"""Canonical fine-tuning model catalog response."""

from __future__ import annotations

from typing import Any

from django.conf import settings

from modal_shared.training_monitoring import capabilities as monitoring_capabilities
from overbae.modal.model_registry import TIER_ORDER, finetuning_catalog
from overbae.services import model_catalog


def fetch_finetuning_model_catalog(*, has_tool_calling: bool = False) -> dict[str, Any]:
    """Return the catalog payload shared by the frontend API and MCP."""
    backend = getattr(settings, "FINETUNING_BACKEND", "modal")
    catalog = finetuning_catalog(backend, include_disabled=True)
    if has_tool_calling:
        catalog = {
            tier: [
                model
                for model in models
                if model.get("supports_tool_calling") and not model.get("disabled")
            ]
            for tier, models in catalog.items()
        }
    upstream = model_catalog.fetch_model_catalog()
    catalog = {
        tier: [
            {**entry, **model_catalog.training_openrouter_match(entry["id"], catalog=upstream)}
            for entry in models
        ]
        for tier, models in catalog.items()
        if models
    }
    return {
        "backend": backend,
        "tiers": [tier for tier in TIER_ORDER if tier in catalog],
        "models": catalog,
        "has_tool_calling": has_tool_calling,
        "monitoring": monitoring_capabilities(backend),
    }
