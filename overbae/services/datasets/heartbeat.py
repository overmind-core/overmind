"""Proof that a long task's worker is alive, written from a daemon thread."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from django.db import connections

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 30
STALE_SECONDS = 5 * 60


@contextmanager
def beating(touch: Callable[[], object]) -> Iterator[None]:
    stop = threading.Event()

    def beat() -> None:
        try:
            while not stop.wait(INTERVAL_SECONDS):
                try:
                    touch()
                except Exception:  # noqa: BLE001 — a database blip must not stop the beat
                    logger.exception("heartbeat failed")
        finally:
            connections.close_all()

    thread = threading.Thread(target=beat, name="heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
