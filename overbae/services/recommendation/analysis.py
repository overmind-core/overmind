"""Assembles the wizard payload: constrain, grade, rank, then describe.

Deterministic end to end — no LLM, no cache, no network.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from overbae.services.benchmarks import artifact
from overbae.services.benchmarks.taxonomy import weights_for

from .candidates import build_candidate, dataset_total_tokens
from .constraints import Exclusion, eligible_models
from .ranking import rank, select_default

logger = logging.getLogger(__name__)

# Matches the profiler's own long-dataset threshold, so "long" means the same thing
# wherever the platform says it.
LONG_CONTEXT_TOKENS = 8_000


def build_analysis(
    stats: Mapping[str, Any],
    *,
    task_type: str,
    task_type_source: str,
    capability_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    has_tool_calling = bool(stats.get("has_tool_calling", False))
    max_tokens = int(stats.get("max_token_length") or 0)
    long_context = max_tokens >= LONG_CONTEXT_TOKENS

    eligible, exclusions = eligible_models(
        has_tool_calling=has_tool_calling,
        max_row_tokens=max_tokens or None,
    )
    tier_of = {entry["id"]: tier for tier, models in eligible.items() for entry in models}
    entry_of = {entry["id"]: entry for models in eligible.values() for entry in models}

    weights = weights_for(task_type, long_context=long_context)
    ranked = rank(list(entry_of), task_type, long_context=long_context)

    candidates: list[dict[str, Any]] = []
    for row in ranked:
        try:
            candidates.append(
                build_candidate(
                    stats,
                    model_entry=entry_of[row.model],
                    tier=tier_of[row.model],
                    ranked=row,
                )
            )
        except ValueError as exc:
            # An incomplete catalog row must not take the whole list down with it.
            logger.warning("Skipping %s in recommendations: %s", row.model, exc)
            exclusions.append(Exclusion(model=row.model, reason=str(exc)))

    by_model = {row["model"]: row for row in candidates}
    shown = select_default([row for row in ranked if row.model in by_model])
    if shown:
        by_model[shown[0]]["selected"] = True

    snapshot = artifact.load()
    return {
        "task_type": task_type,
        "task_type_source": task_type_source,
        "skill_weights": {str(skill): weight for skill, weight in weights.items()},
        "dataset": {
            "rows": int(stats.get("num_examples") or 0),
            "total_tokens": dataset_total_tokens(stats),
            "max_token_length": max_tokens,
            "has_tool_calling": has_tool_calling,
        },
        "candidates": candidates,
        "excluded": [{"model": e.model, "reason": e.reason} for e in exclusions],
        "shown": shown,
        "benchmark_snapshot": {"generated_at": snapshot.generated_at},
        "capability_context": capability_context,
    }


__all__ = ["LONG_CONTEXT_TOKENS", "build_analysis"]
