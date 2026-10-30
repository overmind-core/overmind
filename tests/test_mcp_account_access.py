from __future__ import annotations

import json
import uuid

import pytest
from starlette.testclient import TestClient

from overbae.models import APIToken, Project, ProjectMembership, User
from overbae.services.mcp.server import create_mcp_application

pytestmark = pytest.mark.django_db(transaction=True)


def account():
    user = User.objects.create_user(
        email=f"account-{uuid.uuid4().hex}@test.com", password="pw", clerk_user_id="test"
    )
    projects = [
        Project.objects.create(name=name, slug=uuid.uuid4().hex)
        for name in ["Research", "Production", "Private"]
    ]
    for project in projects[:2]:
        ProjectMembership.objects.create(user=user, project=project)
    raw, _ = APIToken.create_for_user(user)
    return user, projects, raw


def rpc(client, raw, method, params=None):
    return client.post(
        "/api/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"X-Api-Key": raw, "Accept": "application/json"},
    ).json()


def test_account_discovers_projects_and_reads_each_without_shared_selection():
    _, projects, raw = account()
    with TestClient(create_mcp_application()) as client:
        listed = rpc(client, raw, "tools/call", {"name": "list_projects"})["result"]
        assert {row["id"] for row in listed["structuredContent"]["projects"]} == {
            str(project.pk) for project in projects[:2]
        }
        for project in [projects[1], projects[0], projects[1]]:
            resource = rpc(
                client,
                raw,
                "resources/read",
                {"uri": f"overmind://project/current?project_id={project.pk}"},
            )["result"]
            data = json.loads(resource["contents"][0]["text"])
            assert data["id"] == str(project.pk)
        missing = rpc(client, raw, "tools/call", {"name": "list_datasets"})["result"]
        assert missing["isError"]
        assert missing["structuredContent"]["error"]["code"] == "project_required"


def test_explicit_project_is_enforced_for_tools_resources_and_membership_revocation():
    user, projects, raw = account()
    with TestClient(create_mcp_application()) as client:
        for project in projects[:2]:
            result = rpc(
                client,
                raw,
                "tools/call",
                {
                    "name": "list_datasets",
                    "arguments": {"project_id": str(project.pk)},
                },
            )["result"]
            assert not result.get("isError")
        ProjectMembership.objects.filter(user=user, project=projects[0]).delete()
        for project in [projects[0], projects[2]]:
            result = rpc(
                client,
                raw,
                "tools/call",
                {
                    "name": "list_datasets",
                    "arguments": {"project_id": str(project.pk)},
                },
            )["result"]
            assert result["isError"]
            assert result["structuredContent"]["error"]["code"] == "project_required"
            resource = rpc(
                client,
                raw,
                "resources/read",
                {
                    "uri": f"overmind://project/current?project_id={project.pk}",
                },
            )
            assert "error" in resource


def test_project_key_cannot_expand_access_to_another_membership():
    user, projects, _ = account()
    raw, _ = APIToken.create_for_user(user, project=projects[0])
    with TestClient(create_mcp_application()) as client:
        listed = rpc(client, raw, "tools/call", {"name": "list_projects"})["result"]
        assert [row["id"] for row in listed["structuredContent"]["projects"]] == [
            str(projects[0].pk)
        ]
        result = rpc(
            client,
            raw,
            "tools/call",
            {
                "name": "list_datasets",
                "arguments": {"project_id": str(projects[1].pk)},
            },
        )["result"]
        assert result["isError"]
        result = rpc(client, raw, "tools/call", {"name": "list_datasets"})["result"]
        assert not result.get("isError")


def test_account_read_key_cannot_call_write_tools():
    user, projects, _ = account()
    raw, _ = APIToken.create_for_user(user, permission=["read"])
    with TestClient(create_mcp_application()) as client:
        result = rpc(
            client,
            raw,
            "tools/call",
            {
                "name": "run_evaluation",
                "arguments": {"project_id": str(projects[0].pk)},
            },
        )["result"]
        assert result["isError"]
        assert result["structuredContent"]["error"]["code"] == "permission_denied"
