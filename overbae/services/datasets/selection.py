"""The trace source of a dataset: explicit trace ids, or the predicate the traces
list showed. One boundary for REST, MCP and the landing task: the payload is
parsed once, unknown filters are refused instead of silently widening the
selection, and the selection is counted before a dataset is created."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from overbae.models.traces import Span
from overbae.services import trace_selection

MAX_TRACE_IDS = 10_000
# Not selection predicates: paging, the span-level toggle, and ids that name one span.
_NOT_SELECTION = frozenset({"all_spans", "page", "page_size", "span_id", "project_id"})


class TraceSourceError(ValueError):
    pass


def allowed_filters() -> tuple[str, ...]:
    """The traces-list filters a selection may carry, in schema order."""
    from overbae.api.filters import SpanFilter  # noqa: PLC0415 — avoids an app-loading cycle

    return tuple(k for k in SpanFilter.base_filters if k not in _NOT_SELECTION)


def _clean_ids(values: Any, *, what: str) -> tuple[str, ...]:
    if values in (None, ""):
        return ()
    if not isinstance(values, (list, tuple)):
        raise TraceSourceError(f"{what} must be a list of trace ids.")
    seen: dict[str, None] = {}
    for value in values:
        text = str(value).strip()
        if text:
            seen.setdefault(text, None)
    if len(seen) > MAX_TRACE_IDS:
        raise TraceSourceError(f"{what} holds {len(seen)} ids; the limit is {MAX_TRACE_IDS}.")
    return tuple(seen)


@dataclass(frozen=True)
class TraceSource:
    trace_ids: tuple[str, ...] = ()
    filters: dict[str, str] = field(default_factory=dict)
    search: str = ""
    ordering: str = ""
    exclude_trace_ids: tuple[str, ...] = ()
    limit: int | None = None

    @classmethod
    def parse(cls, payload: Any) -> TraceSource:
        if not isinstance(payload, Mapping):
            raise TraceSourceError("traces must be an object with trace_ids or filters.")
        trace_ids = _clean_ids(payload.get("trace_ids"), what="trace_ids")
        raw_filters = payload.get("filters") or {}
        if not isinstance(raw_filters, Mapping):
            raise TraceSourceError("filters must be an object.")
        filters = {str(k): str(v) for k, v in raw_filters.items() if v not in (None, "")}
        allowed = allowed_filters()
        unknown = sorted(k for k in filters if k not in allowed)
        if unknown:
            raise TraceSourceError(
                f"Unknown trace filter{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}. "
                f"Allowed: {', '.join(allowed)}."
            )
        search = str(payload.get("search") or "").strip()
        ordering = str(payload.get("ordering") or "").strip()
        limit = payload.get("limit")
        if limit not in (None, ""):
            try:
                limit = int(limit)
            except (TypeError, ValueError) as exc:
                raise TraceSourceError("limit must be a whole number.") from exc
            if limit < 1:
                raise TraceSourceError("limit must be at least 1.")
        else:
            limit = None
        exclude = _clean_ids(payload.get("exclude_trace_ids"), what="exclude_trace_ids")
        if trace_ids and (filters or search):
            raise TraceSourceError("Give either trace_ids or a filter selection, not both.")
        if not trace_ids and not filters and not search:
            raise TraceSourceError("Give trace_ids, or at least one filter or a search.")
        if trace_ids:
            return cls(trace_ids=trace_ids)
        return cls(
            filters=filters,
            search=search,
            ordering=ordering,
            exclude_trace_ids=exclude,
            limit=limit,
        )

    def spec(self) -> dict[str, Any]:
        """The stored form: only what selects."""
        if self.trace_ids:
            return {"trace_ids": list(self.trace_ids)}
        out: dict[str, Any] = {"filters": dict(self.filters)}
        if self.search:
            out["search"] = self.search
        if self.ordering:
            out["ordering"] = self.ordering
        if self.exclude_trace_ids:
            out["exclude_trace_ids"] = list(self.exclude_trace_ids)
        if self.limit:
            out["limit"] = self.limit
        return out

    def _selection(self) -> trace_selection.TraceSelection:
        return trace_selection.TraceSelection(
            filters=dict(self.filters),
            search=self.search,
            ordering=self.ordering,
            exclude_trace_ids=set(self.exclude_trace_ids),
            limit=self.limit,
        )

    def iter_trace_ids(self, project_id: Any) -> Iterator[str]:
        """Selection order: explicit ids as given, else the traces-list order."""
        if self.trace_ids:
            yield from self.trace_ids
            return
        try:
            yield from trace_selection.iter_selection_trace_ids([project_id], self._selection())
        except ValueError as exc:
            raise TraceSourceError(str(exc)) from exc

    def count(self, project_id: Any) -> int:
        """Traces that exist and match, before any row is built."""
        if self.trace_ids:
            total = 0
            ids = list(self.trace_ids)
            for start in range(0, len(ids), trace_selection.ID_CHUNK_SIZE):
                chunk = ids[start : start + trace_selection.ID_CHUNK_SIZE]
                total += (
                    Span.objects.filter(project_id=project_id, trace_id__in=chunk)
                    .values("trace_id")
                    .distinct()
                    .count()
                )
            return total
        try:
            queryset = trace_selection.selection_queryset([project_id], self._selection())
        except ValueError as exc:
            raise TraceSourceError(str(exc)) from exc
        if self.exclude_trace_ids:
            queryset = queryset.exclude(trace_id__in=self.exclude_trace_ids)
        matched = queryset.values("trace_id").distinct().count()
        return min(matched, self.limit) if self.limit else matched
