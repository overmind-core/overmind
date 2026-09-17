"""Project invitations for emails without a console account.

When Clerk is configured the invite is emailed through Clerk; without Clerk the
row is stored and claimed on the invitee's first local sign-in.
"""

import logging

from clerk_backend_api import Clerk
from django.conf import settings

from overbae.auth import clerk_enabled
from overbae.models import ProjectInvite, ProjectMembership

logger = logging.getLogger(__name__)


def create_clerk_invitation(email: str) -> str:
    """Send a Clerk invitation email and return the Clerk invitation id.

    ``ignore_existing`` lets several projects invite the same email: each call
    gets its own invitation id, so revokes stay per-project. Returns an empty
    string when Clerk is off — the ProjectInvite row is still the source of truth.
    """
    if not clerk_enabled():
        return ""
    with Clerk(bearer_auth=settings.CLERK_API_SECRET_KEY) as clerk:
        invitation = clerk.invitations.create(
            request={
                "email_address": email,
                "redirect_url": f"{settings.FRONTEND_URL}/login",
                "notify": True,
                "ignore_existing": True,
            }
        )
    return invitation.id


def revoke_clerk_invitation(invitation_id: str) -> None:
    """Best effort: if the Clerk revoke fails the invitee can still sign up,
    but with the ProjectInvite row gone they claim no project access.
    """
    if not invitation_id or not clerk_enabled():
        return
    try:
        with Clerk(bearer_auth=settings.CLERK_API_SECRET_KEY) as clerk:
            clerk.invitations.revoke(invitation_id=invitation_id)
    except Exception:
        logger.exception("Clerk invitation revoke failed for %s", invitation_id)


def claim_pending_invites(user) -> None:
    """Convert pending invites for ``user.email`` into memberships.

    An email match is proof of ownership for both Clerk and local sign-in.
    Runs on a user's first authenticated request / local session create and
    must never raise.
    """
    try:
        invites = list(ProjectInvite.objects.filter(email__iexact=user.email))
        for invite in invites:
            ProjectMembership.objects.get_or_create(project_id=invite.project_id, user=user)
        ProjectInvite.objects.filter(id__in=[invite.id for invite in invites]).delete()
    except Exception:
        logger.exception("Claiming project invites failed for user %s", user.pk)
