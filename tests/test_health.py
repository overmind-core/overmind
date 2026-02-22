"""Health and root endpoint tests."""

import pytest


@pytest.mark.asyncio
async def test_root(test_client):
    resp = await test_client.get("/")
    assert resp.status_code == 200
    assert resp.json()["message"] == "Welcome to Overmind Core"


@pytest.mark.asyncio
async def test_health(test_client):
    resp = await test_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
