"""Deterministic task-type classification from a ``profiler.profile_dataset``
result. No LLM, no I/O."""

from __future__ import annotations

from typing import Any

from overbae.services.benchmarks.taxonomy import TaskType

# Free text splits on output/input chars: at or below 0.5x is a summary, at or above
# 2.0x is composition, and the band between them is question answering.
_CONDENSE_RATIO = 0.5
_EXPAND_RATIO = 2.0


def classify_task_type(profile: dict[str, Any], stats: dict[str, Any] | None = None) -> str:
    """The profile carries no length fields, so without *stats* the free-text branch
    collapses to question answering."""
    modality = profile.get("modality")
    output_kind = profile.get("output_kind")

    if profile.get("has_tool_calls") or modality == "tool_calling":
        return TaskType.TOOL_CALLING.value
    if output_kind == "label":
        return TaskType.CLASSIFICATION.value
    if output_kind == "json":
        return TaskType.EXTRACTION.value
    if modality == "multi_turn":
        return TaskType.DIALOGUE.value
    if output_kind == "free_text":
        return _classify_free_text(stats)
    return TaskType.QUESTION_ANSWERING.value


def _classify_free_text(stats: dict[str, Any] | None) -> str:
    avg_in = _positive_float(stats, "avg_input_chars")
    avg_out = _positive_float(stats, "avg_output_chars")
    if avg_in is None or avg_out is None:
        return TaskType.QUESTION_ANSWERING.value
    if avg_out <= _CONDENSE_RATIO * avg_in:
        return TaskType.SUMMARIZATION.value
    if avg_out >= _EXPAND_RATIO * avg_in:
        return TaskType.CREATIVE_WRITING.value
    return TaskType.QUESTION_ANSWERING.value


def _positive_float(stats: dict[str, Any] | None, key: str) -> float | None:
    if not stats:
        return None
    try:
        value = float(stats.get(key))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
