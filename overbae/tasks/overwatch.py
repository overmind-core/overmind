"""Private Slack answers from read-only Overwatch analytics."""

from __future__ import annotations

import asyncio
import logging

import requests
from celery import shared_task
from django.conf import settings

from overbae.services.overwatch_agent import answer_question
from overbae.services.overwatch_privacy import slack_response_url, slack_text, slack_user_allowed

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = (2, 5)


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
