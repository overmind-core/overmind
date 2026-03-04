"""
Stage 1: Onboarding — login, project creation, API token.

Mirrors the frontend onboarding flow: authenticate, create a project,
generate an API token for trace ingestion.
"""

import pytest

from helpers.api_client import OvermindAPIClient
from helpers.state_manager import E2E_PROJECT_NAME

pytestmark = [pytest.mark.e2e, pytest.mark.stage_onboarding]


def test_login(overmind_client: OvermindAPIClient):
    data = overmind_client.login()
    assert "access_token" in data
    assert data["access_token"]


def test_get_profile(overmind_client: OvermindAPIClient):
    me = overmind_client.get_me()
    assert me["email"] == "admin"
    assert me["is_active"] is True


def test_create_project(overmind_client: OvermindAPIClient, shared_state: dict):
    if shared_state.get("project_id"):
        pytest.skip("Project already exists")

    data = overmind_client.create_project(E2E_PROJECT_NAME)
    assert data["name"] == E2E_PROJECT_NAME
    assert "project_id" in data
    shared_state["project_id"] = data["project_id"]


def test_create_api_token(overmind_client: OvermindAPIClient, shared_state: dict):
    project_id = shared_state.get("project_id")
    assert project_id, "Project must be created first"

    if shared_state.get("api_token"):
        pytest.skip("API token already exists")

    data = overmind_client.create_token(project_id, name="e2e-token")
    assert "token" in data
    assert data["token"]
    shared_state["api_token"] = data["token"]


def test_verify_token_listed(overmind_client: OvermindAPIClient, shared_state: dict):
    project_id = shared_state.get("project_id")
    assert project_id

    tokens = overmind_client.list_tokens(project_id)
    names = [t.get("name") for t in tokens]
    assert "e2e-token" in names
