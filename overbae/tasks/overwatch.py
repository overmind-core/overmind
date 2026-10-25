"""Bounded, private Slack queries and durable activation event delivery."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime

import requests
from celery import shared_task
from django.conf import settings
from django.db import DatabaseError
from django.db.models import Q
from django.utils import timezone

from overbae.models import OverwatchFunnelDelivery
from overbae.services.overwatch_agent import answer_question
from overbae.services.overwatch_events import capture_funnel_step
from overbae.services.overwatch_privacy import slack_response_url, slack_text, slack_user_allowed
from overbae.services.user_insights import analytics_read_budget, funnel_milestones
from overbae.tasks.utils.task_lock import with_task_lock

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = (2, 5)
MAX_DELIVERY_ATTEMPTS = 5
MAX_DELIVERIES_PER_TICK = 20
TICK_BUDGET_SECONDS = 40
STEP_LABELS = {
    "signed_up": "Signed up",
    "joined_project": "Joined a project",
    "sent_first_span": "Sent a first trace",
    "synced_capabilities": "Synced capabilities",
    "ran_evaluation": "Ran an evaluation",
    "started_training": "Started training",
    "deployed_model": "Deployed a model",
    "called_alias": "Made a first model call",
}


def _channel_map() -> dict[str, str]:
    try:
        parsed = json.loads(settings.OVERWATCH_FUNNEL_CHANNEL_IDS)
    except (ValueError, TypeError):
        parsed = None
    if not isinstance(parsed, dict):
        logger.error("overwatch_funnel_channel_map_invalid")
        return {}
    return {
        step: channel
        for step, channel in parsed.items()
        if step in STEP_LABELS
        and isinstance(channel, str)
        and re.fullmatch(r"C[A-Z0-9]{8,31}", channel)
    }


def _start_at() -> datetime | None:
    raw = settings.OVERWATCH_FUNNEL_START_AT.strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if timezone.is_naive(value):
            raise ValueError("timezone_required")
        return value
    except ValueError:
        logger.error("overwatch_funnel_start_at_invalid")
        return None


def _slack_api(method: str, *, params=None, payload=None) -> dict:
    response = requests.request(
        "GET" if params is not None else "POST",
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
        params=params,
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError("slack_http_error")
    result = response.json()
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("slack_api_error")
    return result


def _post_message(delivery: OverwatchFunnelDelivery) -> None:
    label = STEP_LABELS[delivery.step]
    attribution = (
        "User" if delivery.step in {"signed_up", "joined_project"} else "Project milestone for user"
    )
    # A stable dashboard reference is enough for an alert. Never export the email.
    text = f"{attribution} #{delivery.user_id}: {label}."
    _slack_api(
        "chat.postMessage",
        payload={
            **slack_text(text),
            "channel": delivery.channel_id,
            "client_msg_id": str(delivery.id),
        },
    )


@shared_task(name="overbae.tasks.overwatch.dispatch_funnel_notifications", ignore_result=True)
@with_task_lock(lock_name="overwatch-funnel", timeout=60, renew=True)
def dispatch_funnel_notifications() -> dict[str, int]:
    """Discover bounded new milestones; retry durable rows independently of source retention."""
    start_at = _start_at()
    channels = _channel_map()
    posthog_enabled = bool(settings.POSTHOG_PROJECT_TOKEN)
    slack_enabled = bool(channels and settings.SLACK_BOT_TOKEN)
    if start_at is None or not (posthog_enabled or slack_enabled):
        return {"created": 0, "sent": 0, "failed": 0}

    deadline = time.monotonic() + TICK_BUDGET_SECONDS
    created = sent = failed = 0
    events = []
    discovery_deadline = min(deadline, time.monotonic() + 15)
    for step in STEP_LABELS:
        if not posthog_enabled and step not in channels:
            continue
        remaining = discovery_deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            # Each step gets its own rollback boundary. A slow source must not
            # discard other milestones or prevent durable destination retries.
            with analytics_read_budget(min(3, remaining)):
                events.extend(
                    funnel_milestones(
                        start_at, only_unrecorded=True, limit_per_step=25, steps={step}
                    )
                )
        except (DatabaseError, TimeoutError):
            logger.warning("overwatch_milestone_discovery_unavailable step=%s", step)
    for event in events:
        if time.monotonic() >= deadline:
            break
        channel_id = channels.get(event["step"], "") if slack_enabled else ""
        if not channel_id and not posthog_enabled:
            continue
        _, was_created = OverwatchFunnelDelivery.objects.get_or_create(
            user_id=event["user_id"],
            step=event["step"],
            defaults={"source_at": event["source_at"], "channel_id": channel_id},
        )
        created += int(was_created)

    pending = Q(pk__in=[])
    if posthog_enabled:
        pending |= Q(posthog_delivered_at__isnull=True, posthog_attempts__lt=MAX_DELIVERY_ATTEMPTS)
    if slack_enabled:
        pending |= Q(step__in=channels, attempts__lt=MAX_DELIVERY_ATTEMPTS) & ~Q(
            status=OverwatchFunnelDelivery.Status.SENT
        )
    deliveries = (
        OverwatchFunnelDelivery.objects.filter(
            pending,
            user__is_guest=False,
            source_at__gte=start_at,
        )
        .select_related("user")
        .order_by("source_at", "id")[:MAX_DELIVERIES_PER_TICK]
    )
    for delivery in deliveries:
        if time.monotonic() >= deadline:
            break
        if (
            posthog_enabled
            and not delivery.posthog_delivered_at
            and delivery.posthog_attempts < MAX_DELIVERY_ATTEMPTS
        ):
            delivery.posthog_attempts += 1
            delivery.posthog_last_error = ""
            delivery.save(update_fields=["posthog_attempts", "posthog_last_error", "updated_at"])
            try:
                accepted = capture_funnel_step(
                    event_id=delivery.id,
                    distinct_id=delivery.user.clerk_user_id or f"user:{delivery.user_id}",
                    step=delivery.step,
                    source_at=delivery.source_at,
                )
                if accepted:
                    delivery.posthog_delivered_at = timezone.now()
            except (requests.RequestException, RuntimeError, ValueError):
                # Exception text can contain a credential-bearing URL. Store a fixed code.
                delivery.posthog_last_error = "posthog_delivery_failed"
                failed += 1
            delivery.save(
                update_fields=["posthog_delivered_at", "posthog_last_error", "updated_at"]
            )

        channel_id = channels.get(delivery.step, "") if slack_enabled else ""
        if (
            not channel_id
            or delivery.status == OverwatchFunnelDelivery.Status.SENT
            or delivery.attempts >= MAX_DELIVERY_ATTEMPTS
            or time.monotonic() >= deadline
        ):
            continue
        delivery.channel_id = channel_id
        delivery.attempts += 1
        delivery.status = OverwatchFunnelDelivery.Status.PENDING
        delivery.last_error = ""
        delivery.save(
            update_fields=["channel_id", "attempts", "status", "last_error", "updated_at"]
        )
        try:
            _post_message(delivery)
        except (requests.RequestException, RuntimeError, ValueError):
            delivery.status = OverwatchFunnelDelivery.Status.FAILED
            delivery.last_error = "slack_delivery_failed"
            failed += 1
        else:
            delivery.status = OverwatchFunnelDelivery.Status.SENT
            delivery.delivered_at = timezone.now()
            sent += 1
        delivery.save(update_fields=["status", "last_error", "delivered_at", "updated_at"])
    return {"created": created, "sent": sent, "failed": failed}


def _approved_user(user_id: str) -> bool:
    if not slack_user_allowed(user_id) or not settings.OVERWATCH_SLACK_TEAM_ID:
        return False
    user = _slack_api("users.info", params={"user": user_id}).get("user")
    if (
        not isinstance(user, dict)
        or user.get("id") != user_id
        or user.get("team_id") != settings.OVERWATCH_SLACK_TEAM_ID
    ):
        return False
    if any(
        user.get(flag)
        for flag in (
            "deleted",
            "is_bot",
            "is_app_user",
            "is_restricted",
            "is_ultra_restricted",
            "is_stranger",
        )
    ):
        return False
    profile = user.get("profile")
    email = profile.get("email", "") if isinstance(profile, dict) else ""
    domain = settings.OVERWATCH_ALLOWED_EMAIL_DOMAIN.lower().lstrip("@")
    return isinstance(email, str) and bool(domain) and email.lower().rsplit("@", 1)[-1] == domain


def _answer(user_id: str, question: str) -> str:
    if not _approved_user(user_id):
        return "Your Slack account is not approved for Overwatch analytics."
    return asyncio.run(answer_question(question))


@shared_task(
    name="overbae.tasks.overwatch.answer_slack_question",
    ignore_result=True,
)
def answer_slack_question(user_id: str, question: str, response_url: str) -> None:
    """Resolve the caller and reply privately without echoing sensitive input."""
    if not slack_response_url(response_url) or not settings.SLACK_BOT_TOKEN:
        return
    try:
        answer = _answer(user_id, question)
    except Exception:
        logger.warning("overwatch_slack_question_failed")
        answer = "I couldn't query the product data just now. Try again shortly."
    try:
        response = requests.post(
            response_url,
            json={
                "response_type": "ephemeral",
                "replace_original": True,
                **slack_text(answer),
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            logger.warning("overwatch_slack_reply_rejected")
    except requests.RequestException:
        logger.warning("overwatch_slack_reply_failed")


@shared_task(
    name="overbae.tasks.overwatch.answer_slack_message",
    ignore_result=True,
)
def answer_slack_message(
    user_id: str, question: str, channel_id: str, message_ts: str, thread_ts: str = ""
) -> None:
    """Answer only an authenticated direct message, with no automatic link previews."""
    if not settings.SLACK_BOT_TOKEN or not channel_id.startswith("D"):
        return
    try:
        answer = _answer(user_id, question)
    except Exception:
        logger.warning("overwatch_slack_dm_failed")
        answer = "I couldn't query the product data just now. Try again shortly."
    try:
        _slack_api(
            "chat.postMessage",
            payload={
                "channel": channel_id,
                "thread_ts": thread_ts or message_ts,
                **slack_text(answer),
            },
        )
    except (requests.RequestException, RuntimeError, ValueError):
        logger.warning("overwatch_slack_dm_reply_failed")
