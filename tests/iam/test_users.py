"""IAM user endpoint tests: login, profile, password change."""

import pytest


async def test_login_success(seed_user, test_client):
    resp = await test_client.post(
        "/api/v1/iam/users/login",
        json={"email": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["user"]["email"] == "admin"


@pytest.mark.parametrize(
    "email,password",
    [
        ("admin", "wrongpassword"),
        ("nobody@example.com", "admin"),
        ("nobody@example.com", "wrongpassword"),
    ],
    ids=["wrong-password", "nonexistent-email", "both-wrong"],
)
async def test_login_failure(seed_user, test_client, email, password):
    resp = await test_client.post(
        "/api/v1/iam/users/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 401


async def test_get_me(seed_user, test_client, auth_headers):
    resp = await test_client.get("/api/v1/iam/users/me", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "admin"
    assert data["full_name"] == "Admin"


async def test_change_password(seed_user, test_client, auth_headers):
    resp = await test_client.put(
        "/api/v1/iam/users/me/password",
        headers=auth_headers,
        json={"current_password": "admin", "new_password": "newsecure123"},
    )
    assert resp.status_code == 200

    resp = await test_client.post(
        "/api/v1/iam/users/login",
        json={"email": "admin", "password": "admin"},
    )
    assert resp.status_code == 401

    resp = await test_client.post(
        "/api/v1/iam/users/login",
        json={"email": "admin", "password": "newsecure123"},
    )
    assert resp.status_code == 200


async def test_change_password_wrong_current(seed_user, test_client, auth_headers):
    resp = await test_client.put(
        "/api/v1/iam/users/me/password",
        headers=auth_headers,
        json={"current_password": "notright", "new_password": "newsecure123"},
    )
    assert resp.status_code == 400
