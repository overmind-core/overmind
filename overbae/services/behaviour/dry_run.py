"""Bind and grade caller-supplied spans with zero database writes, so a coding
agent can check instrumentation before a real trace ever reaches ingest.
"""

from __future__ import annotations

from typing import Any

from overbae.models import Behaviour, Capability, Span, TaskExecution
from overbae.services.behaviour.binder import (
    DECLARED_BEHAVIOUR_KEY,
    FLAG_AMBIGUOUS,
    FLAG_DECLARED_GRAIN_MISMATCH,
    FLAG_GRAIN_MISMATCH,
    declared_key,
    resolve_binding,
    resolve_unit_capability,
    span_qualname,
)
from overbae.services.eval.profiles import BEST_GRADE_BY_CLAUSE, GUIDANCE_BY_CLAUSE, batch_grades
from overbae.services.eval.units import carve, run_surfaces

_REQUIRED_KEYS = ("span_id", "trace_id", "name")
_FAIL_FLAGS = {FLAG_AMBIGUOUS, FLAG_DECLARED_GRAIN_MISMATCH, FLAG_GRAIN_MISMATCH}
_SPAN_FIELDS = (
    "span_id",
    "trace_id",
    "parent_span_id",
    "span_type",
    "name",
    "start_time_ns",
    "end_time_ns",
    "attributes",
    "events",
    "resource_attrs",
    "status_code",
)


def _task_ok(task: dict[str, Any]) -> bool:
    if task["binding_source"] == TaskExecution.BindingSource.UNBOUND:
        return False
    if task.get("declared_key") and task["binding_source"] != TaskExecution.BindingSource.DECLARED:
        return False
    return not (_FAIL_FLAGS & set(task["route_flags"]))


def _build_span(raw: dict[str, Any], project_id: str, index: int) -> tuple[Span | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"span at index {index} is not an object"
    missing = [key for key in _REQUIRED_KEYS if not raw.get(key)]
    if missing:
        return None, f"span at index {index} missing required field(s): {', '.join(missing)}"
    fields = {key: raw[key] for key in _SPAN_FIELDS if key in raw}
    return Span(project_id=project_id, **fields), None


def _identity_of(project_id: str, spans: list[Span]) -> Capability | None:
    """span capability.id, then resource capability.id."""
    if not spans:
        return None
    return resolve_unit_capability(project_id, spans[0], spans[1:])


def verify_spans(
    project_id: str, spans_payload: list[dict], capability: Capability | None = None
) -> dict[str, Any]:
    """Each unit resolves its own capability — from its own spans' identity, then
    its ancestors', then the ``capability`` fallback — so a smoke run spanning
    several capabilities, or a handoff across a capability boundary, is never
    smeared under one."""
    errors: list[dict[str, Any]] = []
    spans: list[Span] = []
    for index, raw in enumerate(spans_payload or []):
        span, error = _build_span(raw, project_id, index)
        if error:
            errors.append({"index": index, "error": error})
        else:
            spans.append(span)

    by_trace: dict[str, list[Span]] = {}
    for span in spans:
        by_trace.setdefault(span.trace_id, []).append(span)

    tasks: list[dict[str, Any]] = []
    unit_capability: dict[str, Capability | None] = {}
    span_owner_unit: dict[str, str] = {}
    trace_capability: dict[str, Capability | None] = {}

    for trace_id, trace_spans in by_trace.items():
        carved = carve(trace_spans)
        units = list(carved.units)
        seen = {u.unit_span.span_id for u in units}
        for surface in run_surfaces(trace_spans, carved):
            if surface.unit_span.span_id in seen:
                continue
            resolved = (
                _identity_of(project_id, surface.member_spans)
                or _identity_of(project_id, surface.ancestor_spans)
                or capability
            )
            binding = resolve_binding(
                unit_span=surface.unit_span,
                unit_spans=surface.member_spans,
                capability=resolved,
                project_id=project_id,
                ancestor_spans=surface.ancestor_spans,
            )
            if binding.version is None or binding.version.behaviour.grain != Behaviour.Grain.RUN:
                continue
            units.append(surface)
            seen.add(surface.unit_span.span_id)
        for unit in units:
            resolved = (
                _identity_of(project_id, unit.member_spans)
                or _identity_of(project_id, unit.ancestor_spans)
                or capability
            )
            unit_capability[unit.unit_span.span_id] = resolved
            binding = resolve_binding(
                unit_span=unit.unit_span,
                unit_spans=unit.member_spans,
                capability=resolved,
                project_id=project_id,
                ancestor_spans=unit.ancestor_spans,
            )
            tasks.append(
                {
                    "behaviour_key": binding.version.behaviour.key if binding.version else None,
                    "binding_source": binding.binding_source,
                    "declared_key": declared_key(unit.unit_span) or None,
                    "route_flags": binding.flags,
                    "unit_span_id": unit.unit_span.span_id,
                    "trace_id": trace_id,
                    "capability": resolved.name if resolved else None,
                    "capability_id": str(resolved.id) if resolved else None,
                    # What the binder actually saw, so an unbound verdict is
                    # diagnosable from the response instead of local span dumps.
                    "spans_seen": [
                        {
                            "name": s.name,
                            "behaviour_key": (s.attributes or {}).get(DECLARED_BEHAVIOUR_KEY),
                            "unit_kind": (s.attributes or {}).get("overmind.unit_kind"),
                            "qualname": span_qualname(s) or None,
                        }
                        for s in unit.member_spans[:20]
                    ],
                }
            )

        # Innermost unit claims its own spans first, so a nested unit's spans
        # aren't graded under an enclosing unit's capability.
        for unit in sorted(units, key=lambda u: len(u.member_spans)):
            for span in unit.member_spans:
                span_owner_unit.setdefault(span.span_id, unit.unit_span.span_id)

        trace_capability[trace_id] = _identity_of(project_id, trace_spans) or capability

    groups: dict[str | None, list[Span]] = {}
    group_capability: dict[str | None, Capability | None] = {}
    for span in spans:
        owner = span_owner_unit.get(span.span_id)
        resolved = unit_capability[owner] if owner is not None else trace_capability[span.trace_id]
        key = str(resolved.id) if resolved else None
        groups.setdefault(key, []).append(span)
        group_capability[key] = resolved

    capabilities: list[dict[str, Any]] = []
    for key, group_spans in groups.items():
        resolved = group_capability[key]
        grades = batch_grades(group_spans)
        capabilities.append(
            {
                "capability": resolved.name if resolved else None,
                "capability_id": key,
                "grades": grades,
                "punch_list": [
                    {"grade": clause, "instruction": GUIDANCE_BY_CLAUSE[clause]}
                    for clause, best in BEST_GRADE_BY_CLAUSE.items()
                    if grades.get(clause) != best
                ],
            }
        )

    return {
        "ok": not errors and bool(tasks) and all(_task_ok(task) for task in tasks),
        "tasks": tasks,
        "capabilities": capabilities,
        "errors": errors,
    }
