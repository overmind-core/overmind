"""Rolling instrumentation grades per capability, derived at ingest from
presence checks only (no payload parsing) and damped with hysteresis."""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

from overbae.api import overmind_attrs as oc_attrs
from overbae.models import EvidenceProfile, Span
from overbae.models.traces import is_tool_operation

logger = logging.getLogger(__name__)

# Consecutive batches a new grade must repeat before it replaces the current
# one, so a single degenerate trace doesn't flap the profile.
STREAK_TO_FLIP = 3

_PROVENANCE_VALUES = {"user", "agent", "environment", "harness"}
_UNIT_VALUES = {"turn", "run"}
_PAYLOAD_SPAN_TYPES = {"llm_call", "llm", "tool_call", "tool", "retrieval"}

GUIDANCE_BY_CLAUSE: dict[str, str] = {
    "task": "Emit an intent event (overmind.eval.intent) at run start.",
    "units": "Stamp overmind.unit_kind=turn|run on unit boundary spans.",
    "tool_ops": "Trace real tool executions as individual tool spans with tool.name.",
    "provenance": "Stamp overmind.provenance on payload-bearing spans.",
    "observations": "Capture real environment facts with overmind.eval_context(...).",
    "delivery": "Mark the terminal deliverable with deliver(grounded_by=...).",
}

# Top of each clause's ladder in :func:`batch_grades` — anything else leaves
# evidence on the table and earns a punch-list entry.
BEST_GRADE_BY_CLAUSE: dict[str, str] = {
    "task": "declared",
    "units": "explicit",
    "tool_ops": "real",
    "provenance": "tagged",
    "observations": "rich",
    "delivery": "declared",
}


def _has_event(span: Span, name: str) -> bool:
    return any(e.get("name") == name for e in span.events or [])


def batch_grades(spans: list[Span]) -> dict[str, str]:
    payload_spans = 0
    tagged_spans = 0
    tool_names: set[str] = set()
    tool_calls = 0
    observations = 0
    unit_declared = False
    delivery_declared = False
    task_declared = False
    has_root_payload = False

    for span in spans:
        attrs = span.attributes or {}
        span_type = (span.span_type or "").lower()
        carries_payload = (
            span_type in _PAYLOAD_SPAN_TYPES
            or attrs.get(oc_attrs.INPUT_DATA) is not None
            or attrs.get(oc_attrs.OUTPUT_DATA) is not None
        )
        provenance = str(attrs.get(oc_attrs.PROVENANCE) or "")
        if carries_payload:
            payload_spans += 1
            if provenance in _PROVENANCE_VALUES:
                tagged_spans += 1
        # Retrievals count as tool operations — see eval.normalizer._is_tool.
        if is_tool_operation(span_type):
            tool_calls += 1
            name = str(attrs.get(oc_attrs.TOOL_NAME) or span.name or "")
            if name:
                tool_names.add(name)
        if provenance == "environment" or _has_event(span, oc_attrs.EVAL_EVENT_CONTEXT):
            observations += 1
        if str(attrs.get(oc_attrs.UNIT_KIND) or "") in _UNIT_VALUES:
            unit_declared = True
        if str(attrs.get(oc_attrs.DELIVERY)).lower() == "true":
            delivery_declared = True
        if _has_event(span, oc_attrs.EVAL_EVENT_INTENT):
            task_declared = True
        if span.parent_span_id is None and carries_payload:
            has_root_payload = True

    if tool_calls == 0:
        tool_ops = "untraced"
    elif len(tool_names) == 1 and tool_calls >= 3:
        # One tool name fanned over many calls reads as a dispatcher wrapper
        # (browser-use); no per-call arg fingerprint exists to grade it precisely.
        tool_ops = "dispatcher"
    else:
        tool_ops = "real"

    if payload_spans and tagged_spans == payload_spans:
        provenance_grade = "tagged"
    elif tagged_spans:
        provenance_grade = "partial"
    else:
        provenance_grade = "untagged"

    return {
        "task": "declared" if task_declared else ("inferred" if has_root_payload else "absent"),
        "units": "explicit" if unit_declared else "inferred",
        "tool_ops": tool_ops,
        "provenance": provenance_grade,
        "observations": "rich" if observations >= 3 else ("sparse" if observations else "none"),
        "delivery": "declared" if delivery_declared else "heuristic",
    }


def _apply_hysteresis(
    grades: dict[str, str], window: dict[str, Any], observed: dict[str, str]
) -> bool:
    changed = False
    pending: dict[str, Any] = window.setdefault("pending", {})
    for clause, candidate in observed.items():
        current = grades.get(clause)
        if current is None:
            grades[clause] = candidate
            pending.pop(clause, None)
            changed = True
            continue
        if candidate == current:
            pending.pop(clause, None)
            continue
        entry = pending.get(clause)
        if entry and entry.get("grade") == candidate:
            entry["streak"] += 1
        else:
            entry = {"grade": candidate, "streak": 1}
            pending[clause] = entry
        if entry["streak"] >= STREAK_TO_FLIP:
            grades[clause] = candidate
            pending.pop(clause, None)
            changed = True
    return changed


def observe_batch(capability_id: Any, spans: list[Span]) -> None:
    if not spans:
        return
    observed = batch_grades(spans)
    with transaction.atomic():
        profile, _ = EvidenceProfile.objects.select_for_update().get_or_create(
            capability_id=capability_id
        )
        grades = dict(profile.grades or {})
        window = dict(profile.window or {})
        _apply_hysteresis(grades, window, observed)
        window["batches"] = int(window.get("batches") or 0) + 1
        window["spans"] = int(window.get("spans") or 0) + len(spans)
        EvidenceProfile.objects.filter(pk=profile.pk).update(grades=grades, window=window)


def grades_for_capability(capability_id: Any) -> dict[str, str]:
    profile = EvidenceProfile.objects.filter(capability_id=capability_id).only("grades").first()
    return dict(profile.grades or {}) if profile else {}


def unverifiable_clauses(grades: dict[str, str], warrant: Any) -> list[str]:
    """Empty grades (no traffic yet) refuse nothing."""
    if not grades:
        return []
    no_tools = grades.get("tool_ops") == "untraced"
    untagged = grades.get("provenance") == "untagged"
    no_observations = grades.get("observations") == "none"
    missing: list[str] = []
    if "environment" in warrant.provenance and untagged and no_tools:
        missing.append("provenance")
    if "tool_io" in warrant.requires and no_tools:
        missing.append("tool_ops")
    if "environment_evidence" in warrant.requires and no_tools and untagged and no_observations:
        missing.append("observations")
    return missing
