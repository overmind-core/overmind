"""Enforce Free seat limits after a Pro → Free downgrade.

Call only once the subscription actually becomes Free (Stripe
``customer.subscription.deleted`` → status ``canceled``), never on
``cancel_at_period_end`` — those users stay Pro until the period ends.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction

if TYPE_CHECKING:
    from overbae.models import User

__all__ = ["enforce_free_seats_after_downgrade"]


@transaction.atomic
def enforce_free_seats_after_downgrade(user: User) -> dict:
    """Strip multi-member projects created by ``user`` down to the creator.

    Creator = earliest ``ProjectMembership`` (``created_at``, then ``id``);
    projects where ``user`` is only an invitee are left alone. Idempotent.
    """
    from overbae.models import ProjectMembership

    project_ids = list(
        ProjectMembership.objects.filter(user=user).values_list("project_id", flat=True)
    )
    projects_touched = 0
    memberships_removed = 0

    for project_id in project_ids:
        members = list(
            ProjectMembership.objects.filter(project_id=project_id).order_by("created_at", "id")
        )
        if len(members) <= 1:
            continue
        creator = members[0]
        if creator.user_id != user.pk:
            continue
        deleted, _ = (
            ProjectMembership.objects.filter(project_id=project_id).exclude(pk=creator.pk).delete()
        )
        # delete() returns (total_objs, {model_label: count}); memberships are one model.
        memberships_removed += deleted
        projects_touched += 1

    return {
        "projects_touched": projects_touched,
        "memberships_removed": memberships_removed,
    }
