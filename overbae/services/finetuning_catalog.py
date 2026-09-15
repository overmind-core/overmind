"""Canonical fine-tuning model catalog response."""

from __future__ import annotations

from typing import Any

from django.conf import settings

from overbae.modal.model_registry import TIER_ORDER, baseten_finetuning_catalog
from overbae.services.recommendation.constraints import eligible_models


def fetch_finetuning_model_catalog(
    *, has_tool_calling: bool = False, max_context: int | None = None
) -> dict[str, Any]:
    """Return the catalog payload shared by the frontend API and MCP."""
    backend = getattr(settings, "FINETUNING_BACKEND", "baseten")

    if backend in ("baseten", "modal"):
        # Modal reuses the Baseten catalog verbatim; models.json has no duplicate rows.
        catalog = baseten_finetuning_catalog(include_disabled=True)
        if has_tool_calling:
            catalog = {
                tier: [
                    model
                    for model in models
                    if model.get("supports_tool_calling") and not model.get("disabled")
                ]
                for tier, models in catalog.items()
            }
        return {
            "backend": backend,
            "tiers": [tier for tier in TIER_ORDER if catalog.get(tier)],
            "models": {tier: models for tier, models in catalog.items() if models},
            "has_tool_calling": has_tool_calling,
            "max_context": None,
        }

    catalog, _excluded = eligible_models(
        has_tool_calling=has_tool_calling,
        max_row_tokens=max_context,
    )
    return {
        "backend": backend,
        "tiers": [tier for tier in TIER_ORDER if tier in catalog],
        "models": catalog,
        "has_tool_calling": has_tool_calling,
        "max_context": max_context,
    }
