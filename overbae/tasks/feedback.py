import logging

import httpx
from celery import shared_task
from django.conf import settings

from overbae.models import Feedback
from overbae.services import feedback as feedback_service

logger = logging.getLogger(__name__)


@shared_task(name="overbae.tasks.feedback.post_to_slack")
def post_to_slack(feedback_id: str) -> None:
    feedback = Feedback.objects.filter(pk=feedback_id).first()
    if feedback is None or not settings.SLACK_FEEDBACK_WEBHOOK_URL:
        return
    if feedback_service.posted_recently(feedback):
        return
    try:
        response = httpx.post(
            settings.SLACK_FEEDBACK_WEBHOOK_URL,
            json=feedback_service.slack_message(feedback),
            timeout=5,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Slack feedback webhook failed: %s", type(exc).__name__)
