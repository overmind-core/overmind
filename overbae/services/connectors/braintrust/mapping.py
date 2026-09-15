"""Turn Braintrust BTQL rows into ``ObservationRecord``s.

The identity rules are the sharp edge here, and Braintrust's own docs are
explicit about them:

- ``id`` identifies the span. ``root_span_id`` identifies the trace.
- ``span_id`` is an internal tree-construction field, and ``span_parents``
  holds ``span_id`` values, not ``id`` values. Some ingestion paths (OTel
  collectors) set ``span_id == id`` and some do not, so parents are resolved
  through a ``span_id -> row`` index and then read back as that row's ``id``.

Spans are mapped onto the shared Overmind span dicts by
``connectors.mapping.observations_to_span_dicts``; there is no second span
mapping.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from overbae.services.connectors.mapping import SourceConventions
from overbae.services.connectors.records import ObservationRecord
from overbae.services.connectors.schema import CONNECTOR_SOURCE_BRAINTRUST

logger = logging.getLogger(__name__)

_MAX_STATUS_MESSAGE = 500
# Metrics that already have a home on the record; the rest ride along as attributes.
_MAPPED_METRICS = frozenset(
    {
        "start",
        "end",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "tokens",
        "estimated_cost",  # taken from the selected estimated_cost() column instead
    }
)

# Braintrust span_attributes.type -> Overmind span_type. Anything absent is an
# LLM call; only the tool-shaped types need saying. Braintrust has no capability type,
# so capability_type stays empty and the profiler ranks on structure alone.
BRAINTRUST = SourceConventions(
    source=CONNECTOR_SOURCE_BRAINTRUST,
    span_types={"FUNCTION": "tool_call", "TOOL": "tool_call"},
    model_call_types=frozenset({"LLM"}),
    # model is already stamped as the gen_ai usage attribute.
    metadata_skip=frozenset({"model"}),
)


# Braintrust has no session or user column: both are metadata by convention. Its
# own log viewer groups related traces on metadata.conversation_id by default,
# and its docs filter and index on metadata.user_id / metadata.session_id.
#
_SESSION_KEYS = ("conversation_id", "session_id", "thread_id")
_USER_KEYS = ("user_id",)


# Online scoring writes its results into the logs as children of the span they
# scored. That is Braintrust's own machinery, not the traced application.
_SCORER_TYPES = frozenset({"SCORE", "CLASSIFIER"})


def _scorer_row_ids(rows: list[dict[str, Any]]) -> set[str]:
    """Ids of scorer output and everything beneath it.

    The subtree matters: an LLM-as-a-judge scorer logs its judging call as a
    child of the score span, so dropping the score alone would leave the judge's
    model call reparented onto the application trace.
    """
    children: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for parent in row.get("span_parents") or []:
            children.setdefault(str(parent), []).append(row)

    dropped: set[str] = set()
    stack = [
        row
        for row in rows
        if str((row.get("span_attributes") or {}).get("type") or "").upper() in _SCORER_TYPES
    ]
    while stack:
        row = stack.pop()
        row_id = str(row["id"])
        if row_id in dropped:
            continue
        dropped.add(row_id)
        stack.extend(children.get(str(row.get("span_id")), []))
    return dropped


def _first_str(metadata: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _iso(epoch_seconds: Any) -> str | None:
    """Braintrust ``metrics.start``/``end`` are float Unix seconds."""
    try:
        return datetime.fromtimestamp(float(epoch_seconds), UTC).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _parent_span_id(row: dict[str, Any], credential_id: str | None) -> str | None:
    """The single parent edge, taken from the sorted ``span_parents`` array.

    Braintrust's schema allows a DAG but every parent-setting API in their SDK is
    singular and their UI renders one root, so no producer of multiple parents is
    known. Sorting keeps the pick stable across imports if one ever appears —
    array order is not documented as stable, and an unstable pick would flip
    which capability claims the span.
    """
    parents = row.get("span_parents")
    if not isinstance(parents, list):
        return None
    values = sorted(str(p) for p in parents if p)
    if not values:
        return None
    if len(values) > 1:
        logger.info(
            "braintrust span %s has %d parents (credential %s)",
            row.get("id"),
            len(values),
            credential_id,
        )
    return values[0]


def _usage_details(metrics: dict[str, Any]) -> dict[str, Any]:
    """Token counts keyed the way the shared mapping reads them.

    Braintrust publishes both ``tokens`` and ``total_tokens``; a zero is not a
    measurement worth stamping on every span, so falsy values are dropped.
    """
    usage = {
        "input": metrics.get("prompt_tokens"),
        "output": metrics.get("completion_tokens"),
        "total": metrics.get("total_tokens") or metrics.get("tokens"),
    }
    return {k: v for k, v in usage.items() if v}


def _extra_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Braintrust's other numbers: cached and reasoning tokens, time to first token.

    Zeroes are dropped because Braintrust zero-fills every optional counter on
    every LLM span, which would otherwise be a dozen empty attributes apiece.
    """
    return {
        f"metrics.{key}": value
        for key, value in metrics.items()
        if key not in _MAPPED_METRICS and isinstance(value, (int, float)) and value
    }


