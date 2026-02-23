"""
Utility for preventing concurrent executions of periodic Celery tasks using Valkey locks.
"""

import logging
from contextlib import contextmanager
from functools import wraps
from typing import Any
from collections.abc import Callable
from valkey import Valkey

from overmind_core.config import settings

logger = logging.getLogger(__name__)


def get_valkey_client() -> Valkey:
    """
    Get a Valkey client instance for locking.

    Returns:
        Valkey client connected to the configured Valkey instance
    """
    return Valkey(
        host=settings.valkey_host,
        port=settings.valkey_port,
        db=settings.valkey_db,
        password=settings.valkey_auth_token if settings.valkey_auth_token else None,
        ssl=True if settings.valkey_auth_token else False,
        decode_responses=False,
    )


@contextmanager
def acquire_task_lock(
    lock_name: str,
    blocking: bool = False,
):
    """
    Context manager for acquiring a distributed lock for a task.

    The lock will be held until the task completes. A safety timeout of 7 days
    is set to prevent deadlocks if a worker crashes.

    Args:
        lock_name: Unique name for the lock (e.g., 'task:agent_discovery')
        blocking: Whether to wait for the lock to be available (default: False)

    Yields:
        bool: True if lock was acquired, False otherwise

    Example:
        with acquire_task_lock('my_task') as acquired:
            if not acquired:
                logger.info("Task already running, skipping")
                return
            # Do work here
    """
    valkey_client = get_valkey_client()
    # Use 7 days as safety timeout to prevent deadlocks if worker crashes
    # Lock will be released when task completes normally
    safety_timeout = 7 * 24 * 60 * 60  # 7 days in seconds

    lock = valkey_client.lock(
        f"celery:lock:{lock_name}",
        timeout=safety_timeout,
        blocking_timeout=0,  # Non-blocking
    )

    acquired = False
    try:
        acquired = lock.acquire(blocking=blocking)
        if acquired:
            logger.info(f"Acquired lock for task: {lock_name}")
        else:
            logger.info(
                f"Could not acquire lock for task: {lock_name} (task already running)"
            )
        yield acquired
    finally:
        if acquired:
            try:
                lock.release()
                logger.info(f"Released lock for task: {lock_name}")
            except Exception as e:
                logger.warning(f"Error releasing lock for task {lock_name}: {e}")


def with_task_lock(
    lock_name: str | None = None,
    blocking: bool = False,
):
    """
    Decorator for Celery tasks to ensure only one instance runs at a time.

    If a task is already running, new executions will be cancelled (skipped).
    The lock is held until the task completes, with a 7-day safety timeout
    to prevent deadlocks if a worker crashes.

    Args:
        lock_name: Unique name for the lock. If None, uses the task name
        blocking: Whether to wait for the lock to be available (default: False)

    Returns:
        Decorator function

    Example:
        @celery_app.task(name="my_task.process")
        @with_task_lock()
        def process_data():
            # Task logic here
            return {"status": "success"}
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Use provided lock_name or fall back to function name
            task_lock_name = lock_name or func.__name__

            with acquire_task_lock(task_lock_name, blocking=blocking) as acquired:
                if not acquired:
                    return {
                        "status": "skipped",
                        "reason": "previous_task_still_running",
                        "message": f"Task {task_lock_name} is already running, skipped this execution",
                    }

                # Execute the actual task
                return func(*args, **kwargs)

        return wrapper

    return decorator
