"""Trace endpoint tests."""

import pytest
from uuid import uuid4


@pytest.mark.asyncio
async def test_list_traces_empty(seed_user, test_client, auth_headers):
    _, project, _ = seed_user
    resp = await test_client.get(
        f"/api/v1/traces/list?project_id={project.project_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["traces"] == []


@pytest.mark.asyncio
async def test_get_nonexistent_trace(seed_user, test_client, auth_headers):
    _, project, _ = seed_user
    fake_id = str(uuid4())
    resp = await test_client.get(
        f"/api/v1/traces/trace/{fake_id}?project_id={project.project_id}",
        headers=auth_headers,
    )
    # Nonexistent trace returns 404 or 200 with 0 spans depending on impl
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        data = resp.json()
        assert data.get("span_count", 0) == 0


@pytest.mark.asyncio
async def test_list_traces_missing_project_id(seed_user, test_client, auth_headers):
    resp = await test_client.get("/api/v1/traces/list", headers=auth_headers)
    assert resp.status_code == 422
