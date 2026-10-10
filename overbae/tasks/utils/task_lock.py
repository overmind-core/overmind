import logging
import threading
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
    lock_name: str,
    blocking: bool = False,
    timeout: int = _DEFAULT_SAFETY_TIMEOUT,
    *,
    renew: bool = False,
):
    """Redis lock with a safety expiry.

    ``timeout`` bounds an abandoned lease. Renewing collectors keep a short lease
    while alive; killing their process must not silence later observations for minutes.
    Non-Redis brokers (``memory://`` in tests) cannot host a distributed lock, so they
    always grant.
    """
    if not _is_redis_broker(_broker_url()):
        yield True
        return

    client = _get_redis_client()
    lock = client.lock(
        f"celery:lock:{lock_name}", timeout=timeout, blocking_timeout=0, thread_local=not renew
    )
    stopped = threading.Event()
    renewer = None

    def maintain():
        while not stopped.wait(timeout / 3):
            try:
                lock.extend(timeout, replace_ttl=True)
            except Exception:
                logger.exception("Lease renewal failed for task %s", lock_name)
                return

    acquired = False
    try:
        acquired = lock.acquire(blocking=blocking)
        if acquired:
            logger.debug("Acquired lock for task: %s", lock_name)
            if renew:
                renewer = threading.Thread(target=maintain, daemon=True)
                renewer.start()
        else:
            logger.warning("Could not acquire lock for task: %s (already running)", lock_name)
        yield acquired
    finally:
        stopped.set()
        if renewer is not None:
            renewer.join(timeout=1)
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
    renew: bool = False,
):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            task_lock_name = lock_name or func.__name__
            with acquire_task_lock(
                task_lock_name, blocking=blocking, timeout=timeout, renew=renew
            ) as acquired:
                if not acquired:
                    return {"status": "skipped", "reason": "previous_task_still_running"}
                return func(*args, **kwargs)

        return wrapper

    return decorator
