"""Private Slack question answering over bounded product analytics tools."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone
from jsonschema import ValidationError, validate
from openai import AsyncOpenAI

from overbae.core.model_registry import openrouter_slug
from overbae.models import User
from overbae.services import overwatch_insights, user_insights
from overbae.services.overwatch_privacy import model_question

MAX_TOOL_ROUNDS = 4
MAX_QUESTION_LENGTH = 4000
MAX_TOOL_CALLS = 8
logger = logging.getLogger(__name__)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "activation_funnel",
            "description": "Get aggregate counts and time to first trace for the signup cohort.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 365}},
                "required": ["days"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "product_usage",
            "description": "Get aggregate activity from Overmind and PostHog.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 365}},
                "required": ["days"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "billing_summary",
            "description": (
                "Get aggregate subscription, product spend, Stripe top-up, and low-balance metrics."
            ),
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 365}},
                "required": ["days"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "customers_at_step",
            "description": (
                "List customers who first reached one activation step in the selected period. "
                "Project steps indicate the user belonged to a project where that activity was "
                "recorded after they joined; they do not identify which member performed it. "
                "This is for private Slack answers only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "step": {
                        "type": "string",
                        "enum": list(user_insights.ACTIVATION_STEPS),
                    },
                    "days": {"type": "integer", "minimum": 1, "maximum": 365},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 25},
                },
                "required": ["step", "days"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "customer_activity",
            "description": "Look up one supplied customer reference. Customer details are rendered locally, never sent to the model.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_ref": {"type": "string", "pattern": "^customer_[1-9][0-9]*$"},
                    "days": {"type": "integer", "minimum": 1, "maximum": 365},
                },
                "required": ["customer_ref", "days"],
                "additionalProperties": False,
            },
        },
    },
]


def _bounded_days(value: Any) -> int:
    try:
        return min(max(int(value), 1), 365)
    except (TypeError, ValueError):
        return 30


def _activation_funnel(args: dict[str, Any]) -> dict[str, Any]:
    days = _bounded_days(args.get("days"))
    return user_insights.activation(user_insights.window_start(days))


def _product_usage(args: dict[str, Any]) -> dict[str, Any]:
    days = _bounded_days(args.get("days"))
    since = user_insights.window_start(days)
    with user_insights.analytics_read_budget():
        overview = user_insights.overview(since, timezone.now() - timedelta(days=7))
    return {
        "window_days": days,
        "registered_users": overview["users_total"],
        "trace_active_users": overview["trace_active_users_window"],
        "projects_created": sum(point["value"] for point in overview["projects_created"]),
        "traces": sum(point["value"] for point in overview["traces"]),
        "training_jobs": overview["training_jobs"],
        "live_deployments": overview["live_deployments"],
        "inference_calls": sum(point["value"] for point in overview["inference_calls"]),
        "posthog": overwatch_insights.product_usage(days),
    }


def _billing_summary(args: dict[str, Any]) -> dict[str, Any]:
    days = _bounded_days(args.get("days"))
    overview = user_insights.overview(
        user_insights.window_start(days), timezone.now() - timedelta(days=7)
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
    days = _bounded_days(args.get("days"))
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
    step = str(args.get("step", ""))
    if step not in user_insights.ACTIVATION_STEPS:
        return {"error": "Unknown activation step."}
    days = _bounded_days(args.get("days"))
    try:
        limit = min(max(int(args.get("limit", 25)), 1), 25)
    except (TypeError, ValueError):
        limit = 25
    since = user_insights.window_start(days)
    matches = [
        event
        for event in user_insights.funnel_milestones(since, steps={step})
        if event["step"] == step
    ]
    matches.sort(key=lambda event: event["source_at"], reverse=True)
    users = {
        row["id"]: row["email"]
        for row in User.objects.filter(
            pk__in=[event["user_id"] for event in matches[:limit]]
        ).values("id", "email")
    }
    return {
        "step": step,
        "window_days": days,
        "total": len(matches),
        "customers": [
            {
                "email": users.get(event["user_id"], "Unknown user"),
                "reached_at": event["source_at"].isoformat(),
            }
            for event in matches[:limit]
        ],
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
    rows = []
    for row in result.get("customers", []):
        line = f"{row['email']}: {row['reached_at']}"
        if sum(len(value) + 1 for value in rows) + len(line) > 2400:
            break
        rows.append(line)
    listing = "\n".join(rows)
    return (
        f"{result['step']}: {result['total']} users in the last {result['window_days']} UTC days.\n"
        f"Showing {len(rows)} results.\n{listing}\n"
        "Project milestones are attributed to memberships, not individual actions."
    )


def _run_tool(name: str, raw_arguments: str, identities: dict[str, str]) -> tuple[str, bool]:
    """Validate model-selected arguments and execute ORM work outside the async loop."""
    close_old_connections()
    try:
        spec = next((tool["function"] for tool in TOOLS if tool["function"]["name"] == name), None)
        if spec is None:
            return '{"error":"This data query is unavailable."}', False
        args = json.loads(raw_arguments)
        validate(args, spec["parameters"])
        if name == "customer_activity":
            email = identities.get(args.pop("customer_ref"))
            if not email:
                return '{"error":"Supply an exact customer email in your question."}', False
            args["email"] = email
        handlers = {
            "activation_funnel": _activation_funnel,
            "product_usage": _product_usage,
            "billing_summary": _billing_summary,
            "customers_at_step": _customers_at_step,
            "customer_activity": _customer_activity,
        }
        if name == "product_usage":
            result = handlers[name](args)
        else:
            with user_insights.analytics_read_budget():
                result = handlers[name](args)
        if name in {"customer_activity", "customers_at_step"}:
            return _private_result(name, result), True
        return json.dumps(result, default=str, separators=(",", ":")), False
    except (ValidationError, ValueError, TypeError):
        return '{"error":"Invalid tool arguments."}', False
    except Exception:
        logger.warning("overwatch_analytics_tool_failed")
        return '{"error":"The product data query could not be completed."}', False
    finally:
        close_old_connections()


async def answer_question(question: str) -> str:
    """Only aggregate tool results reach the approved model provider."""
    if not settings.OVERWATCH_AGENT_ENABLED or not settings.OPENROUTER_API_KEY:
        return "Overwatch's analytics model is not enabled yet."
    try:
        safe_question, identities = model_question(question[:MAX_QUESTION_LENGTH])
    except ValueError:
        return "That question appears to contain a credential. Remove it before asking Overwatch."
    client = AsyncOpenAI(
        api_key=settings.OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        timeout=20.0,
        max_retries=0,
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": safe_question}]
    instructions = (
        "You are Overwatch, Overmind's internal customer insights agent. Answer from the "
        "registered analytics tools. Use aggregate tools for company-wide questions and "
        "billing_summary for aggregate spend, payment, subscription, or balance questions. "
        "Customer emails are replaced by request-local customer_N references. Pass only one "
        "of those supplied references to customer_activity. Customers_at_step is for who "
        "reached a specified milestone. Customer records are rendered locally. "
        "State the UTC calendar-day window and distinguish unavailable PostHog data from zero. "
        "CLI and MCP identities are not necessarily distinct registered users. Activation "
        "milestones are independent adoption counts, not an ordered conversion funnel. "
        "Project activity is attributed to project members, not the person who performed it. "
        "Do not infer causes or invent facts. Do not include links or request credentials."
    )
    calls = 0
    evidence = False
    try:
        async with asyncio.timeout(45):
            for _ in range(MAX_TOOL_ROUNDS):
                response = await client.chat.completions.create(
                    model=openrouter_slug(settings.OVERWATCH_AGENT_MODEL),
                    messages=[{"role": "system", "content": instructions}, *messages],
                    tools=TOOLS,
                    tool_choice="required" if calls == 0 else "auto",
                    max_tokens=900,
                    extra_body={"provider": {"data_collection": "deny", "zdr": True}},
                )
                message = response.choices[0].message
                if not message.tool_calls:
                    if calls == 0:
                        return "No analytics were queried. Ask for a specific metric, milestone, or customer."
                    if not evidence:
                        return "No analytics could be loaded. Try again shortly or narrow the question."
                    return (message.content or "No useful answer was found in the available data.")[
                        :2900
                    ]
                messages.append(message.model_dump(exclude_none=True))
                for call in message.tool_calls:
                    calls += 1
                    if calls > MAX_TOOL_CALLS:
                        return "Try narrowing the question to one metric or customer."
                    result, private = await sync_to_async(_run_tool, thread_sensitive=True)(
                        call.function.name,
                        call.function.arguments,
                        identities,
                    )
                    if private:
                        return result[:2900]
                    evidence = evidence or "error" not in json.loads(result)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    except Exception:
        logger.warning("overwatch_analytics_answer_failed")
        return "I couldn't query the product data just now. Try again shortly."
    finally:
        await client.close()
    return "Try narrowing the question to one metric or customer."
