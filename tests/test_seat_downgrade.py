from __future__ import annotations

import pytest
from factories import make_project, make_user

from overbae.models import ProjectMembership
from overbae.services.seat_downgrade import enforce_free_seats_after_downgrade

pytestmark = pytest.mark.django_db


def test_strips_invitees_keeps_creator():
    creator = make_user("creator@example.com")
    a = make_user("a@example.com")
    b = make_user("b@example.com")
    project = make_project()
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
    owner = make_user("owner@example.com")
    invitee = make_user("invitee@example.com")
    project = make_project()
    ProjectMembership.objects.create(user=owner, project=project)
    ProjectMembership.objects.create(user=invitee, project=project)

    result = enforce_free_seats_after_downgrade(invitee)

    assert result["projects_touched"] == 0
    assert result["memberships_removed"] == 0
    assert ProjectMembership.objects.filter(project=project).count() == 2


def test_idempotent_second_call():
    creator = make_user("solo-creator@example.com")
    peer = make_user("peer@example.com")
    project = make_project()
    ProjectMembership.objects.create(user=creator, project=project)
    ProjectMembership.objects.create(user=peer, project=project)

    first = enforce_free_seats_after_downgrade(creator)
    second = enforce_free_seats_after_downgrade(creator)

    assert first["memberships_removed"] == 1
    assert second["projects_touched"] == 0
    assert second["memberships_removed"] == 0
    assert ProjectMembership.objects.filter(project=project).count() == 1


def test_creator_only_project_unchanged():
    creator = make_user("alone@example.com")
    project = make_project()
    ProjectMembership.objects.create(user=creator, project=project)

    result = enforce_free_seats_after_downgrade(creator)

    assert result == {"projects_touched": 0, "memberships_removed": 0}
    assert ProjectMembership.objects.filter(project=project, user=creator).exists()
