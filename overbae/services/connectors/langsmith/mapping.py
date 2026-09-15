"""Turn LangSmith runs into ``ObservationRecord``s.

Parent is ``parent_run_ids[-1]`` (root → direct parent), then ``parent_run_id``,
then dotted_order. ``session_id`` on a run is the tracing-project UUID, not a
conversation — conversation identity is ``thread_id``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from overbae.services.connectors.mapping import SourceConventions
from overbae.services.connectors.records import ObservationRecord
from overbae.services.connectors.schema import CONNECTOR_SOURCE_LANGSMITH

_MAX_STATUS_MESSAGE = 500
_THREAD_KEYS = ("thread_id", "conversation_id")
_USER_KEYS = ("user_id",)

# LangSmith has no capability run type, so the profiler ranks on structure alone.
LANGSMITH = SourceConventions(
    source=CONNECTOR_SOURCE_LANGSMITH,
    span_types={"TOOL": "tool_call", "RETRIEVER": "retrieval", "CHAIN": "workflow"},
    model_call_types=frozenset({"LLM", "EMBEDDING"}),
    metadata_skip=frozenset({"ls_model_name"}),
)


def _first_str(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _iso(value: Any) -> str | None:
    dt = _parse_dt(value)
    return dt.isoformat() if dt else None


def _row_version(end_time: Any) -> str:
    """Use the selected completion time; LangSmith exposes no row revision.

    This only upgrades a pending run when its selected ``end_time`` arrives.
    Other in-place changes have no safe ordering signal and remain idempotent.
    """
    dt = _parse_dt(end_time)
    if dt is None:
        return "0"
    return str(int(dt.timestamp() * 1_000_000))


def _parent_from_dotted_order(dotted: str) -> str | None:
    """Penultimate UUID of ``<start>Z<uuid>.<start>Z<uuid>`` — last is self."""
    if not dotted or "." not in dotted:
        return None
    parts = dotted.split(".")
    if len(parts) < 2:
        return None
    parent_seg = parts[-2]
    z = parent_seg.rfind("Z")
    if z < 0:
        return None
    return parent_seg[z + 1 :] or None


def _parent_id(run: dict[str, Any]) -> str | None:
    ancestors = run.get("parent_run_ids")
    if isinstance(ancestors, list) and ancestors:
        return str(ancestors[-1])
    if run.get("parent_run_id"):
        return str(run["parent_run_id"])
    dotted = run.get("dotted_order")
    return _parent_from_dotted_order(str(dotted)) if dotted else None


def _metadata(run: dict[str, Any]) -> dict[str, Any]:
    md = run.get("metadata")
    if isinstance(md, dict):
        return md
    extra = run.get("extra")
    nested = extra.get("metadata") if isinstance(extra, dict) else None
    return nested if isinstance(nested, dict) else {}


def _model(run: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    if metadata.get("ls_model_name") not in (None, ""):
        return str(metadata["ls_model_name"])
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    params = extra.get("invocation_params")
    if isinstance(params, dict) and params.get("model") not in (None, ""):
        return str(params["model"])
    return None


def _thread_id(run: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    if run.get("thread_id") not in (None, ""):
        return str(run["thread_id"])
    return _first_str(metadata, _THREAD_KEYS)


def _usage_details(run: dict[str, Any]) -> dict[str, Any]:
    usage = {
        "input": run.get("prompt_tokens"),
        "output": run.get("completion_tokens"),
        "total": run.get("total_tokens"),
    }
    return {k: v for k, v in usage.items() if v}


def _error_fields(run: dict[str, Any]) -> tuple[str | None, str]:
    status = str(run.get("status") or "").upper()
    error = run.get("error")
    if status != "ERROR" and not error:
        return None, ""
    if isinstance(error, str):
        message = error
    elif error:
        try:
            message = json.dumps(error, default=str)
        except (TypeError, ValueError):
            message = str(error)
    else:
        message = status
    return "ERROR", message[:_MAX_STATUS_MESSAGE]


def _extra_attrs(run: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if run.get("dotted_order"):
        extra["dotted_order"] = run["dotted_order"]
    if run.get("run_type"):
        extra["run_type"] = run["run_type"]
    if run.get("first_token_time"):
        extra["first_token_time"] = run["first_token_time"]
    return extra


def _record(run: dict[str, Any]) -> ObservationRecord:
    metadata = _metadata(run)
    parent_id = None if run.get("is_root") else _parent_id(run)
    run_id = str(run["id"])
    if parent_id == run_id:
        parent_id = None
    start = _iso(run.get("start_time"))
    end = _iso(run.get("end_time"))
    level, status_message = _error_fields(run)
    return ObservationRecord(
        id=run_id,
        trace_id=str(run.get("trace_id") or run_id),
        parent_observation_id=parent_id,
        type=str(run.get("run_type") or "CHAIN"),
        name=run.get("name"),
        start_time=start,
        end_time=end,
        input=run.get("inputs"),
        output=run.get("outputs"),
        level=level,
        status_message=status_message,
        metadata=metadata,
        session_id=_thread_id(run, metadata),
        user_id=_first_str(metadata, _USER_KEYS),
        model=_model(run, metadata),
        usage_details=_usage_details(run),
        total_cost=run.get("total_cost"),
        tags=[str(t) for t in (run.get("tags") or []) if t],
        is_root_observation=bool(run.get("is_root")) or parent_id is None,
        row_version=_row_version(end),
        extra_attrs=_extra_attrs(run),
    )


def runs_to_records(
    runs: list[dict[str, Any]],
    *,
    credential_id: str | None = None,  # noqa: ARG001 — signature matches Braintrust
    promote_orphans: bool = True,
) -> list[ObservationRecord]:
    """One LangSmith trace's runs as records.

    ``promote_orphans`` is for a fully-collected window whose root is still
    missing. A pagination slice must pass False: promoting mid-walk fabricates a
    fake root whose synthetic version then blocks the real one.
    """
    runs = [r for r in runs if r.get("id")]
    if any(r.get("reference_example_id") for r in runs):
        return []
    if not runs:
        return []

    records = [_record(r) for r in runs]
    present = {r.id for r in records}

    if promote_orphans and not any(r.is_root_observation for r in records):
        for record in records:
            if record.parent_observation_id and record.parent_observation_id not in present:
                record.parent_observation_id = None
        orphans = [r for r in records if r.parent_observation_id is None]
        root = min(orphans or records, key=lambda r: (r.start_time or "", r.id))
        root.is_root_observation = True
        for record in orphans:
            if record.id != root.id:
                record.parent_observation_id = root.id

    root = next((r for r in records if r.is_root_observation), None)
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
    return getattr(root, field, None) or next(
        (value for r in records if (value := getattr(r, field))), None
    )


def group_runs_by_trace(runs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        if not run.get("id"):
            continue
        groups.setdefault(str(run.get("trace_id") or run["id"]), []).append(run)
    return groups
