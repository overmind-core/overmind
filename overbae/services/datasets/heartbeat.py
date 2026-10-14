"""Daemon threads beside a long task: proof that its worker is alive, and a cancellation watch."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from django.db import connections

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 30
WATCH_SECONDS = 1


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


@contextmanager
def watch(cancelled: Callable[[], bool], *, owner: str) -> Iterator[threading.Event]:
    """An event set once ``cancelled()`` holds, checked every second while the block runs."""
    seen = threading.Event()
    done = threading.Event()

    def poll() -> None:
        try:
            while not done.wait(WATCH_SECONDS):
                try:
                    if cancelled():
                        seen.set()
                        return
                except Exception:  # noqa: BLE001 — a database blip must not stop the watch
                    logger.exception("cancellation check failed for %s", owner)
        finally:
            connections.close_all()

    threading.Thread(target=poll, name=f"watch {owner}", daemon=True).start()
    try:
        yield seen
    finally:
        done.set()
