from __future__ import annotations

import uuid

import pytest

from overbae.models import Project, ProjectMembership, User
from overbae.services.seat_downgrade import enforce_free_seats_after_downgrade

pytestmark = pytest.mark.django_db


def _user(email: str | None = None) -> User:
    return User.objects.create_user(
        email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )


def _project() -> Project:
    return Project.objects.create(
        name=f"P-{uuid.uuid4().hex[:6]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
    )


def test_strips_invitees_keeps_creator():
    creator = _user("creator@example.com")
    a = _user("a@example.com")
    b = _user("b@example.com")
    project = _project()
    ProjectMembership.objects.create(user=creator, project=project)
    ProjectMembership.objects.create(user=a, project=project)
    ProjectMembership.objects.create(user=b, project=project)

    result = enforce_free_seats_after_downgrade(creator)

    assert result["projects_touched"] == 1
    assert result["memberships_removed"] == 2
    remaining = list(
        ProjectMembership.objects.filter(project=project).values_list("user_id", flat=True)
    )
    assert remaining == [creator.pk]


def test_invitee_downgrade_does_not_strip_others_project():
    owner = _user("owner@example.com")
    invitee = _user("invitee@example.com")
    project = _project()
    ProjectMembership.objects.create(user=owner, project=project)
    ProjectMembership.objects.create(user=invitee, project=project)

    result = enforce_free_seats_after_downgrade(invitee)

    assert result["projects_touched"] == 0
    assert result["memberships_removed"] == 0
    assert ProjectMembership.objects.filter(project=project).count() == 2


def test_idempotent_second_call():
    creator = _user("solo-creator@example.com")
    peer = _user("peer@example.com")
    project = _project()
    ProjectMembership.objects.create(user=creator, project=project)
    ProjectMembership.objects.create(user=peer, project=project)

    first = enforce_free_seats_after_downgrade(creator)
    second = enforce_free_seats_after_downgrade(creator)

    assert first["memberships_removed"] == 1
    assert second["projects_touched"] == 0
    assert second["memberships_removed"] == 0
    assert ProjectMembership.objects.filter(project=project).count() == 1


def test_creator_only_project_unchanged():
    creator = _user("alone@example.com")
    project = _project()
    ProjectMembership.objects.create(user=creator, project=project)

    result = enforce_free_seats_after_downgrade(creator)

    assert result == {"projects_touched": 0, "memberships_removed": 0}
    assert ProjectMembership.objects.filter(project=project, user=creator).exists()
