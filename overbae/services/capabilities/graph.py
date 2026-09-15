"""Read-side helpers for the agent graph: edge weighting from runtime traffic
and the last scan's report."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from overbae.models import Span

_TRANSITION_SAMPLE = 5000


def observed_transitions(project_id: str) -> dict[tuple[str, str], int]:
    """How often one trace moves from capability A to capability B: consecutive
    spans of one trace attributed to different capabilities. Weights the static
    edges; never creates a node."""
    rows = (
        Span.objects.filter(project_id=project_id, capability__isnull=False)
        .order_by("trace_id", "start_time_ns")
        .values_list("trace_id", "capability_id")[:_TRANSITION_SAMPLE]
    )
    counts: dict[tuple[str, str], int] = defaultdict(int)
    prev_trace, prev_capability = None, None
    for trace_id, capability_id in rows:
        if trace_id == prev_trace and prev_capability not in (None, capability_id):
            counts[(str(prev_capability), str(capability_id))] += 1
        prev_trace, prev_capability = trace_id, capability_id
    return dict(counts)


def weighted_edges(project_id: str, edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observed = observed_transitions(project_id)
    out = []
    for edge in edges:
        row = dict(edge)
        if row.get("kind") == "invokes":
            row["observed"] = observed.get((str(row.get("source")), str(row.get("target"))), 0)
        out.append(row)
    return out
