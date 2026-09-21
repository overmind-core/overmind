"""What the platform already observed about a capability, gathered for the wizard payload."""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.utils import timezone

from overbae.services.tool_names import canonical_tool_name

_TRACE_WINDOW_DAYS = 30
# Cap the in-Python duration aggregation, mirroring MCP capability health.
_TRACE_ROW_CAP = 5000
_TOOL_CAP = 15


def _tool_call_counts(capability, cutoff_ns: int) -> list[dict[str, Any]]:
    """Per-tool call and error volume over the same window as the trace aggregates."""
    from overbae.models import Span  # noqa: PLC0415
    from overbae.models.traces import TOOL_OPERATION_TYPES  # noqa: PLC0415

    counts: dict[str, dict[str, Any]] = {}
    rows = Span.objects.filter(
        project_id=capability.project_id,
        capability=capability,
        span_type__in=TOOL_OPERATION_TYPES,
        start_time_ns__gte=cutoff_ns,
    ).values_list("name", "status_code", "attributes")[:_TRACE_ROW_CAP]
    for name, status_code, attributes in rows:
        raw = (attributes or {}).get("tool.name") or name or "unknown"
        canon = canonical_tool_name(raw)
        entry = counts.setdefault(canon, {"name": str(raw), "calls": 0, "error_calls": 0})
        entry["calls"] += 1
        if status_code == 2:
            entry["error_calls"] += 1
    ranked = sorted(counts.values(), key=lambda row: row["calls"], reverse=True)
    return ranked[:_TOOL_CAP]


def collect_capability_context(capability) -> dict[str, Any]:
    """The capability-card flow, cumulative OTLP usage counters, root-span aggregates
    and observed tool volume for one capability.
    """
    # Lazy: these pull in Django app machinery that cycles at module load, because
    # settings-time consumers such as finetuning_runner import this package.
    from overbae.models import Span  # noqa: PLC0415
    from overbae.services.codebase.flow import build_capability_flow  # noqa: PLC0415

    flow = build_capability_flow(capability)
    usage = capability.usage_stats or {}

    cutoff = timezone.now() - dt.timedelta(days=_TRACE_WINDOW_DAYS)
    cutoff_ns = int(cutoff.timestamp() * 1e9)
    rows = list(
        Span.objects.filter(
            project_id=capability.project_id,
            capability=capability,
            parent_span_id=None,
            start_time_ns__gte=cutoff_ns,
        ).values_list("duration_ns", "status_code")[:_TRACE_ROW_CAP]
    )
    durations = sorted(d or 0 for d, _s in rows)
    errors = sum(1 for _d, s in rows if s == 2)
    traces: dict[str, Any] = {
        "window_days": _TRACE_WINDOW_DAYS,
        "n": len(rows),
        "errors": errors,
        "error_rate": round(errors / len(rows), 3) if rows else None,
    }
    if durations:
        traces["avg_duration_ms"] = round(sum(durations) / len(durations) / 1e6, 1)
        p95_index = min(len(durations) - 1, -(-95 * len(durations) // 100) - 1)
        traces["p95_duration_ms"] = round(durations[p95_index] / 1e6, 1)

    tools = _tool_call_counts(capability, cutoff_ns)

    models_used = usage.get("models") or {}
    return {
        "capability_id": str(capability.id),
        "name": capability.name,
        "task": flow.get("task") or "",
        "domain": flow.get("domain") or "",
        "modality": flow.get("modality") or "",
        "current_model": flow.get("model") or "",
        "declared_tools": [
            t.get("name") for t in (flow.get("tool_spec") or []) if isinstance(t, dict)
        ],
        "failure_modes": flow.get("failure_modes") or [],
        "success_criteria": flow.get("success_criteria") or [],
        "usage": {
            "llm_calls": usage.get("llm_calls", 0),
            "tool_calls": usage.get("tool_calls", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "models_used": models_used,
        },
        "traces": traces,
        "graph_tools": tools,
    }


__all__ = ["collect_capability_context"]
