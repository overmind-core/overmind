"""Aggregated product usage insights from the configured PostHog project."""

from __future__ import annotations

from datetime import date
from typing import Any

import requests
from django.conf import settings

EVENT_METRICS = {
    "cli.completed": "cli_completions",
    "$mcp_tool_call": "mcp_tool_calls",
    "training_result_available": "training_results_available",
    "training_check_finished": "training_checks_finished",
}
ACTIVE_USER_EVENTS = ("cli.completed", "$mcp_tool_call")
TIMEOUT_SECONDS = 8


def _query(hogql: str) -> list[list[Any]]:
    response = requests.post(
        f"{settings.POSTHOG_API_HOST}/api/projects/{settings.POSTHOG_PROJECT_ID}/query/",
        headers={"Authorization": f"Bearer {settings.POSTHOG_PERSONAL_API_KEY}"},
        json={"query": {"kind": "HogQLQuery", "query": hogql}},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["results"]


def product_usage(days: int) -> dict[str, Any]:
    """Return daily aggregate event counts; never return event or person rows."""
    if not settings.POSTHOG_PERSONAL_API_KEY or not settings.POSTHOG_PROJECT_ID:
        return {"available": False, "reason": "PostHog read access is not configured."}

    event_list = ", ".join(f"'{event}'" for event in EVENT_METRICS)
    try:
        rows = _query(
            "SELECT toDate(timestamp), event, count() "
            "FROM events "
            f"WHERE timestamp >= now() - INTERVAL {days} DAY AND event IN ({event_list}) "
            "GROUP BY toDate(timestamp), event ORDER BY toDate(timestamp)"
        )
        active_event_list = ", ".join(f"'{event}'" for event in ACTIVE_USER_EVENTS)
        active_users = _query(
            "SELECT uniq(person_id) "
            "FROM events "
            f"WHERE timestamp >= now() - INTERVAL {days} DAY "
            f"AND event IN ({active_event_list})"
        )
        top_tools = _query(
            "SELECT properties.$mcp_tool_name, count(), uniq(person_id) "
            "FROM events "
            f"WHERE timestamp >= now() - INTERVAL {days} DAY "
            "AND event = '$mcp_tool_call' "
            "GROUP BY properties.$mcp_tool_name ORDER BY count() DESC LIMIT 8"
        )
        top_cli_commands = _query(
            "SELECT properties.command_path, count(), uniq(person_id) "
            "FROM events "
            f"WHERE timestamp >= now() - INTERVAL {days} DAY "
            "AND event = 'cli.completed' "
            "GROUP BY properties.command_path ORDER BY count() DESC LIMIT 8"
        )
        mcp_errors = _query(
            "SELECT count(), countIf(toBool(properties.$mcp_is_error)) "
            "FROM events "
            f"WHERE timestamp >= now() - INTERVAL {days} DAY "
            "AND event = '$mcp_tool_call'"
        )
        funnel_steps = _query(
            "SELECT properties.funnel_step, uniq(person_id) "
            "FROM events "
            f"WHERE timestamp >= now() - INTERVAL {days} DAY "
            "AND event = 'overwatch_funnel_step' "
            "GROUP BY properties.funnel_step"
        )
    except (requests.RequestException, KeyError, TypeError, ValueError):
        return {"available": False, "reason": "PostHog usage data could not be loaded."}

    by_day: dict[str, dict[str, int]] = {}
    totals = {metric: 0 for metric in EVENT_METRICS.values()}
    for raw_day, event, count in rows:
        day = raw_day.isoformat() if isinstance(raw_day, date) else str(raw_day)
        values = by_day.setdefault(day, {metric: 0 for metric in EVENT_METRICS.values()})
        metric = EVENT_METRICS.get(event)
        if metric is None:
            continue
        values[metric] = int(count)
        totals[metric] += int(count)

    return {
        "available": True,
        "active_users": int(active_users[0][0]) if active_users else 0,
        "totals": totals,
        "daily": [{"day": day, **values} for day, values in sorted(by_day.items())],
        "top_mcp_tools": [
            {"name": name or "Unknown tool", "calls": int(calls), "users": int(users)}
            for name, calls, users in top_tools
        ],
        "top_cli_commands": [
            {"name": name or "Unknown command", "completions": int(count), "users": int(users)}
            for name, count, users in top_cli_commands
        ],
        "funnel_steps": {str(step): int(users) for step, users in funnel_steps if step},
        "mcp_tool_errors": int(mcp_errors[0][1]) if mcp_errors else 0,
        "mcp_tool_error_rate": (
            int(mcp_errors[0][1]) / int(mcp_errors[0][0])
            if mcp_errors and int(mcp_errors[0][0])
            else 0.0
        ),
    }
