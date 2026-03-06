"""Polling utilities for waiting on async Celery-driven processing."""

from __future__ import annotations

import time
import logging
from typing import Any, TypeVar
from collections.abc import Callable

T = TypeVar("T")
logger = logging.getLogger(__name__)


def poll_until(
    check_fn: Callable[[], T | None],
    timeout_s: float = 300,
    interval_s: float = 10,
    description: str = "condition",
) -> T:
    """
    Call *check_fn* every *interval_s* seconds until it returns a truthy value
    or *timeout_s* seconds have elapsed.

    Returns the truthy value on success, raises TimeoutError otherwise.
    """
    deadline = time.monotonic() + timeout_s
    attempt = 0
    last_result = None
    while True:
        attempt += 1
        result = check_fn()
        if result:
            elapsed = timeout_s - (deadline - time.monotonic())
            logger.info(
                "%s satisfied after %d attempts (%.1fs)",
                description,
                attempt,
                elapsed,
            )
            return result

        last_result = result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Timed out after {timeout_s}s waiting for: {description} "
                f"({attempt} attempts, last result: {last_result!r})"
            )
        sleep_time = min(interval_s, remaining)
        logger.debug(
            "%s not ready (attempt %d, %.0fs remaining), sleeping %.0fs…",
            description,
            attempt,
            remaining,
            sleep_time,
        )
        time.sleep(sleep_time)


def wait_for_job_completion(
    client: Any,
    job_id: str,
    timeout_s: float = 600,
    interval_s: float = 15,
) -> dict:
    """Poll a job until it reaches a terminal status (completed, failed, etc.)."""
    terminal = {"completed", "partially_completed", "failed", "cancelled"}

    def _check() -> dict | None:
        job = client.get_job(job_id)
        status = job["status"]
        logger.info("Job %s status: %s", job_id, status)
        if status in terminal:
            return job
        return None

    return poll_until(
        _check,
        timeout_s=timeout_s,
        interval_s=interval_s,
        description=f"job {job_id} completion",
    )
