from __future__ import annotations

import uuid

import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Project, ProjectMembership, Subscription, SubscriptionStatus, User
from overbae.services.plan_limits import (
    FREE_LIMITS,
    SeatLimitExceeded,
    is_pro,
    require_seat_for_invite,
)

pytestmark = pytest.mark.django_db


def _user(email: str | None = None, **extra) -> User:
    return User.objects.create_user(
        email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        **extra,
    )


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def _project(user: User) -> Project:
    project = Project.objects.create(
        name=f"P-{uuid.uuid4().hex[:6]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
    )
    ProjectMembership.objects.create(user=user, project=project)
    return project


def _make_pro(user: User) -> None:
    Subscription.objects.update_or_create(
        user=user,
        defaults={
            "status": SubscriptionStatus.ACTIVE,
            "stripe_subscription_id": f"sub_{uuid.uuid4().hex[:10]}",
        },
    )


def test_past_due_is_not_pro():
    u = _user()
    Subscription.objects.create(
        user=u,
        status=SubscriptionStatus.PAST_DUE,
        stripe_subscription_id="sub_past_due",
    )
    assert not is_pro(u)


def test_active_subscription_is_pro():
    u = _user()
    _make_pro(u)
    assert is_pro(u)


def test_free_cannot_invite_second_member():
    owner = _user("owner-seat@example.com")
    project = _project(owner)
    invitee = _user("invitee-seat@example.com")

    with pytest.raises(SeatLimitExceeded):
        require_seat_for_invite(owner, project)

    r = _auth_client(owner).post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": invitee.email},
        format="json",
    )
    assert r.status_code == 403
    assert r.data.get("code") == "seat_limit_exceeded"
    assert not ProjectMembership.objects.filter(project=project, user=invitee).exists()


def test_pro_can_invite_members():
    owner = _user("pro-owner@example.com")
    _make_pro(owner)
    project = _project(owner)
    invitee = _user("pro-invitee@example.com")

    require_seat_for_invite(owner, project)
    r = _auth_client(owner).post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": invitee.email},
        format="json",
    )
    assert r.status_code == 201
    assert ProjectMembership.objects.filter(project=project, user=invitee).exists()


def test_subscription_payload_reports_only_the_seat_limit():
    user = _user()
    r = _auth_client(user).get(reverse("billing-subscription"))
    assert r.status_code == 200
    assert r.data["usage"] == {"seats": {"limit": FREE_LIMITS["seats"]}}
