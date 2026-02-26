"""Prompt endpoint tests."""

import pytest
from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def _mock_celery_delay(monkeypatch):
    """Prevent prompt create from hitting a real Celery broker."""
    mock_task = MagicMock()
    mock_task.delay = MagicMock(return_value=MagicMock(id="fake-task-id"))
    monkeypatch.setattr(
        "overmind.api.v1.endpoints.prompts.generate_display_name_task", mock_task
    )
    monkeypatch.setattr(
        "overmind.api.v1.endpoints.prompts.generate_criteria_task", mock_task
    )


async def test_create_prompt(seed_user, test_client, auth_headers):
    project = seed_user.project
    resp = await test_client.post(
        "/api/v1/prompts/",
        headers=auth_headers,
        json={
            "slug": "greeting",
            "prompt": "You are a friendly assistant.",
            "project_id": str(project.project_id),
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_new"] is True
    assert data["version"] == 1


async def test_list_prompts(seed_user, test_client, auth_headers):
    project = seed_user.project
    await test_client.post(
        "/api/v1/prompts/",
        headers=auth_headers,
        json={
            "slug": "list-test",
            "prompt": "You answer questions.",
            "project_id": str(project.project_id),
        },
    )

    resp = await test_client.get(
        f"/api/v1/prompts/?project_id={project.project_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    slugs = [p["slug"] for p in data]
    assert "list-test" in slugs


async def test_create_prompt_dedup(seed_user, test_client, auth_headers):
    """Creating the same prompt content twice should return the existing version."""
    project = seed_user.project
    payload = {
        "slug": "dedup-test",
        "prompt": "Identical content for dedup testing.",
        "project_id": str(project.project_id),
    }

    resp1 = await test_client.post(
        "/api/v1/prompts/", headers=auth_headers, json=payload
    )
    resp2 = await test_client.post(
        "/api/v1/prompts/", headers=auth_headers, json=payload
    )

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["prompt_id"] == resp2.json()["prompt_id"]
    assert resp2.json()["is_new"] is False


@pytest.mark.parametrize(
    "payload,expected_status",
    [
        ({}, 422),
        ({"slug": "x", "prompt": "y"}, 422),
    ],
    ids=["empty-body", "missing-project-id"],
)
async def test_create_prompt_validation(
    seed_user, test_client, auth_headers, payload, expected_status
):
    resp = await test_client.post(
        "/api/v1/prompts/", headers=auth_headers, json=payload
    )
    assert resp.status_code == expected_status
