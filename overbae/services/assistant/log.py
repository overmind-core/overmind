"""The conversation's event log and its live signal. Postgres is the record; the dataset's
event channel only tells open pages to read again, and carries a reply while it is written."""

import logging
import math

from django.db import transaction

from overbae.models import AssistantEvent, AssistantThread
from overbae.services.datasets import events

logger = logging.getLogger(__name__)
DRAFT_TTL_SECONDS = 300


class LostTurnError(Exception):
    """A newer turn took the thread: this one must write nothing more."""


def draft_key(thread_id) -> str:
    return f"assistant:draft:{thread_id}"


def changed(thread: AssistantThread) -> None:
    """Tell open pages that the conversation changed. They read Postgres on this signal, so
    it leaves only after the change is committed."""
    dataset = thread.dataset_id
    transaction.on_commit(lambda: events.publish(dataset, {"type": "assistant"}))


def draft(thread: AssistantThread, added: str, steps: list[str], *, at: int) -> None:
    """Send the text a reply gained after its first ``at`` characters, and the summaries of
    the calls it writes. The key holds the whole text for a page that opens in the middle
    of the reply; a gain the page already holds changes nothing, by its position."""
    client = events.client()
    if client is None:
        return
    try:
        with client.pipeline() as pipe:
            key = draft_key(thread.pk)
            if not at:
                pipe.delete(key)
            pipe.append(key, added)
            pipe.expire(key, DRAFT_TTL_SECONDS)
            pipe.execute()
    except Exception:  # noqa: BLE001 - the reply is saved whole when it ends
        logger.warning("assistant draft failed for %s", thread.pk, exc_info=True)
    events.publish(
        thread.dataset_id, {"type": "assistant.draft", "added": added, "at": at, "steps": steps}
    )


def _storable(value):
    """``value`` as jsonb holds it: no NUL character and no number that is not finite. A
    kernel prints either, and so does a row of customer data."""
    if isinstance(value, str):
        return value.replace("\x00", "�")
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {_storable(key): _storable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_storable(item) for item in value]
    return value


def append(
    thread: AssistantThread, kind: str, payload: dict, *, turn=None, presentation=None
) -> AssistantEvent:
    """Add one event. With ``turn``, only while that turn still holds the thread."""
    payload, presentation = _storable(payload), _storable(presentation)
    with transaction.atomic():
        # The row lock orders concurrent appends; the unique constraint backs it.
        held = (
            AssistantThread.objects.select_for_update()
            .values_list("turn", flat=True)
            .get(pk=thread.pk)
        )
        if turn is not None and turn != held:
            raise LostTurnError
        last = thread.events.order_by("-sequence").values_list("sequence", flat=True).first()
        event = AssistantEvent.objects.create(
            thread=thread,
            sequence=(last or 0) + 1,
            kind=kind,
            payload=payload,
            presentation=presentation,
        )
        if kind in (AssistantEvent.Kind.ASSISTANT, AssistantEvent.Kind.STATUS):
            key = draft_key(thread.pk)
            transaction.on_commit(lambda: _drop(key))
        changed(thread)
    return event


def _drop(key: str) -> None:
    client = events.client()
    if client is not None:
        try:
            client.delete(key)
        except Exception:  # noqa: BLE001 - it expires
            logger.debug("assistant draft key not dropped", exc_info=True)
