"""Recorded behaviour across a bounded project window for the Console overview."""

from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
from math import ceil, isfinite
from statistics import mean

from django.db.models import Count, Max
from django.utils import timezone

from overbae.api.serializers import (
    _leaf_usage_rows,
    _model_from_span_attributes,
    _total_cost_from_span_attributes,
)
from overbae.innermind.health import SPAN_CAP
from overbae.innermind.metrics import FAIL_BELOW
from overbae.models import SignalEvent, Span


def _p95(values):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[ceil(len(ordered) * 0.95) - 1], 1)


def _score(value):
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
        else None
    )


def overview(project, days):
    now = timezone.now()
    start = datetime.combine(
        now.astimezone(UTC).date() - timedelta(days=days - 1), time.min, tzinfo=UTC
    )
    queryset = Span.objects.filter(project=project, received_at__gte=start, received_at__lt=now)
    span_count = queryset.count()
    rows = list(
        queryset.order_by("-received_at").values(
            "trace_id",
            "span_id",
            "parent_span_id",
            "conversation_id",
            "capability_id",
            "name",
            "span_type",
            "scope_name",
            "status_code",
            "start_time_ns",
            "end_time_ns",
            "duration_ns",
            "received_at",
            "eval_attributes",
            "usage",
        )[:SPAN_CAP]
    )
    names = dict(project.capabilities.values_list("id", "name"))
    traces = defaultdict(
        lambda: {"capabilities": set(), "error": False, "duration": None, "cost": None}
    )
    days_by_date = defaultdict(
        lambda: {
            "runs": set(),
            "errors": set(),
            "checks": 0,
            "passed": 0,
            "durations": [],
            "costs": [],
        }
    )
    checks = defaultdict(lambda: {"runs": set(), "failures": 0, "errors": 0, "examples": []})
    capability_checks = defaultdict(lambda: {"checks": 0, "failed": 0})
    tools = defaultdict(lambda: {"calls": 0, "errors": 0, "durations": [], "example": ""})
    models = defaultdict(
        lambda: {
            "calls": 0,
            "errors": 0,
            "durations": [],
            "cost": 0.0,
            "cost_calls": 0,
            "example": "",
        }
    )
    sessions = set()
    scored_spans = 0
    final_spans = 0
    latest_received_at = rows[0]["received_at"] if rows else None
    for row in rows:
        trace_id = row["trace_id"]
        trace = traces[trace_id]
        day = row["received_at"].astimezone(UTC).date().isoformat()
        daily = days_by_date[day]
        daily["runs"].add(trace_id)
        if row["conversation_id"]:
            sessions.add(row["conversation_id"])
        if row["capability_id"]:
            trace["capabilities"].add(row["capability_id"])
        if row["status_code"] == 2:
            trace["error"] = True
            daily["errors"].add(trace_id)
        if row["parent_span_id"] is None and row["duration_ns"] > 0:
            duration = row["duration_ns"] / 1_000_000
            trace["duration"] = duration
            daily["durations"].append(duration)
        attrs = row["eval_attributes"] or {}
        if "score" in attrs:
            final_spans += 1
            if _score(attrs["score"]) is not None:
                scored_spans += 1
        for name, metric in (attrs.get("metrics") or {}).items():
            if not isinstance(metric, dict):
                continue
            score = _score(metric.get("score"))
            if score is not None:
                daily["checks"] += 1
                daily["passed"] += score >= FAIL_BELOW
                if row["capability_id"]:
                    capability_checks[row["capability_id"]]["checks"] += 1
                    capability_checks[row["capability_id"]]["failed"] += score < FAIL_BELOW
                if score < FAIL_BELOW:
                    item = checks[name]
                    item["runs"].add(trace_id)
                    item["failures"] += 1
                    if trace_id not in item["examples"] and len(item["examples"]) < 3:
                        item["examples"].append(trace_id)
            elif metric.get("error"):
                checks[name]["errors"] += 1
        if row["span_type"] in ("tool_call", "retrieval", "tool"):
            tool = tools[row["name"] or "Unnamed tool"]
            tool["calls"] += 1
            tool["errors"] += row["status_code"] == 2
            tool["example"] = tool["example"] or trace_id
            if row["duration_ns"] > 0:
                tool["durations"].append(row["duration_ns"] / 1_000_000)
        if row["span_type"] == "llm_call":
            model_name = _model_from_span_attributes(row["usage"])
            if model_name:
                model = models[model_name]
                model["calls"] += 1
                model["errors"] += row["status_code"] == 2
                model["example"] = model["example"] or trace_id
                if row["duration_ns"] > 0:
                    model["durations"].append(row["duration_ns"] / 1_000_000)

    usage_rows = [
        (
            r["trace_id"],
            r["span_id"],
            r["parent_span_id"],
            r["trace_id"],
            r["scope_name"],
            r["start_time_ns"],
            r["end_time_ns"],
            r["usage"],
        )
        for r in rows
        if r["usage"]
    ]
    for trace_id, attrs in _leaf_usage_rows(usage_rows):
        cost = _total_cost_from_span_attributes(attrs)
        if cost is None:
            continue
        trace = traces[trace_id]
        trace["cost"] = (trace["cost"] or 0) + cost
        model_name = _model_from_span_attributes(attrs)
        if model_name:
            models[model_name]["cost"] += cost
            models[model_name]["cost_calls"] += 1

    # Attribute a trace's cost to its latest received day; do not count partial
    # span costs as separate runs on every day they happened to be received.
    trace_days = {
        r["trace_id"]: r["received_at"].astimezone(UTC).date().isoformat() for r in reversed(rows)
    }
    for trace_id, trace in traces.items():
        if trace["cost"] is not None:
            days_by_date[trace_days[trace_id]]["costs"].append(trace["cost"])

    signal_query = SignalEvent.objects.filter(
        project=project, occurred_at__gte=start, occurred_at__lt=now
    )
    signal_groups = list(
        signal_query.values("signal_type")
        .annotate(events=Count("id"), last_seen=Max("occurred_at"))
        .order_by("-events")[:5]
    )
    signal_examples = {
        group["signal_type"]: signal_query.filter(signal_type=group["signal_type"])
        .exclude(trace_id="")
        .order_by("-occurred_at")
        .values_list("trace_id", flat=True)
        .first()
        for group in signal_groups
    }
    issues = [
        {
            "kind": "check",
            "name": name,
            "affected_runs": len(item["runs"]),
            "occurrences": item["failures"],
            "errors": item["errors"],
            "examples": item["examples"],
        }
        for name, item in checks.items()
        if item["failures"] or item["errors"]
    ]
    issues.sort(key=lambda issue: (-issue["affected_runs"], -issue["occurrences"]))
    issues = issues[:5] + [
        {
            "kind": "signal",
            "name": row["signal_type"],
            "affected_runs": None,
            "occurrences": row["events"],
            "errors": 0,
            "examples": [signal_examples[row["signal_type"]]]
            if signal_examples[row["signal_type"]]
            else [],
        }
        for row in signal_groups
    ]
    capability_totals = defaultdict(lambda: {"runs": 0, "errors": 0})
    for trace in traces.values():
        for capability_id in trace["capabilities"]:
            capability_totals[capability_id]["runs"] += 1
            capability_totals[capability_id]["errors"] += trace["error"]
    capability_rows = [
        {
            "id": capability_id,
            "name": names.get(capability_id, "Unknown capability"),
            "runs": totals["runs"],
            "error_runs": totals["errors"],
            "checks": capability_checks[capability_id]["checks"],
            "failed_checks": capability_checks[capability_id]["failed"],
        }
        for capability_id, totals in capability_totals.items()
    ]
    capability_rows.sort(key=lambda row: -row["runs"])
    dates = [
        (now.astimezone(UTC).date() - timedelta(days=i)).isoformat()
        for i in range(days - 1, -1, -1)
    ]
    daily_rows = []
    for date in dates:
        item = days_by_date[date]
        daily_rows.append(
            {
                "day": date,
                "runs": len(item["runs"]),
                "error_runs": len(item["errors"]),
                "checks": item["checks"],
                "passed_checks": item["passed"],
                "p95_duration_ms": _p95(item["durations"]),
                "average_cost_usd": round(mean(item["costs"]), 6) if item["costs"] else None,
            }
        )
    all_durations = [
        trace["duration"] for trace in traces.values() if trace["duration"] is not None
    ]
    all_costs = [trace["cost"] for trace in traces.values() if trace["cost"] is not None]
    passed = sum(item["passed"] for item in days_by_date.values())
    check_count = sum(item["checks"] for item in days_by_date.values())
    return {
        "window_days": days,
        "span_count": span_count,
        "sampled_spans": len(rows),
        "truncated": span_count > len(rows),
        "latest_received_at": latest_received_at,
        "runs": len(traces),
        "sessions": len(sessions),
        "error_runs": sum(t["error"] for t in traces.values()),
        "checks": check_count,
        "failed_checks": check_count - passed,
        "check_pass_rate": round(passed / check_count, 3) if check_count else None,
        "scored_spans": scored_spans,
        "final_spans": final_spans,
        "p95_duration_ms": _p95(all_durations),
        "measured_duration_runs": len(all_durations),
        "average_cost_usd": round(mean(all_costs), 6) if all_costs else None,
        "measured_cost_runs": len(all_costs),
        "daily": daily_rows,
        "issues": issues,
        "capabilities": capability_rows,
        "models": sorted(
            (
                {
                    "name": name,
                    "calls": item["calls"],
                    "error_calls": item["errors"],
                    "average_duration_ms": round(mean(item["durations"]), 1)
                    if item["durations"]
                    else None,
                    "recorded_cost_usd": round(item["cost"], 6) if item["cost_calls"] else None,
                    "example_trace_id": item["example"],
                }
                for name, item in models.items()
            ),
            key=lambda row: -row["calls"],
        )[:8],
        "tools": sorted(
            (
                {
                    "name": name,
                    "calls": item["calls"],
                    "error_calls": item["errors"],
                    "average_duration_ms": round(mean(item["durations"]), 1)
                    if item["durations"]
                    else None,
                    "example_trace_id": item["example"],
                }
                for name, item in tools.items()
            ),
            key=lambda row: -row["calls"],
        )[:8],
    }
