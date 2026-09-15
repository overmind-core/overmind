import logging
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def _broker_url() -> str:
    return getattr(settings, "CELERY_BROKER_URL", "redis://redis:6379/0")


def _is_redis_broker(url: str) -> bool:
    return url.startswith(("redis://", "rediss://", "unix://"))


def _get_redis_client():
    import redis

    return redis.from_url(_broker_url(), decode_responses=False)


_DEFAULT_SAFETY_TIMEOUT = 7 * 24 * 60 * 60


@contextmanager
def acquire_task_lock(
    lock_name: str, blocking: bool = False, timeout: int = _DEFAULT_SAFETY_TIMEOUT
):
    """Redis lock with a safety expiry.

    ``timeout`` is the lock's max lifetime — a holder killed mid-task keeps it until then,
    so frequent periodic tasks must pass a short one or stay silenced by a stale lock.
    Non-Redis brokers (``memory://`` in tests) cannot host a distributed lock, so they
    always grant.
    """
    if not _is_redis_broker(_broker_url()):
        yield True
        return

    client = _get_redis_client()
    lock = client.lock(f"celery:lock:{lock_name}", timeout=timeout, blocking_timeout=0)

    acquired = False
    try:
        acquired = lock.acquire(blocking=blocking)
        if acquired:
            logger.debug("Acquired lock for task: %s", lock_name)
        else:
            logger.warning("Could not acquire lock for task: %s (already running)", lock_name)
        yield acquired
    finally:
        if acquired:
            try:
                lock.release()
                logger.debug("Released lock for task: %s", lock_name)
            except Exception as e:
                logger.warning("Error releasing lock for task %s: %s", lock_name, e)


def with_task_lock(
    lock_name: str | None = None,
    blocking: bool = False,
    timeout: int = _DEFAULT_SAFETY_TIMEOUT,
):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            task_lock_name = lock_name or func.__name__
            with acquire_task_lock(task_lock_name, blocking=blocking, timeout=timeout) as acquired:
                if not acquired:
                    return {"status": "skipped", "reason": "previous_task_still_running"}
                return func(*args, **kwargs)

        return wrapper

    return decorator
