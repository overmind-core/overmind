"""Free/Pro plan entitlements: monthly run caps, projects, seats.

Credits (``require_credits``) gate paid compute; this module gates *how many* of
each priced action Free users may start per calendar month UTC. Pro
(``Subscription.status == ACTIVE``) → unlimited; ``past_due`` / canceled / missing
sub → Free caps. Check-then-create can slightly overshoot under concurrency — no
advisory lock.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from rest_framework.exceptions import APIException

if TYPE_CHECKING:
    from overbae.models import Project, User

QuotaKind = Literal["optimize_runs", "training_jobs", "deploy_jobs"]

FREE_LIMITS: dict[str, int] = {
    "projects": 5,
    "seats": 1,
    "optimize_runs": 10,
    "training_jobs": 2,
    "deploy_jobs": 2,
}

__all__ = [
    "FREE_LIMITS",
    "PlanLimitExceeded",
    "SeatLimitExceeded",
    "capped_projects_limit",
    "effective_projects_limit",
    "enforce_plan_quota",
    "enforce_seat_for_invite",
    "is_pro",
    "month_start_utc",
    "plan_usage_payload",
    "quota_exceeded_message",
    "require_plan_quota",
    "require_seat_for_invite",
    "subscription_is_pro",
    "usage_count",
    "usage_payload",
]


class PlanLimitExceeded(APIException):
    status_code = 403
    default_detail = "Plan limit exceeded."
    default_code = "plan_limit_exceeded"

    def __init__(self, detail: str | None = None, *, code: str | None = None):
        msg = detail or str(self.default_detail)
        body_code = code or self.default_code
        super().__init__(detail={"detail": msg, "code": body_code}, code=body_code)


class SeatLimitExceeded(PlanLimitExceeded):
    default_detail = "Free plan is limited to one seat. Upgrade to Pro to invite teammates."
    default_code = "seat_limit_exceeded"


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


def capped_projects_limit(user: User) -> int | None:
    """Max project memberships under commercial billing. ``None`` = unlimited."""
    if subscription_is_pro(user):
        return None
    if user.projects_limit is not None:
        return user.projects_limit
    return FREE_LIMITS["projects"]


def effective_projects_limit(user: User) -> int | None:
    from overbae.services.billing_provider import get_billing

    return get_billing().effective_projects_limit(user)


def month_start_utc() -> datetime:
    now = datetime.now(tz=UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def usage_count(user: User, kind: QuotaKind) -> int:
    """How many quota units ``user`` has consumed this calendar month UTC."""
    start = month_start_utc()

    if kind == "optimize_runs":
        from overbae.models import OptimizerExperiment

        return OptimizerExperiment.objects.filter(triggered_by=user, created_at__gte=start).count()

    if kind == "training_jobs":
        from overbae.models import FinetuningJob

        return FinetuningJob.objects.filter(triggered_by=user, created_at__gte=start).count()

    if kind == "deploy_jobs":
        from overbae.models import DeployedModel

        return DeployedModel.objects.filter(
            finetuning_job__triggered_by=user,
            created_at__gte=start,
        ).count()

    raise ValueError(f"Unknown quota kind: {kind}")


def _free_limit(kind: str) -> int | None:
    return FREE_LIMITS.get(kind)


def quota_exceeded_message(kind: QuotaKind) -> str:
    limit = FREE_LIMITS[kind]
    labels = {
        "optimize_runs": "optimise runs",
        "training_jobs": "training jobs",
        "deploy_jobs": "deploy jobs",
    }
    return f"Free plan allows {limit} {labels[kind]} per month. Upgrade to Pro for unlimited."


def enforce_plan_quota(user: User, kind: QuotaKind) -> None:
    if subscription_is_pro(user):
        return
    limit = _free_limit(kind)
    if limit is None:
        return
    if usage_count(user, kind) >= limit:
        raise PlanLimitExceeded(quota_exceeded_message(kind))


def require_plan_quota(user: User, kind: QuotaKind) -> None:
    from overbae.services.billing_provider import get_billing

    get_billing().require_plan_quota(user, kind)


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
    """Usage + limits for the subscription API (null limit = unlimited)."""
    from overbae.models import ProjectMembership

    uncapped = unlimited or subscription_is_pro(user)
    projects_used = ProjectMembership.objects.filter(user=user).count()

    def unit(kind: QuotaKind) -> dict:
        return {
            "used": usage_count(user, kind),
            "limit": None if uncapped else FREE_LIMITS[kind],
        }

    return {
        "optimize_runs": unit("optimize_runs"),
        "training_jobs": unit("training_jobs"),
        "deploy_jobs": unit("deploy_jobs"),
        "projects": {
            "used": projects_used,
            "limit": None if uncapped else FREE_LIMITS["projects"],
        },
        "seats": {"limit": None if uncapped else FREE_LIMITS["seats"]},
    }


def plan_usage_payload(user: User) -> dict:
    from overbae.services.billing_provider import get_billing

    return get_billing().plan_usage_payload(user)