def _error_fields(error: Any) -> tuple[str | None, str]:
    """Braintrust ``error`` is arbitrary JSON, not a string."""
    if error is None:
        return None, ""
    if isinstance(error, str):
        message = error
    else:
        try:
            message = json.dumps(error, default=str)
        except (TypeError, ValueError):
            message = str(error)
    return "ERROR", message[:_MAX_STATUS_MESSAGE]


def _record(row: dict[str, Any], parent_observation_id: str | None) -> ObservationRecord:
    attributes = row.get("span_attributes") if isinstance(row.get("span_attributes"), dict) else {}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    level, status_message = _error_fields(row.get("error"))
    xact_id = row.get("_xact_id")

    extra: dict[str, Any] = {"span_id": row.get("span_id"), **_extra_metrics(metrics)}
    if isinstance(row.get("span_parents"), list):
        extra["span_parents"] = row["span_parents"]
    if isinstance(row.get("scores"), dict) and row["scores"]:
        extra["scores"] = row["scores"]

    return ObservationRecord(
        id=str(row["id"]),
        trace_id=str(row.get("root_span_id") or row["id"]),
        parent_observation_id=parent_observation_id,
        type=str(attributes.get("type") or "SPAN"),
        name=attributes.get("name"),
        start_time=_iso(metrics.get("start")) or _created_iso(row),
        end_time=_iso(metrics.get("end")),
        input=row.get("input"),
        output=row.get("output"),
        level=level,
        status_message=status_message,
        metadata=metadata,
        session_id=_first_str(metadata, _SESSION_KEYS),
        user_id=_first_str(metadata, _USER_KEYS),
        model=metadata.get("model"),
        usage_details=_usage_details(metrics),
        total_cost=row.get("estimated_cost"),
        tags=[str(t) for t in (row.get("tags") or []) if t],
        is_root_observation=bool(row.get("is_root")),
        row_version=str(xact_id) if xact_id is not None else None,
        extra_attrs={k: v for k, v in extra.items() if v is not None},
    )


def _created_iso(row: dict[str, Any]) -> str | None:
    created = row.get("created")
    return str(created) if created else None


def rows_to_records(
    rows: list[dict[str, Any]],
    *,
    credential_id: str | None = None,
) -> list[ObservationRecord]:
    """One Braintrust trace's rows as records, with the tree rooted.

    Braintrust's logs table only shows traces whose root span is present and
    warns that sending only child spans hides them, so root-less groups exist in
    storage. Rather than drop the trace, the earliest span becomes its root and
    adopts the other orphans.
    """
    rows = [r for r in rows if r.get("id")]
    scorer_ids = _scorer_row_ids(rows)
    if scorer_ids:
        rows = [r for r in rows if str(r["id"]) not in scorer_ids]
    if not rows:
        return []

    # span_parents holds span_id values; first writer wins on a duplicate.
    by_span_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        span_id = row.get("span_id")
        if span_id:
            by_span_id.setdefault(str(span_id), row)

    records: list[ObservationRecord] = []
    for row in rows:
        parent_row = by_span_id.get(_parent_span_id(row, credential_id) or "")
        parent_id = str(parent_row["id"]) if parent_row is not None else None
        # A row whose span_parents point at itself would otherwise self-parent.
        records.append(_record(row, None if parent_id == str(row["id"]) else parent_id))

    if not any(r.is_root_observation for r in records):
        orphans = [r for r in records if r.parent_observation_id is None]
        root = min(orphans or records, key=lambda r: (r.start_time or "", r.id))
        root.is_root_observation = True
        for record in orphans:
            if record.id != root.id:
                record.parent_observation_id = root.id

    root = next((r for r in records if r.is_root_observation), None)
    # Session and user identify the whole run, but Braintrust metadata is per-span
    # and apps usually log them only on the root, so they are carried down the
    # trace. Otherwise a conversation would hold nothing but root spans.
    session = _trace_wide(records, root, "session_id")
    user = _trace_wide(records, root, "user_id")
    for record in records:
        record.trace_name = root.name if root else None
        record.session_id = session
        record.user_id = user
    return records


def _trace_wide(
    records: list[ObservationRecord], root: ObservationRecord | None, field: str
) -> str | None:
    """The trace's value for *field*: the root's, or the first span that has one."""
    return getattr(root, field, None) or next(
        (value for r in records if (value := getattr(r, field))), None
    )


def group_rows_by_trace(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Rows keyed by ``root_span_id`` — the trace identifier."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not row.get("id"):
            continue
        groups.setdefault(str(row.get("root_span_id") or row["id"]), []).append(row)
    return groups
