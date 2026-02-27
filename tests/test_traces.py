"""Trace endpoint tests."""

import pytest
from uuid import uuid4


async def test_list_traces_empty(seed_user, test_client, auth_headers):
    project = seed_user.project
    resp = await test_client.get(
        f"/api/v1/traces/list?project_id={project.project_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["traces"] == []


async def test_get_nonexistent_trace(seed_user, test_client, auth_headers):
    project = seed_user.project
    fake_id = str(uuid4())
    resp = await test_client.get(
        f"/api/v1/traces/trace/{fake_id}?project_id={project.project_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_list_traces_missing_project_id(seed_user, test_client, auth_headers):
    resp = await test_client.get("/api/v1/traces/list", headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "query,expected_status",
    [
        ("project_id=not-a-uuid", 422),
        (f"project_id={uuid4()}", 200),
    ],
    ids=["bad-uuid", "nonexistent-project"],
)
async def test_list_traces_invalid_params(
    seed_user, test_client, auth_headers, query, expected_status
):
    resp = await test_client.get(f"/api/v1/traces/list?{query}", headers=auth_headers)
    assert resp.status_code == expected_status
