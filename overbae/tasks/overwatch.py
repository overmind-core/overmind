"""Slack delivery for Overwatch's customer activation funnel."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from urllib.parse import urlparse

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from overbae.models import OverwatchFunnelDelivery, User
from overbae.services.overwatch_agent import answer_question
from overbae.services.overwatch_events import capture_funnel_step
from overbae.services.user_insights import funnel_milestones
from overbae.tasks.utils.task_lock import with_task_lock

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 10
MAX_DELIVERY_ATTEMPTS = 5
MAX_DELIVERIES_PER_TICK = 20

STEP_LABELS = {
    "signed_up": "Signed up",
    "joined_project": "Joined a project",
    "sent_first_span": "Sent their first trace",
    "synced_capabilities": "Synced capabilities",
    "ran_evaluation": "Ran an evaluation",
    "started_training": "Started training",
    "deployed_model": "Deployed a model",
    "called_alias": "Made their first model call",
}


def _channel_map() -> dict[str, str]:
    try:
        parsed = json.loads(settings.OVERWATCH_FUNNEL_CHANNEL_IDS)
    except (json.JSONDecodeError, TypeError):
        logger.error("overwatch_funnel_channel_map_invalid")
        return {}
    if not isinstance(parsed, dict):
        logger.error("overwatch_funnel_channel_map_invalid")
        return {}
    return {
        str(step): channel.strip()
        for step, channel in parsed.items()
        if step in STEP_LABELS and isinstance(channel, str) and channel.strip()
    }


def _start_at() -> datetime | None:
    raw = settings.OVERWATCH_FUNNEL_START_AT.strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.error("overwatch_funnel_start_at_invalid")
        return None
    return timezone.make_aware(value) if timezone.is_naive(value) else value


def _post_message(delivery: OverwatchFunnelDelivery, email: str) -> None:
    token = settings.SLACK_BOT_TOKEN
    if not token:
        raise RuntimeError("slack_not_configured")
    label = STEP_LABELS.get(delivery.step, delivery.step)
    safe_email = email.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if delivery.step in {"signed_up", "joined_project"}:
        text = f":chart_with_upwards_trend: *{label}* — {safe_email}"
    else:
        text = f":chart_with_upwards_trend: Project reached *{label}* — member: {safe_email}"
    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "channel": delivery.channel_id,
            "text": text,
            "client_msg_id": str(delivery.id),
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "slack_api_error")[:120])


@shared_task(name="overbae.tasks.overwatch.dispatch_funnel_notifications", ignore_result=True)
@with_task_lock(lock_name="overwatch-funnel", timeout=60, renew=True)
def dispatch_funnel_notifications() -> dict[str, int]:
    """Send newly reached activation steps to PostHog and configured Slack channels."""
    start_at = _start_at()
    channels = _channel_map()
    posthog_enabled = bool(settings.POSTHOG_PROJECT_TOKEN)
    slack_enabled = bool(channels and settings.SLACK_BOT_TOKEN)
    if start_at is None or not (posthog_enabled or slack_enabled):
        return {"created": 0, "sent": 0, "failed": 0}

    created = sent = failed = attempted = 0
    for event in sorted(funnel_milestones(start_at), key=lambda item: item["source_at"]):
        channel_id = channels.get(event["step"], "") if slack_enabled else ""
        if not channel_id and not posthog_enabled:
            continue
        delivery, was_created = OverwatchFunnelDelivery.objects.get_or_create(
            user_id=event["user_id"],
            step=event["step"],
            defaults={
                "source_at": event["source_at"],
                "channel_id": channel_id,
            },
        )
        created += int(was_created)
        if channel_id and delivery.channel_id != channel_id:
            delivery.channel_id = channel_id
            delivery.save(update_fields=["channel_id", "updated_at"])

        if posthog_enabled and not delivery.posthog_delivered_at:
            if delivery.posthog_attempts < MAX_DELIVERY_ATTEMPTS:
                delivery.posthog_attempts += 1
                delivery.posthog_last_error = ""
                delivery.save(
                    update_fields=["posthog_attempts", "posthog_last_error", "updated_at"]
                )
                identity = (
                    User.objects.filter(pk=delivery.user_id)
                    .values_list("clerk_user_id", flat=True)
                    .first()
                ) or f"user:{delivery.user_id}"
                try:
                    queued = capture_funnel_step(
                        event_id=delivery.id,
                        distinct_id=identity,
                        step=delivery.step,
                        source_at=delivery.source_at,
                    )
                    if queued:
                        delivery.posthog_delivered_at = timezone.now()
                        delivery.save(
                            update_fields=["posthog_delivered_at", "updated_at"]
                        )
                except Exception as exc:
                    delivery.posthog_last_error = str(exc)[:160]
                    delivery.save(
                        update_fields=["posthog_last_error", "updated_at"]
                    )
                    logger.warning(
                        "overwatch_funnel_posthog_failed",
                        extra={"step": delivery.step, "attempt": delivery.posthog_attempts},
                    )

        if not channel_id or delivery.status == OverwatchFunnelDelivery.Status.SENT:
            continue
        if delivery.attempts >= MAX_DELIVERY_ATTEMPTS:
            continue
        if attempted >= MAX_DELIVERIES_PER_TICK:
            break

        attempted += 1
        delivery.attempts += 1
        delivery.status = OverwatchFunnelDelivery.Status.PENDING
        delivery.last_error = ""
        delivery.save(update_fields=["attempts", "status", "last_error", "updated_at"])
        try:
            user_email = (
                User.objects.filter(pk=delivery.user_id)
                .values_list("email", flat=True)
                .first()
            )
            _post_message(delivery, user_email or "Unknown user")
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            delivery.status = OverwatchFunnelDelivery.Status.FAILED
            delivery.last_error = str(exc)[:160]
            delivery.save(update_fields=["status", "last_error", "updated_at"])
            failed += 1
            logger.warning(
                "overwatch_funnel_delivery_failed",
                extra={"step": delivery.step, "attempt": delivery.attempts},
            )
            continue

        delivery.status = OverwatchFunnelDelivery.Status.SENT
        delivery.delivered_at = timezone.now()
        delivery.save(update_fields=["status", "delivered_at", "updated_at"])
        sent += 1
    return {"created": created, "sent": sent, "failed": failed}


def _trusted_response_url(response_url: str) -> bool:
    try:
        parsed = urlparse(response_url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "hooks.slack.com"
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


@shared_task(name="overbae.tasks.overwatch.answer_slack_question", ignore_result=True)
def answer_slack_question(user_id: str, question: str, response_url: str) -> None:
    """Resolve the caller from Slack, run the private analytics agent and reply ephemerally."""
    if not _trusted_response_url(response_url):
        return
    token = settings.SLACK_BOT_TOKEN
    if not token:
        return
    try:
        profile_response = requests.get(
            "https://slack.com/api/users.info",
            params={"user": user_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
        email = (((profile.get("user") or {}).get("profile") or {}).get("email") or "").lower()
        domain = settings.OVERWATCH_ALLOWED_EMAIL_DOMAIN.lower().lstrip("@")
        if not email or email.rsplit("@", 1)[-1] != domain:
            answer = f"Overwatch is available to {domain} team members only."
        else:
            import asyncio

            answer = asyncio.run(answer_question(question))
    except Exception:
        logger.warning("overwatch_slack_question_failed")
        answer = "Sorry, I couldn't query the product data just now. Try again shortly."

    quoted = "> " + " ".join(question.split())[:1000]
    try:
        requests.post(
            response_url,
            json={
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"{quoted}\n\n{answer}"[:3900],
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        ).raise_for_status()
    except requests.RequestException:
        logger.warning("overwatch_slack_reply_failed")


@shared_task(name="overbae.tasks.overwatch.answer_slack_message", ignore_result=True)
def answer_slack_message(
    user_id: str,
    question: str,
    channel_id: str,
    message_ts: str,
    thread_ts: str = "",
) -> None:
    """Answer an authenticated Slack DM in the same conversation."""
    token = settings.SLACK_BOT_TOKEN
    if not token:
        return
    try:
        profile_response = requests.get(
            "https://slack.com/api/users.info",
            params={"user": user_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
        email = (((profile.get("user") or {}).get("profile") or {}).get("email") or "").lower()
        domain = settings.OVERWATCH_ALLOWED_EMAIL_DOMAIN.lower().lstrip("@")
        if not email or email.rsplit("@", 1)[-1] != domain:
            answer = f"Overwatch is available to {domain} team members only."
        else:
            import asyncio

            answer = asyncio.run(answer_question(question))
    except Exception:
        logger.warning("overwatch_slack_dm_failed")
        answer = "Sorry, I couldn't query the product data just now. Try again shortly."

    payload = {
        "channel": channel_id,
        "text": answer[:3500],
    }
    payload["thread_ts"] = thread_ts or message_ts
    try:
        response = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        if not response.json().get("ok"):
            logger.warning("overwatch_slack_dm_reply_rejected")
    except (requests.RequestException, ValueError):
        logger.warning("overwatch_slack_dm_reply_failed")
