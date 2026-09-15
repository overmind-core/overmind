"""One SSE stream per dataset. Events are best-effort: a Redis hiccup never
fails the run that emitted, and a replay gap is harmless because the dataset
row is the source of truth."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_REPLAY_CAP = 300
_REPLAY_TTL_SECONDS = 6 * 3600


def _redis_client():
    import redis as redis_lib
    from django.conf import settings

    url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
    if not url.startswith(("redis://", "rediss://", "unix://")):
        return None
    return redis_lib.from_url(url, decode_responses=True)


def channel(dataset_id: Any) -> str:
    return f"dataset:{dataset_id}"


def _replay_key(dataset_id: Any) -> str:
    return f"dataset:events:{dataset_id}"


def publish(dataset_id: Any, event: dict[str, Any], *, client: Any = None) -> None:
    payload = json.dumps(event, default=str)
    try:
        r = client if client is not None else _redis_client()
        if r is None:
            return
        pipe = r.pipeline()
        pipe.publish(channel(dataset_id), payload)
        pipe.rpush(_replay_key(dataset_id), payload)
        pipe.ltrim(_replay_key(dataset_id), -_REPLAY_CAP, -1)
        pipe.expire(_replay_key(dataset_id), _REPLAY_TTL_SECONDS)
        pipe.execute()
    except Exception:  # noqa: BLE001 — events are advisory, never fatal
        logger.warning("dataset event publish failed for %s", dataset_id, exc_info=True)


def replay(dataset_id: Any, *, client: Any = None) -> list[dict[str, Any]]:
    try:
        r = client if client is not None else _redis_client()
        if r is None:
            return []
        raw = r.lrange(_replay_key(dataset_id), 0, -1)
    except Exception:  # noqa: BLE001
        logger.warning("dataset event replay failed for %s", dataset_id, exc_info=True)
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        try:
            out.append(json.loads(item))
        except ValueError:
            continue
    return out
