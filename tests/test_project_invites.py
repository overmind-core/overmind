"""Inviting an email with no account creates a ProjectInvite and a Clerk
invitation; the invite becomes a membership when the invitee first signs in.
"""

from __future__ import annotations

import uuid

import pytest
from django.urls import reverse
from factories import auth_client, make_user

from overbae.models import (
    Project,
    ProjectInvite,
    ProjectMembership,
    User,
)
from overbae.services.project_invites import claim_pending_invites

pytestmark = [pytest.mark.django_db, pytest.mark.covers("authentication")]


@pytest.fixture(autouse=True)
def _clerk_on(clerk):
    return clerk


def _project_with(owner: User) -> Project:
    project = Project.objects.create(name="Inv", slug=f"inv-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=owner, project=project)
    return project


def _invites_url(project: Project) -> str:
    return reverse("project-invite-list", kwargs={"project_id": project.id})


def test_invite_unknown_email_creates_row_and_clerk_invitation(clerk):
    owner = make_user("lead@example.com")
    project = _project_with(owner)

    r = auth_client(owner).post(_invites_url(project), {"email": "New@Person.ai"}, format="json")

    assert r.status_code == 201
    [sent] = clerk.invitations.values()
    assert sent["email_address"] == "new@person.ai"
    invite = ProjectInvite.objects.get(project=project)
    assert invite.email == "new@person.ai"
    assert invite.invited_by == owner
    assert invite.clerk_invitation_id == sent["id"]
    assert r.data["email"] == "new@person.ai"
    assert r.data["invited_by_email"] == owner.email


def test_invite_existing_user_email_is_rejected(clerk):
    owner = make_user("lead@example.com")
    make_user("known@example.com")
    project = _project_with(owner)

    r = auth_client(owner).post(
        _invites_url(project), {"email": "known@example.com"}, format="json"
    )

    assert r.status_code == 400
    assert r.data["code"] == "user_exists"
    assert clerk.invitations == {}
    assert not ProjectInvite.objects.exists()


def test_repeat_invite_is_idempotent(clerk):
    owner = make_user("lead@example.com")
    project = _project_with(owner)
    client = auth_client(owner)

    first = client.post(_invites_url(project), {"email": "new@person.ai"}, format="json")
    second = client.post(_invites_url(project), {"email": "new@person.ai"}, format="json")

    assert first.status_code == 201
    assert second.status_code == 201
    assert len(clerk.invitations) == 1
    assert ProjectInvite.objects.filter(project=project).count() == 1
    assert second.data["id"] == first.data["id"]


def test_invite_list_requires_membership():
    owner = make_user("lead@example.com")
    outsider = make_user("outsider@example.com")
    project = _project_with(owner)
    ProjectInvite.objects.create(project=project, email="new@person.ai", invited_by=owner)

    r = auth_client(owner).get(_invites_url(project))
    assert r.status_code == 200
    assert [row["email"] for row in r.data["results"]] == ["new@person.ai"]

    r = auth_client(outsider).get(_invites_url(project))
    assert r.status_code == 404


def test_revoke_invite_deletes_row_and_revokes_clerk(clerk):
    owner = make_user("lead@example.com")
    project = _project_with(owner)
    sent = clerk.invite("new@person.ai")
    invite = ProjectInvite.objects.create(
        project=project, email="new@person.ai", invited_by=owner, clerk_invitation_id=sent["id"]
    )

    r = auth_client(owner).delete(
        reverse("project-invite-detail", kwargs={"project_id": project.id, "id": invite.id})
    )

    assert r.status_code == 204
    assert sent["status"] == "revoked"
    assert not ProjectInvite.objects.exists()


def test_a_project_takes_any_number_of_members_and_invites(clerk):
    owner = make_user("lead@example.com")
    project = _project_with(owner)
    ProjectInvite.objects.create(project=project, email="pending@person.ai", invited_by=owner)
    client = auth_client(owner)

    added = client.post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": make_user("known@example.com").email},
        format="json",
    )
    invited = client.post(_invites_url(project), {"email": "new@person.ai"}, format="json")

    assert added.status_code == 201
    assert invited.status_code == 201
    assert ProjectMembership.objects.filter(project=project).count() == 2
    assert ProjectInvite.objects.filter(project=project).count() == 2


def test_claim_converts_invites_to_memberships():
    owner = make_user("lead@example.com")
    p1 = _project_with(owner)
    p2 = _project_with(owner)
    ProjectInvite.objects.create(project=p1, email="new@person.ai", invited_by=owner)
    ProjectInvite.objects.create(project=p2, email="new@person.ai", invited_by=owner)
    joiner = make_user("New@Person.ai")

    claim_pending_invites(joiner)

    assert ProjectMembership.objects.filter(user=joiner, project=p1).exists()
    assert ProjectMembership.objects.filter(user=joiner, project=p2).exists()
    assert not ProjectInvite.objects.exists()
