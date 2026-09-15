from __future__ import annotations

import uuid
from datetime import timedelta
from unittest import mock

import pytest
from conftest import EVAL_ROWS, frozen_dataset
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.api.guest import start_guest_session
from overbae.models import Project, ProjectMembership, User
from overbae.tasks.guest_cleanup import sweep_guest_workspaces

pytestmark = pytest.mark.django_db


def _auth(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def _guest_client() -> tuple[User, Project, APIClient, str]:
    user, project, access, refresh = start_guest_session()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return user, project, client, refresh


def _member(**fields) -> User:
    suffix = uuid.uuid4().hex[:8]
    return User.objects.create_user(
        email=f"u-{suffix}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{suffix}",
        **fields,
    )


@pytest.fixture(autouse=True)
def _clear_throttle():
    """The guest-start throttle counts per process; a worker that runs several
    of these tests in one hour would trip it."""
    cache.clear()


def test_guest_start_creates_user_and_project():
    res = APIClient().post("/api/auth/guest/")
    assert res.status_code == 201
    body = res.json()
    assert body["access"] and body["refresh"] and body["project_id"]
    assert body["user"]["is_guest"] is True
    assert body["user"]["has_completed_onboarding"] is True
    user = User.objects.get(pk=body["user"]["id"])
    assert user.is_guest
    assert user.project_memberships.filter(project_id=body["project_id"]).exists()


def test_guest_can_read_and_cannot_mutate():
    _user, project, client, _refresh = _guest_client()
    assert client.get("/api/agent/", {"project": str(project.id)}).status_code == 200
    denied = client.post("/api/projects/", {"name": "Other", "slug": f"x-{uuid.uuid4().hex[:8]}"})
    assert denied.status_code == 403
    assert denied.json()["code"] == "guest_upgrade_required"


def test_guest_gate_covers_views_with_their_own_permission_classes():
    _user, project, client, _refresh = _guest_client()
    denied = client.post(
        "/api/auth/api-keys/", {"name": "probe", "project": str(project.id)}, format="json"
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "guest_upgrade_required"


def test_guest_can_refresh_tokens():
    _user, _project, client, refresh = _guest_client()
    res = client.post("/api/auth/token/refresh/", {"refresh": refresh}, format="json")
    assert res.status_code == 200
    assert res.json()["access"]


def test_regular_user_is_not_restricted():
    res = _auth(_member()).get("/api/auth/me/")
    assert res.status_code == 200
    assert res.json()["is_guest"] is False


def test_claim_moves_workspace_to_clerk_account_and_ignores_project_limit():
    guest, project, client, _refresh = _guest_client()
    owner = _member(projects_limit=0)
    with mock.patch("overbae.api.guest.ClerkAuthentication") as clerk:
        clerk.return_value.authenticate.return_value = (owner, {})
        res = client.post("/api/auth/guest/claim/", {"clerk_token": "clerk-jwt"}, format="json")
    assert res.status_code == 200
    body = res.json()
    assert body["project_id"] == str(project.id)
    assert body["user"]["id"] == owner.pk
    assert body["user"]["is_guest"] is False
    assert ProjectMembership.objects.filter(user=owner, project=project).exists()
    assert not ProjectMembership.objects.filter(user=guest).exists()
    guest.refresh_from_db()
    assert guest.is_active is False
    assert guest.is_guest is True


def test_claim_rejects_invalid_clerk_token():
    _guest, _project, client, _refresh = _guest_client()
    with mock.patch("overbae.api.guest.ClerkAuthentication") as clerk:
        clerk.return_value.authenticate.return_value = None
        res = client.post("/api/auth/guest/claim/", {"clerk_token": "bad"}, format="json")
    assert res.status_code == 401


def test_claim_endpoint_requires_guest():
    res = _auth(_member()).post("/api/auth/guest/claim/", {"clerk_token": "x"}, format="json")
    assert res.status_code == 403


def test_sweep_removes_expired_and_claimed_guests_but_keeps_claimed_projects():
    expired, expired_project, _access, _refresh = start_guest_session()
    User.objects.filter(pk=expired.pk).update(date_joined=timezone.now() - timedelta(days=8))
    fresh, fresh_project, _access, _refresh = start_guest_session()
    claimed, claimed_project, _access, _refresh = start_guest_session()
    owner = _member()
    ProjectMembership.objects.filter(user=claimed).update(user=owner)
    User.objects.filter(pk=claimed.pk).update(is_active=False)

    assert sweep_guest_workspaces() == {"projects": 1, "users": 2}
    assert not User.objects.filter(pk__in=[expired.pk, claimed.pk]).exists()
    assert not Project.objects.filter(pk=expired_project.pk).exists()
    assert User.objects.filter(pk=fresh.pk).exists()
    assert Project.objects.filter(pk=fresh_project.pk).exists()
    assert Project.objects.filter(pk=claimed_project.pk).exists()


def test_refresh_for_a_swept_guest_is_unauthorized():
    guest, _project, client, refresh = _guest_client()
    guest.delete()
    res = client.post("/api/auth/token/refresh/", {"refresh": refresh}, format="json")
    assert res.status_code == 401


@pytest.mark.parametrize("header", ["Bearer  {t}", "Bearer\t{t}", " Bearer {t}", "Bearer {t} "])
def test_guest_gate_survives_authorization_header_whitespace(header):
    _user, _project, _client, _refresh = _guest_client()
    access = str(RefreshToken.for_user(_user).access_token)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=header.format(t=access))
    res = client.post("/api/projects/", {"name": "x", "slug": f"x-{uuid.uuid4().hex[:8]}"})
    assert res.status_code in (401, 403)
    if res.status_code == 403:
        assert res.json()["code"] == "guest_upgrade_required"


def test_guest_cannot_ingest_traces_on_either_route():
    _user, _project, client, _refresh = _guest_client()
    for path in ("/api/v1/traces", "/v1/traces"):
        res = client.post(path, b"\x00", content_type="application/x-protobuf")
        assert res.status_code == 403, path
        assert res.json()["code"] == "guest_upgrade_required"


def test_guest_starts_with_no_credits():
    from overbae.services.billing_ledger import balance_usd

    user, _project, _access, _refresh = start_guest_session()
    assert balance_usd(user) == 0


def test_guest_start_ignores_the_callers_account_and_throttles_by_address():
    from django.core.cache import cache

    cache.clear()
    client = _auth(_member())
    codes = [client.post("/api/auth/guest/").status_code for _ in range(6)]
    assert codes == [201] * 5 + [429]


def test_sweep_keeps_going_past_a_protected_project():
    from overbae.models import FinetuningJob

    blocked, blocked_project, _a, _r = start_guest_session()
    plain, plain_project, _a, _r = start_guest_session()
    User.objects.filter(pk__in=[blocked.pk, plain.pk]).update(
        date_joined=timezone.now() - timedelta(days=8)
    )
    dataset = frozen_dataset(blocked_project, EVAL_ROWS, name="d")
    FinetuningJob.objects.create(project=blocked_project, dataset=dataset)

    result = sweep_guest_workspaces()
    assert result["projects"] == 1
    assert not Project.objects.filter(pk=plain_project.pk).exists()
    assert Project.objects.filter(pk=blocked_project.pk).exists()
