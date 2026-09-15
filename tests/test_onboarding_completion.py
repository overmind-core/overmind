from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Project, ProjectMembership, User, UserOnboarding

pytestmark = pytest.mark.django_db


def _user() -> User:
    return User.objects.create_user(
        email=f"ob-{uuid.uuid4().hex[:8]}@example.com",
        password="x",
        clerk_user_id=f"c_{uuid.uuid4().hex}",
        projects_limit=5,
    )


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def _me(user: User) -> dict:
    return _auth_client(user).get("/api/auth/me/").json()


def _add_membership(user: User) -> None:
    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)


def test_fresh_user_is_incomplete():
    assert _me(_user())["has_completed_onboarding"] is False


def test_membership_without_onboarding_record_counts_as_completed():
    user = _user()
    _add_membership(user)
    assert _me(user)["has_completed_onboarding"] is True


def test_in_progress_overrides_membership_shortcut():
    user = _user()
    UserOnboarding.objects.create(user=user, status="in_progress")
    _add_membership(user)
    assert _me(user)["has_completed_onboarding"] is False


def test_completed_status_wins():
    user = _user()
    UserOnboarding.objects.create(user=user, status="completed")
    assert _me(user)["has_completed_onboarding"] is True


def test_patch_cannot_reassign_onboarding_user():
    user = _user()
    other = _user()
    client = _auth_client(user)

    response = client.patch(
        "/api/auth/onboarding/",
        {"status": "in_progress", "user": str(other.pk)},
        format="json",
    )

    assert response.status_code == 200
    onboarding = UserOnboarding.objects.get(user=user)
    assert onboarding.status == "in_progress"
    assert not UserOnboarding.objects.filter(user=other).exists()
