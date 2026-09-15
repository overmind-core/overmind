"""Inviting an email with no account creates a ProjectInvite and a Clerk
invitation; the invite becomes a membership when the invitee first signs in.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
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
from overbae.services.project_invites import claim_pending_invites

pytestmark = pytest.mark.django_db

CREATE_CLERK = "overbae.services.project_invites.create_clerk_invitation"
REVOKE_CLERK = "overbae.services.project_invites.revoke_clerk_invitation"


def _user(email: str) -> User:
    return User.objects.create_user(
        email=email,
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        projects_limit=5,
    )


def _pro(user: User) -> User:
    Subscription.objects.update_or_create(
        user=user,
        defaults={
            "status": SubscriptionStatus.ACTIVE,
            "stripe_subscription_id": f"sub_{uuid.uuid4().hex[:8]}",
        },
    )
    user.projects_limit = None
    user.save(update_fields=["projects_limit"])
    return user


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def _project_with(owner: User) -> Project:
    project = Project.objects.create(name="Inv", slug=f"inv-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=owner, project=project)
    return project


def _invites_url(project: Project) -> str:
    return reverse("project-invite-list", kwargs={"project_id": project.id})


def test_invite_unknown_email_creates_row_and_clerk_invitation():
    owner = _pro(_user("lead@example.com"))
    project = _project_with(owner)

    with patch(CREATE_CLERK, return_value="inv_clerk_1") as mock_create:
        r = _auth_client(owner).post(
            _invites_url(project), {"email": "New@Person.ai"}, format="json"
        )

    assert r.status_code == 201
    mock_create.assert_called_once_with("new@person.ai")
    invite = ProjectInvite.objects.get(project=project)
    assert invite.email == "new@person.ai"
    assert invite.invited_by == owner
    assert invite.clerk_invitation_id == "inv_clerk_1"
    assert r.data["email"] == "new@person.ai"
    assert r.data["invited_by_email"] == owner.email


def test_invite_existing_user_email_is_rejected():
    owner = _pro(_user("lead@example.com"))
    _user("known@example.com")
    project = _project_with(owner)

    with patch(CREATE_CLERK) as mock_create:
        r = _auth_client(owner).post(
            _invites_url(project), {"email": "known@example.com"}, format="json"
        )

    assert r.status_code == 400
    assert r.data["code"] == "user_exists"
    mock_create.assert_not_called()
    assert not ProjectInvite.objects.exists()


def test_repeat_invite_is_idempotent():
    owner = _pro(_user("lead@example.com"))
    project = _project_with(owner)
    client = _auth_client(owner)

    with patch(CREATE_CLERK, return_value="inv_clerk_1") as mock_create:
        first = client.post(_invites_url(project), {"email": "new@person.ai"}, format="json")
        second = client.post(_invites_url(project), {"email": "new@person.ai"}, format="json")

    assert first.status_code == 201
    assert second.status_code == 201
    assert mock_create.call_count == 1
    assert ProjectInvite.objects.filter(project=project).count() == 1
    assert second.data["id"] == first.data["id"]


def test_invite_list_requires_membership():
    owner = _pro(_user("lead@example.com"))
    outsider = _user("outsider@example.com")
    project = _project_with(owner)
    ProjectInvite.objects.create(project=project, email="new@person.ai", invited_by=owner)

    r = _auth_client(owner).get(_invites_url(project))
    assert r.status_code == 200
    assert [row["email"] for row in r.data["results"]] == ["new@person.ai"]

    r = _auth_client(outsider).get(_invites_url(project))
    assert r.status_code == 404


def test_revoke_invite_deletes_row_and_revokes_clerk():
    owner = _pro(_user("lead@example.com"))
    project = _project_with(owner)
    invite = ProjectInvite.objects.create(
        project=project, email="new@person.ai", invited_by=owner, clerk_invitation_id="inv_clerk_1"
    )

    with patch(REVOKE_CLERK) as mock_revoke:
        r = _auth_client(owner).delete(
            reverse("project-invite-detail", kwargs={"project_id": project.id, "id": invite.id})
        )

    assert r.status_code == 204
    mock_revoke.assert_called_once_with("inv_clerk_1")
    assert not ProjectInvite.objects.exists()


def test_free_actor_cannot_invite_past_seat_limit():
    owner = _user("free@example.com")
    project = _project_with(owner)

    with patch(CREATE_CLERK) as mock_create:
        r = _auth_client(owner).post(
            _invites_url(project), {"email": "new@person.ai"}, format="json"
        )

    assert r.status_code == 403
    mock_create.assert_not_called()
    assert not ProjectInvite.objects.exists()


def test_pending_invite_fills_a_free_seat():
    owner = _user("free@example.com")
    project = _project_with(owner)
    ProjectInvite.objects.create(project=project, email="pending@person.ai", invited_by=owner)
    known = _user("known@example.com")

    r = _auth_client(owner).post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": known.email},
        format="json",
    )
    assert r.status_code == 403


def test_claim_converts_invites_to_memberships():
    owner = _pro(_user("lead@example.com"))
    p1 = _project_with(owner)
    p2 = _project_with(owner)
    ProjectInvite.objects.create(project=p1, email="new@person.ai", invited_by=owner)
    ProjectInvite.objects.create(project=p2, email="new@person.ai", invited_by=owner)
    joiner = _user("New@Person.ai")

    claim_pending_invites(joiner)

    assert ProjectMembership.objects.filter(user=joiner, project=p1).exists()
    assert ProjectMembership.objects.filter(user=joiner, project=p2).exists()
    assert not ProjectInvite.objects.exists()


def test_claim_never_raises():
    joiner = _user("lonely@person.ai")
    with patch(
        "overbae.services.project_invites.ProjectInvite.objects.filter",
        side_effect=RuntimeError("db down"),
    ):
        claim_pending_invites(joiner)
