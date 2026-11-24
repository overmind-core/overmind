"""Proof that a long task's worker is alive, written from a daemon thread."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from django.db import connections
from django.db.models import Q, QuerySet
from django.utils import timezone

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 30
STALE_SECONDS = 5 * 60
# A job that loses its worker this often is failing on its own, not being stopped.
MAX_ATTEMPTS = 3
STOPPED = f"The worker stopped {MAX_ATTEMPTS} times before this finished."


def start(touch: Callable[[], object], *, owner: str) -> threading.Event:
    stop = threading.Event()

    def beat() -> None:
        try:
            while not stop.wait(INTERVAL_SECONDS):
                try:
                    touch()
                except Exception:  # noqa: BLE001 — a database blip must not stop the beat
                    logger.exception("heartbeat failed for %s", owner)
        finally:
            connections.close_all()

    threading.Thread(target=beat, name=f"heartbeat {owner}", daemon=True).start()
    return stop


def lease_end() -> datetime:
    return timezone.now() + timedelta(seconds=STALE_SECONDS)


def lapsed() -> Q:
    """Rows whose holder stopped renewing; a null lease predates leases."""
    return Q(lease_until__isnull=True) | Q(lease_until__lt=timezone.now())


def take(job, *, running: bool) -> str:
    """On a ``Leased`` row locked FOR UPDATE: ``"held"`` while a live worker renews it,
    ``"exhausted"`` once its worker was lost too often, else ``"taken"`` under a new
    attempt (unsaved). ``running`` says whether some worker started it before."""
    if running:
        if job.lease_until is not None and job.lease_until > timezone.now():
            return "held"
        if job.attempts >= MAX_ATTEMPTS - 1:
            return "exhausted"
        job.attempts += 1
    else:
        job.attempts = 0
    job.attempt, job.lease_until = uuid.uuid4(), lease_end()
    return "taken"


def hold(rows: QuerySet, *, owner: str, field: str = "lease_until") -> threading.Event:
    """Renew ``field`` on ``rows`` until the event is set. ``rows`` names the holder, so a
    worker whose job was taken over renews nothing."""
    return start(lambda: rows.update(**{field: lease_end()}), owner=owner)
