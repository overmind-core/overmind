"""Free/Pro seat entitlement.

Free projects hold one seat; Pro (``Subscription.status == ACTIVE``) is unlimited.
``past_due`` / canceled / missing sub → Free. Check-then-create can slightly
overshoot under concurrency — no advisory lock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework.exceptions import APIException

if TYPE_CHECKING:
    from overbae.models import Project, User

FREE_LIMITS: dict[str, int] = {"seats": 1}

__all__ = [
    "FREE_LIMITS",
    "SeatLimitExceeded",
    "enforce_seat_for_invite",
    "is_pro",
    "plan_usage_payload",
    "require_seat_for_invite",
    "subscription_is_pro",
    "usage_payload",
]


class SeatLimitExceeded(APIException):
    status_code = 403
    default_detail = "Free plan is limited to one seat. Upgrade to Pro to invite teammates."
    default_code = "seat_limit_exceeded"

    def __init__(self, detail: str | None = None):
        msg = detail or str(self.default_detail)
        super().__init__(detail={"detail": msg, "code": self.default_code}, code=self.default_code)


def subscription_is_pro(user: User) -> bool:
    from overbae.models import Subscription, SubscriptionStatus

    try:
        sub = user.subscription
    except Subscription.DoesNotExist:
        return False
    return sub.status == SubscriptionStatus.ACTIVE


def is_pro(user: User) -> bool:
    from overbae.services.billing_provider import get_billing

    return get_billing().is_pro(user)


def enforce_seat_for_invite(actor: User, project: Project) -> None:
    """Free actors cannot add members once the project already has a seat filled.

    Pending invites count as filled seats — they become memberships without a
    further plan check when the invitee signs up.
    """
    if subscription_is_pro(actor):
        return
    from overbae.models import ProjectInvite, ProjectMembership

    seats = FREE_LIMITS["seats"]
    n = (
        ProjectMembership.objects.filter(project=project).count()
        + ProjectInvite.objects.filter(project=project).count()
    )
    if n >= seats:
        raise SeatLimitExceeded()


def require_seat_for_invite(actor: User, project: Project) -> None:
    from overbae.services.billing_provider import get_billing

    get_billing().require_seat_for_invite(actor, project)


def usage_payload(user: User, *, unlimited: bool) -> dict:
    """Limits for the subscription API (null limit = unlimited)."""
    uncapped = unlimited or subscription_is_pro(user)
    return {"seats": {"limit": None if uncapped else FREE_LIMITS["seats"]}}


def plan_usage_payload(user: User) -> dict:
    from overbae.services.billing_provider import get_billing

    return get_billing().plan_usage_payload(user)
