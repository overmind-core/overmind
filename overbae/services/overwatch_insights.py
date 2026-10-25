"""Bounded aggregate PostHog queries. Raw event/person rows never leave this layer."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

from overbae.services.overwatch_privacy import model_question, posthog_url
from overbae.services.user_insights import ACTIVATION_STEPS, window_start

EVENT_METRICS = {
    "cli.completed": "cli_completions",
    "$mcp_tool_call": "mcp_tool_calls",
    "training_result_available": "training_results_available",
    "training_check_finished": "training_checks_finished",
}
ACTIVE_USER_EVENTS = ("cli.completed", "$mcp_tool_call")


def _query(hogql: str) -> list[list[Any]]:
    project = settings.POSTHOG_PROJECT_ID
    if not str(project).isascii() or not str(project).isdecimal():
        raise ValueError("invalid_posthog_project")
    response = requests.post(
        posthog_url(settings.POSTHOG_API_HOST, f"/api/projects/{project}/query/"),
        headers={"Authorization": f"Bearer {settings.POSTHOG_PERSONAL_API_KEY}"},
        json={"query": {"kind": "HogQLQuery", "query": hogql}},
        timeout=(2, 6),
        allow_redirects=False,
    )
    if not 200 <= response.status_code < 300 or len(response.content) > 256_000:
        raise ValueError("posthog_query_failed")
    rows = response.json()["results"]
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("invalid_posthog_results")
    return rows


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("invalid_count")
    count = int(value)
    if not 0 <= count <= 2**53 - 1:
        raise ValueError("invalid_count")
    return count


def _name(value: Any, fallback: str) -> str:
    # Names are controlled identifiers, never arbitrary event-property payloads.
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_ -]{0,79}", value):
        return fallback
    try:
        model_question(value)
    except ValueError:
        return fallback
    return value


def product_usage(days: int) -> dict[str, Any]:
    """Return aggregates with explicit source, identity and calendar-window semantics."""
    since = window_start(days)
    if not settings.POSTHOG_PERSONAL_API_KEY or not settings.POSTHOG_PROJECT_ID:
        return {"available": False, "reason": "PostHog read access is not configured."}
    until = timezone.now()
    window = f"timestamp >= toDateTime('{since.strftime('%Y-%m-%d %H:%M:%S')}', 'UTC') AND timestamp <= toDateTime('{until.strftime('%Y-%m-%d %H:%M:%S')}', 'UTC')"
    events = ", ".join(f"'{event}'" for event in EVENT_METRICS)
    active_events = ", ".join(f"'{event}'" for event in ACTIVE_USER_EVENTS)
    queries = [
        f"SELECT toDate(timestamp, 'UTC'), event, count() FROM events WHERE {window} AND event IN ({events}) GROUP BY toDate(timestamp, 'UTC'), event ORDER BY toDate(timestamp, 'UTC')",
        f"SELECT uniq(person_id) FROM events WHERE {window} AND event IN ({active_events})",
        f"SELECT properties.$mcp_tool_name, count(), uniq(person_id) FROM events WHERE {window} AND event = '$mcp_tool_call' GROUP BY properties.$mcp_tool_name ORDER BY count() DESC LIMIT 8",
        f"SELECT properties.command_path, count(), uniq(person_id) FROM events WHERE {window} AND event = 'cli.completed' GROUP BY properties.command_path ORDER BY count() DESC LIMIT 8",
        f"SELECT count(), countIf(toBool(properties.$mcp_is_error)) FROM events WHERE {window} AND event = '$mcp_tool_call'",
        f"SELECT properties.funnel_step, uniqExact(distinct_id) FROM events WHERE {window} AND event = 'overwatch_funnel_step' GROUP BY properties.funnel_step LIMIT {len(ACTIVATION_STEPS)}",
    ]
    try:
        # Six independent, bounded reads; at most three requests run concurrently.
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="overwatch-posthog") as pool:
            rows, active, tools, commands, errors, steps = list(pool.map(_query, queries))
        by_day: dict[str, dict[str, int]] = {}
        totals = dict.fromkeys(EVENT_METRICS.values(), 0)
        for raw_day, event, raw_count in rows:
            day = raw_day.isoformat() if isinstance(raw_day, date) else str(raw_day)
            date.fromisoformat(day)
            metric = EVENT_METRICS[event]
            count = _count(raw_count)
            by_day.setdefault(day, dict.fromkeys(totals, 0))[metric] += count
            totals[metric] += count
        total_calls, failed_calls = (_count(value) for value in errors[0]) if errors else (0, 0)
        if failed_calls > total_calls:
            raise ValueError("invalid_error_count")
        return {
            "available": True,
            "identity_basis": "PostHog identities, including unlinked CLI installations; not a count of registered customers.",
            "window_start": since.isoformat(),
            "window_end": until.isoformat(),
            "active_users": _count(active[0][0]) if active else 0,
            "totals": totals,
            "daily": [{"day": day, **values} for day, values in sorted(by_day.items())],
            "top_mcp_tools": [
                {
                    "name": _name(name, "Unknown tool"),
                    "calls": _count(calls),
                    "users": _count(users),
                }
                for name, calls, users in tools
            ],
            "top_cli_commands": [
                {
                    "name": _name(name, "Unknown command"),
                    "completions": _count(count),
                    "users": _count(users),
                }
                for name, count, users in commands
            ],
            "funnel_steps": {
                step: _count(users) for step, users in steps if step in ACTIVATION_STEPS
            },
            "mcp_tool_errors": failed_calls,
            "mcp_tool_error_rate": failed_calls / total_calls if total_calls else 0.0,
        }
    except (requests.RequestException, KeyError, TypeError, ValueError, IndexError):
        return {"available": False, "reason": "PostHog usage data could not be loaded."}
