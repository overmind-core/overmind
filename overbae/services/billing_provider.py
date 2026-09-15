"""Commercial billing vs uncapped metering.

Ledger charges always run. Remaining-credit gates, signup grants, Stripe, and
Free/Pro quotas exist only when ``STRIPE_SECRET_KEY`` is set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from django.conf import settings

if TYPE_CHECKING:
    from overbae.models import BillingTelemetry, Project, User
    from overbae.services.plan_limits import QuotaKind

__all__ = ["Billing", "commercial_billing_enabled", "get_billing"]


def commercial_billing_enabled() -> bool:
    return bool((getattr(settings, "STRIPE_SECRET_KEY", "") or "").strip())


@runtime_checkable
class Billing(Protocol):
    enabled: bool

    def ensure_credits(self, user: User) -> None: ...
    def grant_free_credits(self, user: User) -> BillingTelemetry | None: ...
    def is_pro(self, user: User) -> bool: ...
    def require_plan_quota(self, user: User, kind: QuotaKind) -> None: ...
    def require_seat_for_invite(self, actor: User, project: Project) -> None: ...
    def effective_projects_limit(self, user: User) -> int | None: ...
    def plan_usage_payload(self, user: User) -> dict: ...


class UncappedBilling:
    enabled = False

    def ensure_credits(self, user: User) -> None:
        return

    def grant_free_credits(self, user: User) -> BillingTelemetry | None:
        return None

    def is_pro(self, user: User) -> bool:
        return False

    def require_plan_quota(self, user: User, kind: QuotaKind) -> None:
        return

    def require_seat_for_invite(self, actor: User, project: Project) -> None:
        return

    def effective_projects_limit(self, user: User) -> int | None:
        return None

    def plan_usage_payload(self, user: User) -> dict:
        from overbae.services.plan_limits import usage_payload

        return usage_payload(user, unlimited=True)


class StripeBilling:
    enabled = True

    def ensure_credits(self, user: User) -> None:
        from overbae.services.billing_ledger import InsufficientCredits, balance_usd

        if balance_usd(user) <= 0:
            raise InsufficientCredits("Insufficient credits.")

    def grant_free_credits(self, user: User) -> BillingTelemetry | None:
        from overbae.models import BillingService
        from overbae.services.billing_ledger import FREE_CREDITS_USD, append_entry

        return append_entry(
            user=user,
            amount=FREE_CREDITS_USD,
            service=BillingService.FREE_CREDITS,
            idempotency_key=f"free-credits:{user.pk}",
        )

    def is_pro(self, user: User) -> bool:
        from overbae.services.plan_limits import subscription_is_pro

        return subscription_is_pro(user)

    def require_plan_quota(self, user: User, kind: QuotaKind) -> None:
        from overbae.services.plan_limits import enforce_plan_quota

        enforce_plan_quota(user, kind)

    def require_seat_for_invite(self, actor: User, project: Project) -> None:
        from overbae.services.plan_limits import enforce_seat_for_invite

        enforce_seat_for_invite(actor, project)

    def effective_projects_limit(self, user: User) -> int | None:
        from overbae.services.plan_limits import capped_projects_limit

        return capped_projects_limit(user)

    def plan_usage_payload(self, user: User) -> dict:
        from overbae.services.plan_limits import usage_payload

        return usage_payload(user, unlimited=False)


_STRIPE = StripeBilling()
_UNCAPPED = UncappedBilling()


def get_billing() -> Billing:
    return _STRIPE if commercial_billing_enabled() else _UNCAPPED
