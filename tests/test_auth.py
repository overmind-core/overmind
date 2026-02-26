"""Authentication edge-case tests: missing/invalid/expired credentials."""

import pytest


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer this.is.not.a.valid.jwt"},
        {"X-API-Token": "ovr_core_nonexistenttoken123456"},
    ],
    ids=["no-auth", "invalid-jwt", "invalid-api-token"],
)
async def test_invalid_credentials_return_401(test_client, db_session, headers):
    resp = await test_client.get("/api/v1/iam/users/me", headers=headers)
    assert resp.status_code == 401


async def test_deactivated_token_returns_401(
    seed_user, test_client, auth_headers, db_session
):
    """A token that has been deactivated should be rejected."""
    project = seed_user.project

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
