"""Write low-cardinality product milestones to the Overwatch PostHog project."""

from __future__ import annotations

import atexit
from threading import Lock
from uuid import UUID

from django.conf import settings
from posthog import Posthog

_lock = Lock()
_client: Posthog | None = None


def _get_client() -> Posthog | None:
    global _client
    if not settings.POSTHOG_PROJECT_TOKEN:
        return None
    with _lock:
        if _client is None:
            _client = Posthog(
                settings.POSTHOG_PROJECT_TOKEN,
                host=settings.POSTHOG_HOST,
                timeout=2,
                max_retries=0,
                flush_interval=1,
                max_queue_size=1000,
                enable_local_evaluation=False,
            )
        return _client


def flush() -> None:
    with _lock:
        if _client is not None:
            _client.flush(timeout_seconds=2)


atexit.register(flush)


def capture_funnel_step(*, event_id: UUID, distinct_id: str, step: str, source_at) -> bool:
    """Queue one idempotently identified funnel milestone without customer PII."""
    client = _get_client()
    if client is None:
        return False
    client.capture(
        "overwatch_funnel_step",
        distinct_id=distinct_id,
        uuid=str(event_id),
        timestamp=source_at,
        properties={
            "funnel_step": step,
            "source": "overmind_backend",
            "$process_person_profile": False,
        },
    )
    return True
