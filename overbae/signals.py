import logging
import threading

import httpx
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from overbae.models import Capability, Feedback

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


@receiver(pre_save, sender=Capability)
def _stash_capability_card_change(sender, instance: Capability, **kwargs):
    """Any writer of ``improvement_metadata.capability_card`` (not just a scan)
    must refresh the managed Tier-0 evaluators, else live judges grade against
    a stale map until the next scan."""
    instance._card_changed = False
    if instance.pk is None:
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "improvement_metadata" not in update_fields:
        return
    # Fetch only the card: improvement_metadata also carries the eval matrix and
    # scan history, which can be orders of magnitude larger.
    old_card = (
        Capability.objects.filter(pk=instance.pk)
        .values_list("improvement_metadata__capability_card", flat=True)
        .first()
    )
    new_card = (instance.improvement_metadata or {}).get("capability_card")
    instance._card_changed = bool(new_card) and new_card != old_card


@receiver(post_save, sender=Capability)
def sync_evaluators_on_card_change(sender, instance: Capability, created: bool, **kwargs):
    # ``overmind sync`` enqueues full Default-set preload on create; this signal
    # handles later card edits with Tier-0 behaviour sync only.
    if created or not getattr(instance, "_card_changed", False):
        return
    instance._card_changed = False
    from overbae.tasks.eval import sync_card_evaluators_task  # noqa: PLC0415 — avoid cycle

    transaction.on_commit(lambda: sync_card_evaluators_task.delay(capability_id=str(instance.pk)))
