import logging
import threading

import httpx
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from overbae.models import Feedback

logger = logging.getLogger(__name__)


def _post_to_slack(webhook_url: str, payload: dict) -> None:
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=5)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Slack webhook failed: %s", exc)


@receiver(post_save, sender=Feedback)
def notify_slack_on_new_feedback(sender, instance: Feedback, created: bool, **kwargs):
    if not created:
        return

    webhook_url = getattr(settings, "SLACK_FEEDBACK_WEBHOOK_URL", "")
    if not webhook_url:
        return

    user_label = instance.user.email if instance.user_id else "anonymous"
    payload = {
        "text": f":speech_balloon: *New feedback from {user_label}*\n\n{instance.feedback}",
    }

    thread = threading.Thread(target=_post_to_slack, args=(webhook_url, payload), daemon=True)
    thread.start()
