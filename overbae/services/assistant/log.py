"""The thread's event log and its live channel. Postgres is the record; Redis only tells
open streams to read again and carries the text of a reply while it is written."""

import json
import logging
import math
from functools import cache

import redis
from django.conf import settings
from django.db import transaction

from overbae.models import AssistantEvent, AssistantThread

logger = logging.getLogger(__name__)
DRAFT_TTL_SECONDS = 300


class LostClaimError(Exception):
    """Another step took the thread: this one must write nothing more."""


def channel(thread_id) -> str:
    return f"assistant:{thread_id}"


def draft_key(thread_id) -> str:
    return f"assistant:draft:{thread_id}"


@cache
def _redis(url: str):
    # Tests run on an in-memory broker: there is no stream to tell.
    if not url.startswith(("redis://", "rediss://", "unix://")):
        return None
    return redis.from_url(url, socket_timeout=1)


def _send(thread_id, commands) -> None:
    client = _redis(settings.CELERY_BROKER_URL)
    if client is None:
        return
    try:
        with client.pipeline() as pipe:
            commands(pipe)
            pipe.execute()
    except redis.RedisError:
        logger.warning("assistant stream publish failed for %s", thread_id, exc_info=True)


def changed(thread_id, *, clear_draft: bool = False) -> None:
    """Tell open streams that the thread, its queue or its events changed. Streams read
    Postgres on this signal, so it leaves only after the change is committed."""

    def commands(pipe):
        if clear_draft:
            pipe.delete(draft_key(thread_id))
        pipe.publish(channel(thread_id), json.dumps({"type": "changed"}))

    transaction.on_commit(lambda: _send(thread_id, commands))


def draft(thread_id, added: str, tools: list[str], *, at: int) -> None:
    """Send the text a reply gained after its first ``at`` characters. The key holds the
    whole draft for a stream that opens in the middle of it, and that stream may get a
    delta the key already held: the position makes it harmless. A reply's first delta drops
    what an abandoned reply left in the key."""

    def commands(pipe):
        if not at:
            pipe.delete(draft_key(thread_id))
        pipe.append(draft_key(thread_id), added)
        pipe.expire(draft_key(thread_id), DRAFT_TTL_SECONDS)
        message = {"type": "draft", "added": added, "at": at, "tools": tools}
        pipe.publish(channel(thread_id), json.dumps(message))

    _send(thread_id, commands)


def _storable(value):
    """``value`` as jsonb holds it: no NUL character and no number that is not finite. A
    kernel prints either, and so does a row of customer data."""
    if isinstance(value, str):
        return value.replace("\x00", "\ufffd")
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {_storable(key): _storable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_storable(item) for item in value]
    return value


def append(
    thread: AssistantThread, kind: str, payload: dict, *, claim=None, presentation=None
) -> AssistantEvent:
    """Add one event. With ``claim``, only while that claim still holds the thread."""
    payload, presentation = _storable(payload), _storable(presentation)
    with transaction.atomic():
        # The row lock orders concurrent appends; the unique constraint backs it.
        held = (
            AssistantThread.objects.select_for_update()
            .values_list("claim", flat=True)
            .get(pk=thread.pk)
        )
        if claim is not None and claim != held:
            raise LostClaimError
        last = thread.events.order_by("-sequence").values_list("sequence", flat=True).first()
        event = AssistantEvent.objects.create(
            thread=thread,
            sequence=(last or 0) + 1,
            kind=kind,
            payload=payload,
            presentation=presentation,
        )
    changed(thread.pk, clear_draft=kind in ("assistant", "status"))
    return event
