"""Self-hosted local auth: login-or-register without Clerk."""

from __future__ import annotations

import uuid

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import (
    Project,
    ProjectInvite,
    ProjectMembership,
    Subscription,
    SubscriptionStatus,
    User,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_throttle():
    cache.clear()


@pytest.fixture(autouse=True)
def _clerk_off(settings):
    settings.CLERK_API_SECRET_KEY = ""


def _auth(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def _owner() -> tuple[User, Project]:
    user = User.objects.create_user(
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"local_{uuid.uuid4().hex}",
        projects_limit=None,
    )
    Subscription.objects.update_or_create(
        user=user,
        defaults={
            "status": SubscriptionStatus.ACTIVE,
            "stripe_subscription_id": f"sub_{uuid.uuid4().hex[:8]}",
        },
    )
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    return user, project


def test_local_session_creates_account_on_first_use():
    res = APIClient().post(
        "/api/auth/local/",
        {"email": "New@Ops.local", "password": "password123"},
        format="json",
    )
    assert res.status_code == 201
    body = res.json()
    assert body["access"] and body["refresh"]
    assert body["user"]["email"] == "new@ops.local"
    assert body["user"]["email_verified"] is True
    assert body["user"]["is_guest"] is False
    user = User.objects.get(email="new@ops.local")
    assert user.clerk_user_id.startswith("local_")
    assert user.check_password("password123")


def test_local_session_signs_in_existing_user():
    User.objects.create_user(
        email="ops@example.com",
        password="password123",
        clerk_user_id=f"local_{uuid.uuid4().hex}",
    )
    res = APIClient().post(
        "/api/auth/local/",
        {"email": "ops@example.com", "password": "password123"},
        format="json",
    )
    assert res.status_code == 200
    assert res.json()["user"]["email"] == "ops@example.com"
    assert User.objects.filter(email="ops@example.com").count() == 1


def test_local_session_wrong_password():
    User.objects.create_user(
        email="ops@example.com",
        password="password123",
        clerk_user_id=f"local_{uuid.uuid4().hex}",
    )
    res = APIClient().post(
        "/api/auth/local/",
        {"email": "ops@example.com", "password": "wrong-password"},
        format="json",
    )
    assert res.status_code == 401, res.content
    assert res.json().get("detail")


def test_local_session_refused_when_clerk_enabled(settings):
    settings.CLERK_API_SECRET_KEY = "sk_test_xxx"
    res = APIClient().post(
        "/api/auth/local/",
        {"email": "ops@example.com", "password": "password123"},
        format="json",
    )
    assert res.status_code == 403
    assert res.json()["code"] == "clerk_required"
    assert not User.objects.filter(email="ops@example.com").exists()


def test_local_session_claims_pending_invite():
    owner, project = _owner()
    ProjectInvite.objects.create(
        project=project,
        email="invitee@example.com",
        invited_by=owner,
        clerk_invitation_id="",
    )
    res = APIClient().post(
        "/api/auth/local/",
        {"email": "invitee@example.com", "password": "password123"},
        format="json",
    )
    assert res.status_code == 201
    user = User.objects.get(email="invitee@example.com")
    assert ProjectMembership.objects.filter(user=user, project=project).exists()
    assert not ProjectInvite.objects.filter(email="invitee@example.com").exists()


def test_invite_create_without_clerk_skips_email():
    owner, project = _owner()
    res = _auth(owner).post(
        f"/api/projects/{project.id}/invites/",
        {"email": "peer@example.com"},
        format="json",
    )
    assert res.status_code == 201
    invite = ProjectInvite.objects.get(project=project, email="peer@example.com")
    assert invite.clerk_invitation_id == ""
