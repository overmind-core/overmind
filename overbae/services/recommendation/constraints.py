"""Hard eligibility: can this model be fine-tuned on this dataset at all.

No benchmark score and no grade reaches this module. Quality ranks the survivors; it
never decides who survives, so the two can never blend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from overbae.modal.training_type import (
    training_context_length,
    training_enabled,
    usable_training_kinds,
)

from .catalog import active_backend, tier_models


@dataclass(frozen=True, slots=True)
class Exclusion:
    model: str
    reason: str


def eligible_models(
    *,
    has_tool_calling: bool,
    max_row_tokens: int | None,
    backend: str | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[Exclusion]]:
    """Tier catalog split into models trainable on this dataset and rejections.

    Tiers left empty are omitted. Each rejected model appears once, with the first
    constraint it fails.
    """
    eligible: dict[str, list[dict[str, Any]]] = {}
    exclusions: list[Exclusion] = []
    for tier, models in tier_models(backend=backend).items():
        survivors = []
        for entry in models:
            reason = _rejection(
                entry,
                has_tool_calling=has_tool_calling,
                max_row_tokens=None if (backend or active_backend()) == "modal" else max_row_tokens,
            )
            if reason is None:
                survivors.append(entry)
            else:
                exclusions.append(Exclusion(model=entry["id"], reason=reason))
        if survivors:
            eligible[tier] = survivors
    return eligible, exclusions


def _rejection(
    entry: dict[str, Any],
    *,
    has_tool_calling: bool,
    max_row_tokens: int | None,
) -> str | None:
    """The dataset-independent blockers come first: a model that cannot be tuned at all
    must not be reported as merely short on context.
    """
    if not training_enabled(entry, "lora") and not training_enabled(entry, "full"):
        return "No supported fine-tuning method"
    if has_tool_calling and not entry.get("supports_tool_calling", False):
        return "No tool-calling fine-tuning support"
    # Model must cover longest row + training headroom (context_headroom in
    # models.json) for at least one training kind. Exact equality leaves zero
    # headroom and would clamp MAX_LENGTH onto the longest row — exclude those too.
    if max_row_tokens:
        from overbae.modal.model_registry import context_headroom

        headroom = context_headroom("baseten")
        if not usable_training_kinds(entry, max_tokens=max_row_tokens, headroom=headroom):
            sft_context = max(
                training_context_length(entry, kind) or 0 for kind in ("lora", "full")
            )
            return f"SFT context {sft_context:,} < longest row {max_row_tokens:,}" + (
                f" + headroom {headroom}" if headroom else ""
            )
    return None
