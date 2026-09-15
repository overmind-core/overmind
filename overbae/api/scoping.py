from __future__ import annotations

from overbae.models import APIToken, ProjectMembership


def project_ids_for(user, auth=None):
    """The project ids this request may read.

    A project-scoped API key is pinned to ``resourceIds`` and must not reach
    the caller's other memberships. An account-scoped key follows memberships
    the same way a session does. ``auth`` is a plain dict under Clerk, hence
    the isinstance guard.
    """
    if isinstance(auth, APIToken):
        return auth.allowed_project_ids()
    return ProjectMembership.objects.filter(user=user).values_list("project_id", flat=True)
