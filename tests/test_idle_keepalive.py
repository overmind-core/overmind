from __future__ import annotations

import asyncio
import time

import pytest

from overbae.api.streaming import aiter_keeping_idle_alive, iter_keeping_idle_alive


def test_first_item_is_ping():
    def source():
        yield "a"

    assert list(iter_keeping_idle_alive(source(), ping="p", interval_s=10)) == ["p", "a"]


def test_slow_source_emits_extra_pings():
    def source():
        time.sleep(0.25)
        yield "a"

    items = list(iter_keeping_idle_alive(source(), ping="p", interval_s=0.1))
    assert items[0] == "p"
    assert items[-1] == "a"
    assert items.count("p") >= 2


def test_exception_propagates_after_the_leading_ping():
    def source():
        yield from ()
        raise ValueError("boom")

    it = iter_keeping_idle_alive(source(), ping="p", interval_s=10)
    assert next(it) == "p"
    with pytest.raises(ValueError, match="boom"):
        next(it)


def test_aiter_first_item_is_ping():
    async def source():
        yield "a"

    async def collect():
        return [x async for x in aiter_keeping_idle_alive(source(), ping="p", interval_s=10)]

    assert asyncio.run(collect()) == ["p", "a"]


def test_aiter_slow_source_emits_extra_pings():
    async def source():
        await asyncio.sleep(0.25)
        yield "a"

    async def collect():
        return [x async for x in aiter_keeping_idle_alive(source(), ping="p", interval_s=0.1)]

    items = asyncio.run(collect())
    assert items[0] == "p"
    assert items[-1] == "a"
    assert items.count("p") >= 2


def test_aiter_exception_propagates_after_the_leading_ping():
    async def source():
        if False:
            yield "x"
        raise ValueError("boom")

    async def run():
        it = aiter_keeping_idle_alive(source(), ping="p", interval_s=10)
        assert await anext(it) == "p"
        with pytest.raises(ValueError, match="boom"):
            await anext(it)

    asyncio.run(run())


def test_close_stops_draining():
    n = 0

    def source():
        nonlocal n
        while True:
            n += 1
            yield n
            time.sleep(0.05)

    it = iter_keeping_idle_alive(source(), ping="p", interval_s=10)
    assert next(it) == "p"
    assert next(it) == 1
    it.close()
    frozen = n
    time.sleep(0.2)
    assert n <= frozen + 1


def test_aiter_aclose_cancels_producer():
    continued = False

    async def source():
        nonlocal continued
        yield "a"
        await asyncio.sleep(60)
        continued = True
        yield "b"

    async def run():
        it = aiter_keeping_idle_alive(source(), ping="p", interval_s=10)
        assert await anext(it) == "p"
        assert await anext(it) == "a"
        await it.aclose()
        await asyncio.sleep(0.05)
        assert continued is False

    asyncio.run(run())
