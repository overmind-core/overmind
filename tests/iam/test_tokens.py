"""IAM token CRUD endpoint tests."""

import pytest


@pytest.mark.asyncio
async def test_create_token(seed_user, test_client, auth_headers):
    _, project, _ = seed_user
    resp = await test_client.post(
        "/api/v1/iam/tokens/",
        headers=auth_headers,
        json={
            "name": "New Token",
            "project_id": str(project.project_id),
            "expires_in_days": 30,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "New Token"
    assert data["token"].startswith("ovr_core_")
    assert data["project_id"] == str(project.project_id)


@pytest.mark.asyncio
async def test_list_tokens(seed_user, test_client, auth_headers):
    _, project, _ = seed_user
    resp = await test_client.get(
        f"/api/v1/iam/tokens/?project_id={project.project_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] >= 1


@pytest.mark.asyncio
async def test_update_token(seed_user, test_client, auth_headers, db_session):
    _, project, _ = seed_user
    from overmind_core.models.iam.tokens import Token
    from sqlalchemy import select

    token_row = (
        await db_session.execute(
            select(Token).where(Token.project_id == project.project_id)
        )
    ).scalar_one()

    resp = await test_client.put(
        f"/api/v1/iam/tokens/{token_row.token_id}",
        headers=auth_headers,
        json={"name": "Renamed Token"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed Token"


@pytest.mark.asyncio
async def test_delete_token(seed_user, test_client, auth_headers):
    _, project, _ = seed_user
    # Create one to delete
    create_resp = await test_client.post(
        "/api/v1/iam/tokens/",
        headers=auth_headers,
        json={"name": "Temp Token", "project_id": str(project.project_id)},
    )
    token_id = create_resp.json()["token_id"]

    resp = await test_client.delete(
        f"/api/v1/iam/tokens/{token_id}", headers=auth_headers
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_duplicate_token_returns_409(seed_user, test_client, auth_headers):
    _, project, _ = seed_user
    payload = {"name": "DuplicateMe", "project_id": str(project.project_id)}
    await test_client.post("/api/v1/iam/tokens/", headers=auth_headers, json=payload)
    resp = await test_client.post(
        "/api/v1/iam/tokens/", headers=auth_headers, json=payload
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_created_token_authenticates_requests(seed_user, test_client, auth_headers):
    """A newly created API token should work for authentication."""
    _, project, _ = seed_user
    create_resp = await test_client.post(
        "/api/v1/iam/tokens/",
        headers=auth_headers,
        json={"name": "Auth Test Token", "project_id": str(project.project_id)},
    )
    plain_token = create_resp.json()["token"]

    resp = await test_client.get(
        "/api/v1/iam/users/me", headers={"X-API-Token": plain_token}
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin"
