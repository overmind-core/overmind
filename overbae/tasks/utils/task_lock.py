import logging
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from typing import Any

from django.conf import settings
from redis.exceptions import LockNotOwnedError

logger = logging.getLogger(__name__)


def _broker_url() -> str:
    return getattr(settings, "CELERY_BROKER_URL", "redis://redis:6379/0")


def _is_redis_broker(url: str) -> bool:
    return url.startswith(("redis://", "rediss://", "unix://"))


def _get_redis_client():
    import redis

    return redis.from_url(_broker_url(), decode_responses=False)


def _renew(lock, timeout: int, stop: threading.Event) -> None:
    # A blip costs one extension, not the lock: the next try still lands within
    # the lease. Only a lock that is already gone ends the renewal.
    while not stop.wait(timeout / 3):
        try:
            lock.extend(timeout, replace_ttl=True)
        except LockNotOwnedError:
            logger.error("Lock %s lapsed while its holder still runs", lock.name)
            return
        except Exception:  # noqa: BLE001 — the holder keeps running; retry on the next beat
            logger.warning("Could not renew lock %s", lock.name, exc_info=True)


@contextmanager
def hold_one_slot(lock_name: str, slots: int, *, timeout: int):
    """Hold one of ``slots`` locks until the block exits.

    Modal's v1 volume rejects small commits once more than a handful of writers
    commit at once, so training uploads share five slots.
    """
    if slots < 1:
        raise ValueError("slots must be positive")
    if not _is_redis_broker(_broker_url()):
        yield
        return
    client = _get_redis_client()
    while True:
        for slot in range(slots):
            lock = client.lock(
                f"celery:lock:{lock_name}:{slot}",
                timeout=timeout,
                blocking_timeout=0,
                thread_local=False,
            )
            if not lock.acquire(blocking=False):
                continue
            stop = threading.Event()
            threading.Thread(
                target=_renew,
                args=(lock, timeout, stop),
                name=f"lock {lock_name}:{slot}",
                daemon=True,
            ).start()
            try:
                yield
            finally:
                stop.set()
                try:
                    lock.release()
                except LockNotOwnedError:
                    logger.error("Lock %s:%s lapsed while its holder still runs", lock_name, slot)
            return
        time.sleep(0.5)


@contextmanager
def acquire_task_lock(lock_name: str, *, timeout: int, blocking: bool = False, renew: bool = False):
    """Redis lock that a holder killed mid-task keeps for ``timeout`` seconds.

    Without ``renew`` the lock also lapses after ``timeout`` while its holder still
    runs, so frequent periodic tasks pass a short one rather than stay silenced by a
    stale lock. With ``renew`` it holds for as long as the holder lives, and
    ``timeout`` only bounds how long a dead holder blocks the next one.
    Non-Redis brokers (``memory://`` in tests) cannot host a distributed lock, so they
    always grant.
    """
    if not _is_redis_broker(_broker_url()):
        yield True
        return

    client = _get_redis_client()
    # Not thread-local: the renewing thread extends with the acquiring thread's token.
    lock = client.lock(
        f"celery:lock:{lock_name}", timeout=timeout, blocking_timeout=0, thread_local=False
    )
    stop = threading.Event()

    acquired = False
    try:
        acquired = lock.acquire(blocking=blocking)
        if acquired:
            logger.debug("Acquired lock for task: %s", lock_name)
            if renew:
                threading.Thread(
                    target=_renew, args=(lock, timeout, stop), name=f"lock {lock_name}", daemon=True
                ).start()
        else:
            logger.warning("Could not acquire lock for task: %s (already running)", lock_name)
        yield acquired
    finally:
        stop.set()
        if acquired:
            try:
                lock.release()
                logger.debug("Released lock for task: %s", lock_name)
            except Exception as e:
                logger.warning("Error releasing lock for task %s: %s", lock_name, e)


def with_task_lock(
    lock_name: str | None = None, blocking: bool = False, *, timeout: int, renew: bool = False
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
