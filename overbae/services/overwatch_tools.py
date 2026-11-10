"""Bounded read-only analytics contract shared by the dashboard and agent runtime."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from overbae.services import overwatch_insights, overwatch_usage, user_insights


def _activation_funnel(args: dict[str, Any]) -> dict[str, Any]:
    days = args["days"]
    return user_insights.activation(user_insights.window_start(days))


def _product_usage(args: dict[str, Any]) -> dict[str, Any]:
    days = args["days"]
    since = user_insights.window_start(days)
    with user_insights.analytics_read_budget():
        overview = user_insights.overview(since, user_insights.window_start(7))
    return {
        "window_days": days,
        "registered_users": overview["users_total"],
        "trace_active_users": overview["trace_active_users_window"],
        "projects_created": sum(point["value"] for point in overview["projects_created"]),
        "traces": sum(point["value"] for point in overview["traces"]),
        "training_jobs": overview["training_jobs"],
        "live_deployments": overview["live_deployments"],
        "inference_calls": sum(point["value"] for point in overview["inference_calls"]),
        "posthog": overwatch_insights.cached_product_usage(days),
    }


def _billing_summary(args: dict[str, Any]) -> dict[str, Any]:
    days = args["days"]
    overview = user_insights.overview(
        user_insights.window_start(days), user_insights.window_start(7)
    )
    return {
        "window_days": days,
        "subscription_statuses": overview["subscriptions"],
        "paying_users": overview["paying_users"],
        "cancelling_users": overview["cancelling_users"],
        "free_users": overview["free_users"],
        "low_balance_users": overview["low_balance_users"],
        "topups_usd": sum((point["value"] for point in overview["topups_usd"]), Decimal("0")),
        "spend_usd": sum((point["value"] for point in overview["spend_usd"]), Decimal("0")),
        "spend_by_service_usd": overview["spend_by_service"],
    }


def _customers_at_step(args: dict[str, Any]) -> dict[str, Any]:
    step = args["step"]
    if step in user_insights.RESETTABLE_MILESTONE_STEPS:
        return {
            "available": False,
            "reason": (
                f"First-time activity for {step} cannot be determined from retained product records. "
                "Deployment and alias timestamps reset on redeployment or model switching. "
                "No first-time customer count or list is available."
            ),
        }
    days = args["days"]
    limit = args.get("limit", 25)
    since = user_insights.window_start(days)
    return {
        "available": True,
        "step": step,
        "window_days": days,
        **user_insights.milestone_customers(since, step=step, limit=limit),
    }


def _private_result(name: str, result: dict[str, Any]) -> str:
    """Format customer records in-process, without another model request."""
    if not result["available"]:
        return result["reason"]
    rows = []
    for row in result.get("customers", []):
        line = f"{row['email']}: {row['reached_at']}"
        if sum(len(value) + 1 for value in rows) + len(line) > 2400:
            break
        rows.append(line)
    listing = "\n".join(rows)
    return (
        f"{result['step']}: {result['total']} users with their earliest retained record "
        f"in the last {result['window_days']} UTC days.\n"
        f"Showing {len(rows)} results.\n{listing}\n"
        "Retained records do not prove lifetime-first activity if history was deleted. "
        "Project milestones are attributed to memberships, not individual actions."
    )


def query(tool: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    """Arguments are validated by the internal API before any query executes."""
    if tool in {"customer_activity", "model_usage"}:
        with user_insights.analytics_read_budget(seconds=10):
            report = (
                overwatch_usage.customer_activity(arguments)
                if tool == "customer_activity"
                else overwatch_usage.model_usage(arguments)
            )
        return report, True
    handlers = {
        "activation_funnel": _activation_funnel,
        "product_usage": _product_usage,
        "billing_summary": _billing_summary,
        "customers_at_step": _customers_at_step,
    }
    if tool == "product_usage":
        result = handlers[tool](arguments)
    else:
        with user_insights.analytics_read_budget():
            result = handlers[tool](arguments)
    private = tool in {"customer_activity", "customers_at_step"}
    return (_private_result(tool, result) if private else json.dumps(result, default=str)), private
