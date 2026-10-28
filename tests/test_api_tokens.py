from __future__ import annotations

import pytest
from factories import make_project, make_user
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.api.scoping import project_ids_for
from overbae.models import APIToken, Project, ProjectMembership, User

pytestmark = pytest.mark.django_db


def _jwt(user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def _api_key_client(user: User, *, project: Project | None = None) -> tuple[APIClient, str]:
    raw, _ = APIToken.create_for_user(user, project=project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw)
    return client, raw


def test_create_stores_description_and_dialog_expiry():
    user, project = make_user(), make_project()
    ProjectMembership.objects.create(user=user, project=project)

    r = _jwt(user).post(
        "/api/auth/api-keys/",
        {
            "name": "CLI",
            "description": "api key for overmind",
            "expires_in_days": 90,
            "project": str(project.id),
        },
        format="json",
    )
    assert r.status_code == status.HTTP_201_CREATED
    token = APIToken.objects.get(id=r.data["id"])
    assert token.description == "api key for overmind"
    assert token.expires_at is not None
    assert r.data["expires_at"] is not None


def test_create_with_project_mints_project_scope():
    user, project = make_user(), make_project()
    ProjectMembership.objects.create(user=user, project=project)

    r = _jwt(user).post("/api/auth/api-keys/", {"name": "cli", "project": str(project.id)})
    assert r.status_code == status.HTTP_201_CREATED
    assert r.data["scope"] == {
        "scope": "project",
        "resourceIds": [str(project.id)],
        "permission": ["read", "write"],
    }
    token = APIToken.objects.get(id=r.data["id"])
    assert token.project_id == project.id
    assert [str(pid) for pid in project_ids_for(user, token)] == [str(project.id)]


def test_create_account_scope_covers_all_memberships():
    user, a, b = make_user(), make_project(), make_project()
    ProjectMembership.objects.create(user=user, project=a)
    ProjectMembership.objects.create(user=user, project=b)

    r = _jwt(user).post(
        "/api/auth/api-keys/",
        {"name": "root", "scope": {"scope": "account", "permission": ["read", "write"]}},
        format="json",
    )
    assert r.status_code == status.HTTP_201_CREATED
    assert r.data["scope"]["scope"] == "account"
    assert r.data["project"] is None
    token = APIToken.objects.get(id=r.data["id"])
    assert {str(pid) for pid in token.allowed_project_ids()} == {str(a.id), str(b.id)}


def test_account_api_key_can_mint_project_scoped_key():
    user, project = make_user(), make_project()
    ProjectMembership.objects.create(user=user, project=project)
    client, raw = _api_key_client(user)

    r = client.post(
        "/api/auth/api-keys/", {"name": "mcp", "project": str(project.id)}, format="json"
    )
    assert r.status_code == status.HTTP_201_CREATED
    assert r.data["scope"]["scope"] == "project"
    assert r.data["scope"]["resourceIds"] == [str(project.id)]
    assert r.data["key"].startswith("ovr_")


def test_project_api_key_mints_only_for_its_own_project():
    user, own, sibling = make_user(), make_project(), make_project()
    ProjectMembership.objects.create(user=user, project=own)
    ProjectMembership.objects.create(user=user, project=sibling)
    client, _ = _api_key_client(user, project=own)

    account = {"name": "x", "scope": {"scope": "account", "permission": ["read", "write"]}}
    pinned = {
        "name": "x",
        "scope": {"scope": "project", "resourceIds": [str(sibling.id)], "permission": ["read"]},
    }
    for body in (account, {"name": "x", "project": str(sibling.id)}, pinned):
        r = client.post("/api/auth/api-keys/", body, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN, body

    r = client.post("/api/auth/api-keys/", {"name": "x", "project": str(own.id)}, format="json")
    assert r.status_code == status.HTTP_201_CREATED
    assert r.data["scope"]["resourceIds"] == [str(own.id)]
    assert APIToken.objects.filter(user=user).count() == 2


def test_read_only_api_key_cannot_write():
    user, project = make_user(), make_project()
    ProjectMembership.objects.create(user=user, project=project)
    raw, _ = APIToken.create_for_user(user, project=project, permission=["read"])
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw)

    assert client.get(f"/api/projects/{project.id}/").status_code == status.HTTP_200_OK
    r = client.patch(f"/api/projects/{project.id}/", {"name": "renamed"}, format="json")
    assert r.status_code == status.HTTP_403_FORBIDDEN
    project.refresh_from_db()
    assert project.name != "renamed"


def test_api_key_mints_only_permissions_it_holds():
    user, project = make_user(), make_project()
    ProjectMembership.objects.create(user=user, project=project)
    raw, _ = APIToken.create_for_user(user, project=project, permission=["write"])
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw)

    wider = client.post("/api/auth/api-keys/", {"project": str(project.id)}, format="json")
    assert wider.status_code == status.HTTP_403_FORBIDDEN

    same = {
        "scope": {"scope": "project", "resourceIds": [str(project.id)], "permission": ["write"]}
    }
    r = client.post("/api/auth/api-keys/", same, format="json")
    assert r.status_code == status.HTTP_201_CREATED
    assert r.data["scope"]["permission"] == ["write"]


def test_api_key_current_returns_scope():
    user, project = make_user(), make_project()
    ProjectMembership.objects.create(user=user, project=project)
    account_client, _ = _api_key_client(user)
    project_client, _ = _api_key_client(user, project=project)

    account = account_client.get("/api/auth/api-keys/current/")
    assert account.status_code == status.HTTP_200_OK
    assert account.data["scope"] == "account"

    pinned = project_client.get("/api/auth/api-keys/current/")
    assert pinned.status_code == status.HTTP_200_OK
    assert pinned.data["scope"] == "project"
    assert pinned.data["resourceIds"] == [str(project.id)]


def test_create_requires_project_or_scope():
    r = _jwt(make_user()).post("/api/auth/api-keys/", {"name": "x"})
    assert r.status_code == status.HTTP_400_BAD_REQUEST


def test_invited_member_can_only_pin_their_project_via_project_field():
    owner, invitee = make_user(), make_user()
    owned, shared = make_project(), make_project()
    ProjectMembership.objects.create(user=owner, project=owned)
    ProjectMembership.objects.create(user=owner, project=shared)
    ProjectMembership.objects.create(user=invitee, project=shared)

    r = _jwt(invitee).post("/api/auth/api-keys/", {"project": str(shared.id)}, format="json")
    assert r.status_code == status.HTTP_201_CREATED
    assert r.data["scope"]["scope"] == "project"
    assert r.data["scope"]["resourceIds"] == [str(shared.id)]

    denied = _jwt(invitee).post("/api/auth/api-keys/", {"project": str(owned.id)}, format="json")
    assert denied.status_code == status.HTTP_403_FORBIDDEN


def test_project_key_does_not_see_sibling_membership():
    user, a, b = make_user(), make_project(), make_project()
    ProjectMembership.objects.create(user=user, project=a)
    ProjectMembership.objects.create(user=user, project=b)
    _, token = APIToken.create_for_user(user, project=a)
    assert [str(pid) for pid in project_ids_for(user, token)] == [str(a.id)]
