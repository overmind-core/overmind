import asyncio
import json
import threading

import pytest
from django.core.cache import cache

from overbae.models import APIToken, Project, User
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.covers("capacity")


def test_uncached_catalog_fetch_leaves_the_event_loop_available(scripted):
    cache.clear()
    started, release = threading.Event(), threading.Event()
    observed = []

    def response(call):
        started.set()
        observed.append(release.wait(3))
        return 200, {"content-type": "application/json"}, json.dumps({"data": []}).encode()

    scripted("https://openrouter.ai/api/v1/models").then(response)
    context = MCPContext(
        user=User(email="catalog@test.com"),
        token=APIToken(scope={"permission": ["read"]}),
        project=Project(name="Catalog"),
    )

    async def check():
        async def heartbeat():
            assert await asyncio.to_thread(started.wait, 3)
            release.set()

        result, _ = await asyncio.gather(
            CATALOG.call("get_model_catalog", {}, context), heartbeat()
        )
        assert not result.isError

    try:
        asyncio.run(check())
        assert observed == [True]
    finally:
        release.set()
        cache.clear()
