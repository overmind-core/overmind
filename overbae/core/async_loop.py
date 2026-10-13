"""One event loop per worker process for async Celery tasks.

`async_to_sync` on a ``--pool=threads`` worker builds a fresh event loop, a one-shot
thread and a fresh ``to_thread`` executor for every task call, so N concurrent tasks mean
N loops and N executors: module-level ``asyncio`` primitives bind to the first loop and
raise on the rest, and nothing bounds judge threads or memory across the process. Running
every task's coroutine on one shared loop fixes both: the semaphores and the ``to_thread``
executor are shared and bounded, and the loop is reused instead of rebuilt per call.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor

from asgiref.sync import sync_to_async
from django.db import close_old_connections

logger = logging.getLogger(__name__)

# The shared ``to_thread`` pool serves the blocking Jev and explanation calls; it is the
# true ceiling on judges in flight, so it tracks ``evals.MAX_CONCURRENT_JUDGES``.
_EXECUTOR_WORKERS = int(os.environ.get("ASYNC_LOOP_EXECUTOR_WORKERS", "32"))

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_loop_pid: int | None = None


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """The current process's loop, started once on a daemon thread. Recreated after a fork
    (a prefork child inherits no loop thread), so prefork and threads workers both work."""
    global _loop, _loop_pid
    pid = os.getpid()
    if _loop is not None and _loop_pid == pid and not _loop.is_closed():
        return _loop
    with _lock:
        if _loop is None or _loop_pid != pid or _loop.is_closed():
            loop = asyncio.new_event_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=_EXECUTOR_WORKERS))
            threading.Thread(target=loop.run_forever, name="celery-async-loop", daemon=True).start()
            _loop = loop
            _loop_pid = pid
    return _loop


async def _guarded[T](coro: Coroutine[object, object, T]) -> T:
    # Recycle a connection the loop's executor left stale between tasks, the way Celery's
    # signals do for the main thread; the loop's executor threads never see those signals.
    await sync_to_async(close_old_connections)()
    try:
        return await coro
    finally:
        await sync_to_async(close_old_connections)()


def run[T](coro: Coroutine[object, object, T]) -> T:
    """Runs ``coro`` on the worker's shared loop and blocks for its result."""
    return asyncio.run_coroutine_threadsafe(_guarded(coro), _ensure_loop()).result()
