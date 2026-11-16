"""One live channel per dataset. An event names what changed, and an open page reads it
again from the API: Postgres is the record, so an event that is lost costs only a delay,
and a page that connects reads the current state, not a history."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

import redis as redis_lib
from django.conf import settings

logger = logging.getLogger(__name__)


def client():
    url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
    if not url.startswith(("redis://", "rediss://", "unix://")):
        return None
    return _client(os.getpid(), url)


@lru_cache(maxsize=4)
def _client(pid, url):
    return redis_lib.from_url(
        url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
    )


def channel(dataset_id: Any) -> str:
    return f"dataset:{dataset_id}"


def publish(dataset_id: Any, event: dict[str, Any]) -> None:
    try:
        r = client()
        if r is not None:
            r.publish(channel(dataset_id), json.dumps(event, default=str))
    except Exception:  # noqa: BLE001 — events are advisory, never fatal
        logger.warning("dataset event publish failed for %s", dataset_id, exc_info=True)


@lru_cache(maxsize=4)
def _signal_client(pid, url):
    # Runs on the request thread after commit, so an unreachable Redis must not stall it.
    return redis_lib.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.2)


def signal(name: str) -> None:
    url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
    if not url.startswith(("redis://", "rediss://", "unix://")):
        return
    try:
        _signal_client(os.getpid(), url).publish(name, "1")
    except Exception:  # noqa: BLE001 — events are advisory, never fatal
        logger.warning("event signal failed for %s", name, exc_info=True)
