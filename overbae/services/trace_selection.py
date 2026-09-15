"""Resolve a *selection of traces* server-side, from the predicate the user
actually expressed in the traces list (filters + search + ordering).

``TraceSelection`` carries the same parameters ``GET /api/traces/`` takes, plus
the (small) set of traces the user unticked, and resolves against the SAME
``SpanFilter`` the list view uses — so what the wizard ingests is exactly what
the list showed. Explicit id lists remain the other, equally valid source.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from django.db.models import Q

from overbae.models.traces import Span

# Bounds the ids held per step, not the selection itself: callers stream.
ID_CHUNK_SIZE = 2_000

# Mirrors ``SpanViewSet.search_fields`` — a selection must resolve to the same
# rows the list view showed for the same query.
SEARCH_FIELDS = ("name", "service_name", "trace_id", "span_id")

DEFAULT_ORDERING = "-start_time_ns"

# The analyze response carries one entry per analyzed trace, so this cap is what
# keeps it fixed-size instead of linear in the selection.
# ``all_spans`` is never honoured from a selection: a dataset takes one
# candidate row per trace, so resolution is always root-spans-only.
_IGNORED_FILTERS = frozenset({"all_spans", "page", "page_size", "search", "ordering"})


@dataclass
class TraceSelection:
    filters: dict[str, Any] = field(default_factory=dict)
    search: str = ""
    ordering: str = ""
    exclude_trace_ids: set[str] = field(default_factory=set)
    # User-facing ceiling ("take the first N matching"), not a safety limit.
    limit: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TraceSelection:
        raw_filters = payload.get("filters") or {}
        return cls(
            filters={
                k: v
                for k, v in raw_filters.items()
                if k not in _IGNORED_FILTERS and v not in (None, "")
            },
            search=(payload.get("search") or "").strip(),
            ordering=(payload.get("ordering") or "").strip(),
            exclude_trace_ids={
                str(t).strip() for t in (payload.get("exclude_trace_ids") or []) if str(t).strip()
            },
            limit=payload.get("limit"),
        )


def _base_queryset(project_ids) -> Any:
    """The same head spans the traces list shows: the root once it arrived, else
    the earliest span, so an interrupted run is selectable too."""
    return Span.trace_heads(Span.objects.all(), project_ids)


def _apply_search(queryset, search: str):
    """DRF ``SearchFilter`` semantics: terms AND together, fields OR within a term."""
    for term in search.split():
        clause = Q()
        for field_name in SEARCH_FIELDS:
            clause |= Q(**{f"{field_name}__icontains": term})
        queryset = queryset.filter(clause)
    return queryset


def selection_queryset(project_ids, selection: TraceSelection):
    """Root spans matching *selection*, through the list view's own ``SpanFilter``."""
    from overbae.api.filters import SpanFilter  # noqa: PLC0415 — avoids an app-loading cycle
    from overbae.api.span_ordering import annotate_spans_for_ordering  # noqa: PLC0415

    queryset = _base_queryset(project_ids)
    ordering = selection.ordering or DEFAULT_ORDERING
    queryset = annotate_spans_for_ordering(queryset, ordering, all_spans=False)

    filterset = SpanFilter(data=selection.filters, queryset=queryset)
    # An unparseable filter must not silently widen the selection to every trace.
    if not filterset.is_valid():
        raise ValueError(f"Invalid trace filter: {filterset.errors.as_json()}")
    queryset = filterset.qs

    if selection.search:
        queryset = _apply_search(queryset, selection.search)
    return queryset.order_by(ordering)


def iter_selection_trace_ids(project_ids, selection: TraceSelection) -> Iterator[str]:
    """Stream the selected trace ids in list order, deduped, exclusions applied."""
    queryset = selection_queryset(project_ids, selection)
    seen: set[str] = set(selection.exclude_trace_ids)
    yielded = 0
    for trace_id in queryset.values_list("trace_id", flat=True).iterator(chunk_size=ID_CHUNK_SIZE):
        if not trace_id or trace_id in seen:
            continue
        seen.add(trace_id)
        yield trace_id
        yielded += 1
        if selection.limit and yielded >= selection.limit:
            return
