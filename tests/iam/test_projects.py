"""IAM project CRUD endpoint tests."""

import pytest
from uuid import uuid4


async def test_create_project(seed_user, test_client, auth_headers):
    resp = await test_client.post(
        "/api/v1/iam/projects/",
        headers=auth_headers,
        json={"name": "My New Project", "description": "Testing"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "My New Project"
    assert data["project_id"]


async def test_list_projects(seed_user, test_client, auth_headers):
    resp = await test_client.get("/api/v1/iam/projects/", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] >= 1
    slugs = [p["name"] for p in data["projects"]]
    assert "Default Project" in slugs


async def test_get_project(seed_user, test_client, auth_headers):
    project = seed_user.project
    resp = await test_client.get(
        f"/api/v1/iam/projects/{project.project_id}", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Default Project"


async def test_update_project(seed_user, test_client, auth_headers):
    project = seed_user.project
    resp = await test_client.put(
        f"/api/v1/iam/projects/{project.project_id}",
        headers=auth_headers,
        json={"name": "Renamed Project", "description": "Updated description"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed Project"


async def test_delete_project(seed_user, test_client, auth_headers):
    project = seed_user.project
    resp = await test_client.delete(
        f"/api/v1/iam/projects/{project.project_id}", headers=auth_headers
    )
    assert resp.status_code == 200

    resp = await test_client.get(
        f"/api/v1/iam/projects/{project.project_id}", headers=auth_headers
    )
    assert resp.status_code == 404


async def test_create_duplicate_project_returns_409(
    seed_user, test_client, auth_headers
):
    await test_client.post(
        "/api/v1/iam/projects/",
        headers=auth_headers,
        json={"name": "UniqueProject"},
    )
    resp = await test_client.post(
        "/api/v1/iam/projects/",
        headers=auth_headers,
        json={"name": "UniqueProject"},
    )
    assert resp.status_code == 409


async def test_get_nonexistent_project(seed_user, test_client, auth_headers):
    resp = await test_client.get(
        f"/api/v1/iam/projects/{uuid4()}", headers=auth_headers
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "payload,expected_status",
    [
        ({}, 422),
    ],
    ids=["empty-body"],
)
async def test_create_project_validation(
    seed_user, test_client, auth_headers, payload, expected_status
):
    resp = await test_client.post(
        "/api/v1/iam/projects/", headers=auth_headers, json=payload
    )
    assert resp.status_code == expected_status
