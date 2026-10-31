"""Bounded read-only analytics contract shared by the dashboard and agent runtime."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from overbae.services import overwatch_insights, user_insights


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


def _customer_activity(args: dict[str, Any]) -> dict[str, Any]:
    email = str(args.get("email", "")).strip()[:254]
    days = args["days"]
    user = user_insights.users(user_insights.window_start(days)).filter(email__iexact=email).first()
    if user is None:
        return {"found": False}
    activity = user_insights.user_activity(user, user_insights.window_start(days))
    return {
        "found": True,
        "email": user.email,
        "signed_up_at": user.date_joined.isoformat(),
        "activation_stage": user_insights.stage(user),
        "subscription_status": activity["billing"]["subscription_status"],
        "lifetime_spend_usd": activity["billing"]["spend_usd"],
        "lifetime_topups_usd": activity["billing"]["topups_usd"],
        "current_credit_balance_usd": activity["billing"]["balance_usd"],
        "projects": user.projects,
        "trace_count_in_window": user.spans,
        "last_trace_at": user.last_span_at.isoformat() if user.last_span_at else None,
        "training": activity["training"]["totals"],
        "traces_total": activity["traces"]["total"],
        "inference_calls": activity["inference"]["calls"],
        "evaluations": activity["evaluations"]["total"],
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
    if name == "customer_activity":
        if not result.get("found"):
            return "No matching customer was found."
        return (
            f"Customer: {result['email']}\n"
            f"Signed up: {result['signed_up_at']}\n"
            f"Milestone: {result['activation_stage']}\n"
            f"Subscription: {result['subscription_status'] or 'None'}\n"
            f"Lifetime spend: ${result['lifetime_spend_usd']}\n"
            f"Lifetime top-ups: ${result['lifetime_topups_usd']}\n"
            f"Credit balance: ${result['current_credit_balance_usd']}\n"
            f"Spans in the selected UTC window: {result['trace_count_in_window']}\n"
            f"Project lifetime totals: {result['traces_total']} traces, "
            f"{result['inference_calls']} model calls, {result['evaluations']} evaluations.\n"
            "Project activity belongs to the customer's projects; it does not identify the actor."
        )
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
    handlers = {
        "activation_funnel": _activation_funnel,
        "product_usage": _product_usage,
        "billing_summary": _billing_summary,
        "customers_at_step": _customers_at_step,
        "customer_activity": _customer_activity,
    }
    if tool == "product_usage":
        result = handlers[tool](arguments)
    else:
        with user_insights.analytics_read_budget():
            result = handlers[tool](arguments)
    private = tool in {"customer_activity", "customers_at_step"}
    return (_private_result(tool, result) if private else json.dumps(result, default=str)), private
