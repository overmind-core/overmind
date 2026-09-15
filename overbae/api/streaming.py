"""Bridge sync SSE generators onto the ASGI response path.

Handed a sync iterator, `StreamingHttpResponse.__aiter__` falls back to
`sync_to_async(list)(...)` and materializes the whole body before writing a byte, so under
uvicorn every SSE endpoint delivers one post-completion flush and time-to-first-token equals
total latency. The generators stay sync because they close over Django ORM calls; only the
iteration is bridged, one item at a time.
"""

from __future__ import annotations

import asyncio
import contextlib
import queue
import threading
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any

from asgiref.sync import sync_to_async

_DONE = object()

# Edge idle timeout is 60s with no bytes. Modal cold boot is 2–7 min, so the
# generator must write before the HTTP call to vLLM returns.
IDLE_PING_INTERVAL_S = 15.0
SSE_IDLE_PING = ": \n\n"
JSON_IDLE_PING = "\n"


class _End:
    pass


class _Fail:
    __slots__ = ("exc",)

    def __init__(self, exc: BaseException):
        self.exc = exc


def iter_keeping_idle_alive[T](
    source: Iterable[T],
    *,
    ping: T,
    interval_s: float = IDLE_PING_INTERVAL_S,
) -> Iterator[T]:
    """Yield `ping` immediately and whenever `source` is silent for `interval_s`.

    `source` runs in a thread because it blocks on HTTP. This iterator stays on
    the request thread (`AsyncStream` is thread_sensitive) so ORM in the
    caller's `finally` still sees the request connection.
    """
    q: queue.Queue[T | _End | _Fail] = queue.Queue()
    stopped = threading.Event()

    def _produce() -> None:
        it = iter(source)
        try:
            for item in it:
                if stopped.is_set():
                    break
                q.put(item)
        except BaseException as exc:
            if not stopped.is_set():
                q.put(_Fail(exc))
        else:
            if not stopped.is_set():
                q.put(_End())
        finally:
            close = getattr(it, "close", None)
            if close is not None:
                close()

    threading.Thread(target=_produce, daemon=True).start()
    try:
        yield ping
        while True:
            try:
                item = q.get(timeout=interval_s)
            except queue.Empty:
                yield ping
                continue
            if isinstance(item, _End):
                return
            if isinstance(item, _Fail):
                raise item.exc
            yield item
    finally:
        # ponytail: requests POST can't be aborted while blocked; an in-flight
        # non-stream call still finishes after hangup. Close the Response if
        # that GPU time shows up on the bill.
        stopped.set()


async def aiter_keeping_idle_alive[T](
    source: AsyncIterator[T],
    *,
    ping: T,
    interval_s: float = IDLE_PING_INTERVAL_S,
) -> AsyncIterator[T]:
    """Async counterpart of `iter_keeping_idle_alive`."""
    q: asyncio.Queue[T | _End | _Fail] = asyncio.Queue()

    async def _produce() -> None:
        try:
            async for item in source:
                await q.put(item)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await q.put(_Fail(exc))
        else:
            await q.put(_End())
        finally:
            aclose = getattr(source, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(aclose())

    task = asyncio.create_task(_produce())
    try:
        yield ping
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=interval_s)
            except TimeoutError:
                yield ping
                continue
            if isinstance(item, _End):
                return
            if isinstance(item, _Fail):
                raise item.exc
            yield item
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)


class AsyncStream:
    """Async-iterable view over a sync iterable, for `StreamingHttpResponse`."""

    def __init__(self, source: Iterable[Any]) -> None:
        self._source = source
        self._iterator = iter(source)

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._pump()

    async def _pump(self) -> AsyncIterator[Any]:
        # thread_sensitive keeps every step on the request's single sync thread, so the
        # generator's ORM writes and connection cleanup behave as they do in a sync view.
        pull = sync_to_async(self._next, thread_sensitive=True)
        while True:
            item = await pull()
            if item is _DONE:
                return
            yield item

    def _next(self) -> Any:
        return next(self._iterator, _DONE)

    def close(self) -> None:
        """Django registers this as a resource closer, so a client disconnect still runs the
        source generator's `finally` (usage recording, credit charges)."""
        closer = getattr(self._source, "close", None)
        if closer is not None:
            closer()
