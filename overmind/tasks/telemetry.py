"""
Daily telemetry heartbeat — sends anonymous usage stats to PostHog.

Runs at every 30 minutes via Celery Beat. Skipped silently if telemetry is disabled
or the PostHog API key is not configured.
"""

import asyncio
import logging

from celery import shared_task

from overmind.config import settings
from overmind.db.session import dispose_engine, get_session_local
from overmind.tasks.utils.task_lock import with_task_lock
from overmind.telemetry import TelemetryReporter

logger = logging.getLogger(__name__)


async def _send_heartbeat() -> dict:
    try:
        AsyncSessionLocal = get_session_local()
        async with AsyncSessionLocal() as db:
            reporter = TelemetryReporter()
            payload = await reporter.collect(db)
            reporter.send(payload)
            return {"status": "sent"}
    except Exception as exc:
        logger.debug("Telemetry heartbeat failed (non-critical): %s", exc)
        return {"status": "failed", "error": str(exc)}
    finally:
        # Dispose both the SQLAlchemy engine and the async Valkey (GlideClient)
        # connection. Each asyncio.run() creates a fresh event loop; leaving
        # either client alive would bind it to a dead loop on the next run.
        from overmind.db.valkey import close_valkey_client

        await dispose_engine()
        await close_valkey_client()


@shared_task(name="telemetry.send_heartbeat")
@with_task_lock("telemetry-heartbeat")
def send_heartbeat() -> dict:
    """Send anonymous usage heartbeat to PostHog.

    Runs every 30 minutes via Celery Beat.
    Protected by a distributed lock so only one worker runs it at a time.
    """
    if not settings.overmind_analytics_enabled:
        return {"status": "skipped", "reason": "telemetry_disabled"}

    return asyncio.run(_send_heartbeat())
