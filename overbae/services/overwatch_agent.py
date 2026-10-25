"""Private Slack question answering over bounded product analytics tools."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.utils import timezone
from openai import AsyncOpenAI

from overbae.models import User
from overbae.services import overwatch_insights, user_insights

MAX_TOOL_ROUNDS = 4
MAX_QUESTION_LENGTH = 4000

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
            "description": "Look up one customer's product activity by exact email address.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "maxLength": 254},
                    "days": {"type": "integer", "minimum": 1, "maximum": 365},
                },
                "required": ["email", "days"],
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
    return user_insights.activation(timezone.now() - timedelta(days=days))


def _product_usage(args: dict[str, Any]) -> dict[str, Any]:
    days = _bounded_days(args.get("days"))
    since = timezone.now() - timedelta(days=days)
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
        timezone.now() - timedelta(days=days), timezone.now() - timedelta(days=7)
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
    user = (
        user_insights.users(timezone.now() - timedelta(days=days))
        .filter(email__iexact=email)
        .first()
    )
    if user is None:
        return {"found": False}
    activity = user_insights.user_activity(user, timezone.now() - timedelta(days=days))
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
    since = timezone.now() - timedelta(days=days)
    matches = [
        event
        for event in user_insights.funnel_milestones(since)
        if event["step"] == step
    ]
    matches.sort(key=lambda event: event["source_at"], reverse=True)
    users = {
        row["id"]: row["email"]
        for row in User.objects.filter(pk__in=[event["user_id"] for event in matches]).values(
            "id", "email"
        )
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


def _run_tool(name: str, raw_arguments: str) -> str:
    try:
        args = json.loads(raw_arguments)
        if not isinstance(args, dict):
            return '{"error":"Invalid tool arguments."}'
        handlers = {
            "activation_funnel": _activation_funnel,
            "product_usage": _product_usage,
            "billing_summary": _billing_summary,
            "customers_at_step": _customers_at_step,
            "customer_activity": _customer_activity,
        }
        handler = handlers.get(name)
        if handler is None:
            return '{"error":"This data query is unavailable."}'
        result = handler(args)
        return json.dumps(result, default=str, separators=(",", ":"))
    except Exception:
        return '{"error":"The product data query could not be completed."}'


async def answer_question(question: str) -> str:
    """Use a bounded OpenAI-compatible tool loop; only registered read tools are callable."""
    if not settings.OPENROUTER_API_KEY:
        return "Overwatch's analytics model is not configured yet."
    client = AsyncOpenAI(
        api_key=settings.OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        timeout=45.0,
        max_retries=0,
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": question[:MAX_QUESTION_LENGTH]}]
    instructions = (
        "You are Overwatch, Overmind's internal customer insights agent. Answer from the "
        "registered product analytics tools. Use aggregate tools for company-wide questions. "
        "Use billing_summary for spend, payment, subscription, or credit balance questions. "
        "Use customer_activity only for an exact email supplied by the user, and "
        "customers_at_step only when asked who reached a specified funnel step. For project "
        "steps, explain that activity is attributed to members who belonged to the project at "
        "the time; the data does not identify which member performed it. "
        "State the time window and distinguish missing PostHog data from zero activity. "
        "Do not invent facts or infer causes that the data "
        "does not establish. Keep the answer concise and clear."
    )
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            response = await client.chat.completions.create(
                model=settings.OVERWATCH_AGENT_MODEL,
                messages=[{"role": "system", "content": instructions}, *messages],
                tools=TOOLS,
                tool_choice="auto",
                max_tokens=900,
            )
            choice = response.choices[0]
            message = choice.message
            if not message.tool_calls:
                return (
                    message.content or "I couldn't find a useful answer in the available data."
                )[:3500]
            messages.append(message.model_dump(exclude_none=True))
            for call in message.tool_calls:
                result = _run_tool(call.function.name, call.function.arguments)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    except Exception:
        return "Sorry, I couldn't query the product data just now. Try again shortly."
    finally:
        await client.close()
    return "I needed more steps than expected to answer that. Try narrowing the question."
