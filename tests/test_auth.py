"""Authentication edge-case tests: missing/invalid/expired credentials."""

import pytest


@pytest.mark.asyncio
async def test_no_auth_returns_401(test_client, db_session):
    """Request without any auth header is rejected."""
    resp = await test_client.get("/api/v1/iam/users/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_jwt_returns_401(test_client, db_session):
    resp = await test_client.get(
        "/api/v1/iam/users/me",
        headers={"Authorization": "Bearer this.is.not.a.valid.jwt"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_api_token_returns_401(test_client, db_session):
    resp = await test_client.get(
        "/api/v1/iam/users/me",
        headers={"X-API-Token": "ovr_core_nonexistenttoken123456"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_deactivated_token_returns_401(
    seed_user, test_client, auth_headers, db_session
):
    """A token that has been deactivated should be rejected."""
    _, project, _ = seed_user

    # Create a token then deactivate it
    create_resp = await test_client.post(
        "/api/v1/iam/tokens/",
        headers=auth_headers,
        json={"name": "DeactivateMe", "project_id": str(project.project_id)},
    )
    token_id = create_resp.json()["token_id"]
    plain_token = create_resp.json()["token"]

    await test_client.put(
        f"/api/v1/iam/tokens/{token_id}",
        headers=auth_headers,
        json={"is_active": False},
    )

    resp = await test_client.get(
        "/api/v1/iam/users/me",
        headers={"X-API-Token": plain_token},
    )
    assert resp.status_code == 401
