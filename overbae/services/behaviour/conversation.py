"""Conversation-turn payload assembly for the executions surface.

One query for the executions plus one bulk span fetch replaces the frontend's
per-turn detail + full-trace fetches; the wire-format sniffing (ChatML roles,
tool-call payloads) lives here with the formats.
"""

from __future__ import annotations

import json
from typing import Any

from overbae.models import Span, TaskExecution
from overbae.models.traces import is_tool_operation
from overbae.services.behaviour.binder import anchor_matches

# Turn ask/delivery previews; full payloads stay on the trace view.
TURN_IO_CAP = 8000
_TOOL_IO_CAP = 4000

# Dispatch wrappers, not user-meaningful activities.
_SKIP_TOOLS = frozenset({"iter_turn", "execute_tool"})

_INPUT_ATTR_KEYS = (
    "overmind.input_data",
    "overmind.input.data",
    "inputs",
    "traceloop.entity.input",
)
_OUTPUT_ATTR_KEYS = (
    "overmind.output_data",
    "overmind.output.data",
    "outputs",
    "traceloop.entity.output",
)


def _maybe_parse(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in '{["':
        return value
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return value


def _cap(text: str, limit: int = TURN_IO_CAP) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _message_content(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        return "\n".join(filter(None, (_message_content(v) for v in value))).strip()
    if isinstance(value, dict):
        for key in ("text", "content"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
    return ""


def _last_role_text(value: Any, role: str) -> str:
    messages = value if isinstance(value, list) else None
    if messages is None and isinstance(value, dict) and isinstance(value.get("messages"), list):
        messages = value["messages"]
    for item in reversed(messages or []):
        if isinstance(item, dict) and item.get("role") == role:
            text = _message_content(item.get("content"))
            if text:
                return text
    return ""


def _unwrap_io_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in ("user_text", "text", "content", "final_output", "output", "prompt"):
        text = _message_content(value.get(key))
        if text:
            return text
    return ""


def _fallback_payload(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = {k: v for k, v in value.items() if v not in (None, "")}
        if not value:
            return ""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def normalize_intent(raw: Any) -> dict[str, str] | None:
    """``{text, source, current?}`` — the conversation-running intent displays,
    with this turn's own ask carried as ``current`` when it differs."""
    if not isinstance(raw, dict):
        return None
    current = str(raw.get("text") or "").strip()
    running = str(raw.get("running") or "").strip()
    display = running or current
    if not display:
        return None
    intent = {"text": display, "source": str(raw.get("source") or "")}
    if running and current and running != current:
        intent["current"] = current
    return intent


def turn_input_text(intent: dict[str, str] | None, root_inputs: Any) -> str:
    """This turn's ask. Never conversation_so_far — that is prior turns only."""
    ask = ((intent or {}).get("current") or (intent or {}).get("text") or "").strip()
    if ask:
        return _cap(ask)
    parsed = _maybe_parse(root_inputs)
    return _cap(
        _last_role_text(parsed, "user") or _unwrap_io_text(parsed) or _fallback_payload(parsed)
    )


def turn_output_text(root_outputs: Any) -> str:
    """This turn's delivery from the root span."""
    parsed = _maybe_parse(root_outputs)
    if isinstance(parsed, str):
        return _cap(parsed.strip())
    return _cap(
        _last_role_text(parsed, "assistant") or _unwrap_io_text(parsed) or _fallback_payload(parsed)
    )


def task_state(route: Any) -> dict[str, Any] | None:
    raw = route.get("task_state") if isinstance(route, dict) else None
    if not isinstance(raw, dict):
        return None
    status = str(raw.get("status") or "").strip()
    if not status:
        return None

    def _asks(value: Any) -> list[str]:
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

    return {
        "status": status,
        "outstanding_asks": _asks(raw.get("outstanding_asks")),
        "asked": _asks(raw.get("asked")),
        "reason": str(raw.get("reason") or ""),
        "delivered_wrong": raw.get("delivered_wrong") is True,
    }


def is_displayed_tool(name: str) -> bool:
    trimmed = name.strip()
    return bool(trimmed) and "." not in trimmed and trimmed not in _SKIP_TOOLS


def occupancy_tools(route: Any) -> list[str]:
    occ = route.get("cluster_occupancy") if isinstance(route, dict) else None
    if not isinstance(occ, dict):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for tools in occ.values():
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if isinstance(tool, str) and is_displayed_tool(tool) and tool not in seen:
                seen.add(tool)
                names.append(tool)
    return names


def _humanize(name: str) -> str:
    words = name.replace("_", " ").replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else name


def _short_arg(value: Any) -> str:
    text = str(value or "").strip()
    return text if len(text) <= 8 else text[:8] + "…"


def tool_activity_title(name: str, args: dict[str, Any] | None = None) -> str:
    if not args:
        return _humanize(name)
    capability = str(args.get("capability") or "").strip()
    if name == "get_trace":
        trace = _short_arg(args.get("trace_id"))
        return f"Read trace {trace}" if trace else "Read trace"
    if name == "query_failures":
        return f"Query failures for {capability or 'capability'}"
    if name == "list_traces":
        return "Read trace list"
    if name == "list_capabilities":
        return "List project capabilities"
    if name == "list_datasets":
        return "List project datasets"
    if name == "list_eval_runs":
        return "List eval runs"
    if name == "get_eval_run":
        run = str(args.get("eval_run_name") or args.get("eval_run_id") or "").strip() or "run"
        return f"Load eval run {run}"
    if name == "contract_drift":
        return f"Check contract drift for {capability or 'capability'}"
    if name == "tool_stats":
        return f"Read tool stats for {args.get('tool') or 'tools'}"
    return _humanize(name)


def _scored(steps: list[Any]) -> list[dict[str, Any]]:
    return [
        s
        for s in steps
        if isinstance(s, dict)
        and s.get("outcome") == "scored"
        and (s.get("score") is not None or s.get("passed") is not None)
    ]


def execution_flow(route: Any, step_results: Any, terminal_kind: str) -> dict[str, Any]:
    """Linear chain of the steps actually taken: observed anchors, each carrying
    the step verdict whose segment ends on it, then the terminal with the
    outcome verdict."""
    route = route if isinstance(route, dict) else {}
    anchors = [a for a in (route.get("anchors") or []) if isinstance(a, str)]
    matched = [a for a in (route.get("matched_anchors") or []) if isinstance(a, str)]
    verdicts = _scored(step_results if isinstance(step_results, list) else [])
    step_verdicts = [v for v in verdicts if v.get("role") == "step"]
    used: set[int] = set()

    steps = []
    for anchor in anchors:
        verdict = None
        for i, v in enumerate(step_verdicts):
            if i in used:
                continue
            segment = v.get("segment") if isinstance(v.get("segment"), list) else []
            last = segment[-1] if segment else None
            if isinstance(last, str) and anchor_matches(last, anchor):
                used.add(i)
                verdict = v
                break
        steps.append(
            {
                "anchor": anchor,
                "matched": any(anchor_matches(m, anchor) for m in matched),
                "verdict": verdict,
            }
        )
    outcome = next((v for v in verdicts if v.get("role") == "outcome"), None)
    return {"steps": steps, "terminal": {"kind": terminal_kind or "", "verdict": outcome}}


def _attr_payload(attrs: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = attrs.get(key)
        if value not in (None, ""):
            return value
    return None


def _tool_name(span: Span) -> str:
    attr = (span.attributes or {}).get("tool.name")
    if isinstance(attr, str) and attr.strip():
        return attr.strip()
    return (span.name or "").split(".")[-1] or (span.name or "")


def _is_tool_span(span: Span) -> bool:
    return is_tool_operation(span.span_type) or isinstance(
        (span.attributes or {}).get("tool.name"), str
    )


def _tool_entries(spans: list[Span], route: Any) -> list[dict[str, Any]]:
    occ = set(occupancy_tools(route))
    entries: list[dict[str, Any]] = []
    for span in spans:
        if not _is_tool_span(span):
            continue
        name = _tool_name(span)
        if not is_displayed_tool(name) or (occ and name not in occ):
            continue
        attrs = span.attributes or {}
        args = _maybe_parse(_attr_payload(attrs, _INPUT_ATTR_KEYS))
        failed = span.status_code == 2 or attrs.get("overmind.status") == "failed"
        entries.append(
            {
                "id": span.span_id,
                "name": name,
                "title": tool_activity_title(name, args if isinstance(args, dict) else None),
                "failed": failed,
                "input": _cap(_fallback_payload(args), _TOOL_IO_CAP),
                "output": _cap(
                    _fallback_payload(_maybe_parse(_attr_payload(attrs, _OUTPUT_ATTR_KEYS))),
                    _TOOL_IO_CAP,
                ),
            }
        )
    if entries:
        return entries
    return [
        {
            "id": name,
            "name": name,
            "title": tool_activity_title(name),
            "failed": False,
            "input": "",
            "output": "",
        }
        for name in occupancy_tools(route)
    ]


def conversation_turns(
    executions: list[TaskExecution], project_ids: list[str]
) -> list[dict[str, Any]]:
    """Per-turn payload for *executions* (already scoped and ordered): intent,
    ask/delivery text, output kind, task state, tool activities, and the
    anchor-paired flow — everything the thread view renders."""
    trace_ids = {row.trace_id for row in executions if row.trace_id}
    spans_by_trace: dict[str, list[Span]] = {tid: [] for tid in trace_ids}
    if trace_ids:
        span_rows = Span.objects.filter(
            project_id__in=project_ids, trace_id__in=trace_ids
        ).order_by("start_time_ns")
        for span in span_rows:
            spans_by_trace.setdefault(span.trace_id, []).append(span)

    turns: list[dict[str, Any]] = []
    for row in executions:
        spans = spans_by_trace.get(row.trace_id, [])
        root = next(
            (s for s in spans if s.span_id == row.unit_span_id),
            next((s for s in spans if not s.parent_span_id), None),
        )
        root_attrs = (root.attributes or {}) if root is not None else {}
        intent = normalize_intent(row.user_intent)
        output_text = turn_output_text(_attr_payload(root_attrs, _OUTPUT_ATTR_KEYS))
        turns.append(
            {
                "id": str(row.id),
                "trace_id": row.trace_id,
                "conversation_id": row.conversation_id,
                "capability": str(row.capability_id) if row.capability_id else None,
                "status": row.status,
                "started_at": row.started_at,
                "success_score": row.success_score,
                "session_score": row.session_score,
                "session_rationale": row.session_rationale,
                "intent": intent,
                "input_text": turn_input_text(intent, _attr_payload(root_attrs, _INPUT_ATTR_KEYS)),
                "output_text": output_text,
                "task_state": task_state(row.observed_route),
                "step_results": row.step_results or [],
                "tools": _tool_entries(spans, row.observed_route),
            }
        )
    return turns
