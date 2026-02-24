"""Health and root endpoint tests."""

import pytest


@pytest.mark.asyncio
async def test_root(test_client):
    from overmind.main import FRONTEND_DIR

    resp = await test_client.get("/")
    assert resp.status_code == 200
    if FRONTEND_DIR.is_dir():
        assert "text/html" in resp.headers.get("content-type", "")
    else:
        assert "message" in resp.json()


@pytest.mark.asyncio
async def test_health(test_client):
    resp = await test_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
