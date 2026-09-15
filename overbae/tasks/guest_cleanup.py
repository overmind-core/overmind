import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import ProtectedError, Q
from django.utils import timezone

from overbae.models import Project, User

logger = logging.getLogger(__name__)

GUEST_TTL = timedelta(days=7)


@shared_task(name="overbae.tasks.guest_cleanup.sweep_guest_workspaces")
def sweep_guest_workspaces() -> dict[str, int]:
    """Delete unclaimed guests older than ``GUEST_TTL`` and every claimed (inactive) one.

    A claimed workspace keeps its project: the owner's membership excludes it.
    """
    cutoff = timezone.now() - GUEST_TTL
    stale = User.objects.filter(is_guest=True).filter(
        Q(is_active=False) | Q(date_joined__lt=cutoff)
    )
    projects = (
        Project.objects.filter(memberships__user__in=stale)
        .exclude(memberships__user__is_guest=False)
        .distinct()
    )
    deleted_projects = 0
    for project in projects:
        try:
            project.delete()
            deleted_projects += 1
        except ProtectedError:
            logger.warning("guest project %s kept: protected rows remain", project.id)
    _, deleted_by_model = stale.delete()
    deleted_users = deleted_by_model.get("overbae.User", 0)
    logger.info(
        "guest_swept",
        extra={"event": "guest_swept", "projects": deleted_projects, "users": deleted_users},
    )
    return {"projects": deleted_projects, "users": deleted_users}
