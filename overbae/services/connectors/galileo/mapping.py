"""Flatten a Galileo trace tree into ``ObservationRecord``s.

``GET /traces/{id}`` nests the whole span tree under ``spans`` — there is no
separate flat observation list, unlike Langfuse/LangSmith/Braintrust. Galileo
also has no end-timestamp field: ``created_at`` plus ``metrics.duration_ns``
stands in for a start/end pair.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from overbae.services.connectors.mapping import SourceConventions
from overbae.services.connectors.records import ObservationRecord
from overbae.services.connectors.schema import CONNECTOR_SOURCE_GALILEO

_MAX_STATUS_MESSAGE = 500

GALILEO = SourceConventions(
    source=CONNECTOR_SOURCE_GALILEO,
    span_types={"TOOL": "tool_call"},
    # Galileo's native agent span is the closest analog to a capability boundary.
    capability_type="AGENT",
    model_call_types=frozenset({"LLM"}),
)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(value: Any) -> str | None:
    dt = _parse_dt(value)
    return dt.isoformat() if dt else None


def _end_time(created_at: Any, metrics: dict[str, Any]) -> str | None:
    start = _parse_dt(created_at)
    if start is None:
        return None
    duration_ns = metrics.get("duration_ns")
    if duration_ns is None:
        return start.isoformat()
    return (start + timedelta(microseconds=float(duration_ns) / 1000)).isoformat()


def _row_version(updated_at: Any) -> str:
    """Async metrics rewrite a row after it lands, so a mutable ``connector.version``
    lets a later re-fetch overwrite it instead of being skipped as stale."""
    dt = _parse_dt(updated_at)
    return str(int(dt.timestamp() * 1_000_000)) if dt else "0"


def _error_fields(node: dict[str, Any]) -> tuple[str | None, str]:
    message = node.get("error_message") or ""
    if message:
        return "ERROR", str(message)[:_MAX_STATUS_MESSAGE]
    status_code = node.get("status_code")
    if isinstance(status_code, int) and status_code >= 400:
        return "ERROR", f"Galileo status code {status_code}"
    return None, ""


def _usage_details(metrics: dict[str, Any]) -> dict[str, Any]:
    usage = {
        "input": metrics.get("num_input_tokens"),
        "output": metrics.get("num_output_tokens"),
        "total": metrics.get("num_total_tokens"),
    }
    return {k: v for k, v in usage.items() if v}


def _flatten(
    node: dict[str, Any], parent_id: str | None, out: list[tuple[dict[str, Any], str | None]]
) -> None:
    node_id = node.get("id")
    if not node_id:
        return
    out.append((node, parent_id))
    for child in node.get("spans") or []:
        if isinstance(child, dict):
            _flatten(child, str(node_id), out)


def _record(node: dict[str, Any], parent_id: str | None, trace_id: str) -> ObservationRecord:
    metrics = node.get("metrics") if isinstance(node.get("metrics"), dict) else {}
    metadata = node.get("user_metadata") if isinstance(node.get("user_metadata"), dict) else {}
    level, status_message = _error_fields(node)
    created_at = node.get("created_at")
    return ObservationRecord(
        id=str(node["id"]),
        trace_id=trace_id,
        parent_observation_id=parent_id,
        type=str(node.get("type") or "trace").upper(),
        name=node.get("name") or None,
        start_time=_iso(created_at),
        end_time=_end_time(created_at, metrics),
        input=node.get("input"),
        output=node.get("output"),
        level=level,
        status_message=status_message,
        metadata=metadata,
        model=node.get("model"),
        usage_details=_usage_details(metrics),
        tags=[str(t) for t in (node.get("tags") or []) if t],
        is_root_observation=parent_id is None,
        row_version=_row_version(node.get("updated_at")),
    )


def tree_to_records(tree: dict[str, Any]) -> list[ObservationRecord]:
    """One Galileo trace (with nested ``spans``) as flat records, root first.

    Returns ``[]`` for a tree with no usable id.
    """
    trace_id = tree.get("id")
    if not trace_id:
        return []
    trace_id = str(trace_id)

    nodes: list[tuple[dict[str, Any], str | None]] = []
    _flatten(tree, None, nodes)
    if not nodes:
        return []

    records = [_record(node, parent_id, trace_id) for node, parent_id in nodes]
    root = records[0]
    # session_id defaults to the trace's own id absent a real multi-turn
    # session, and propagating that would make every trace its own Conversation.
    session_id = str(tree.get("session_id") or "")
    session = session_id if session_id and session_id != trace_id else None
    for record in records:
        record.trace_name = root.name
        record.session_id = session
    return records
