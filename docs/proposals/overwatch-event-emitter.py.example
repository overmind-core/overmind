"""Write low-cardinality product milestones to the Overwatch PostHog project."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import requests
from django.conf import settings
from django.utils import timezone

from overbae.services.overwatch_privacy import posthog_url
from overbae.services.user_insights import ACTIVATION_STEPS


def capture_funnel_step(
    *, event_id: UUID, distinct_id: str, step: str, source_at: datetime
) -> bool:
    """Wait for ingestion acceptance before advancing the durable delivery ledger."""
    if not settings.POSTHOG_PROJECT_TOKEN:
        return False
    if (
        step not in ACTIVATION_STEPS
        or not distinct_id
        or len(distinct_id) > 200
        or "@" in distinct_id
        or timezone.is_naive(source_at)
        or source_at > timezone.now()
    ):
        raise ValueError("invalid_funnel_event")
    response = requests.post(
        posthog_url(settings.POSTHOG_HOST, "/i/v0/e/", ingest=True),
        json={
            "api_key": settings.POSTHOG_PROJECT_TOKEN,
            "event": "overwatch_funnel_step",
            "distinct_id": distinct_id,
            "uuid": str(event_id),
            "timestamp": source_at.isoformat(),
            "properties": {
                "funnel_step": step,
                "source": "overmind_backend",
                "$process_person_profile": False,
            },
        },
        timeout=(2, 5),
        allow_redirects=False,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError("posthog_ingestion_rejected")
    return True
