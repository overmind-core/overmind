"""Commercial billing vs uncapped metering.

Ledger charges always run. Remaining-credit gates, signup grants and Stripe
Checkout exist only when ``STRIPE_SECRET_KEY`` is set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from django.conf import settings

if TYPE_CHECKING:
    from overbae.models import BillingTelemetry, User

__all__ = ["Billing", "commercial_billing_enabled", "get_billing"]


def commercial_billing_enabled() -> bool:
    return bool((getattr(settings, "STRIPE_SECRET_KEY", "") or "").strip())


@runtime_checkable
class Billing(Protocol):
    enabled: bool

    def ensure_credits(self, user: User) -> None: ...
    def grant_signup_credits(self, user: User) -> BillingTelemetry | None: ...


class UncappedBilling:
    enabled = False

    def ensure_credits(self, user: User) -> None:
        return

    def grant_signup_credits(self, user: User) -> BillingTelemetry | None:
        return None


class StripeBilling:
    enabled = True

    def ensure_credits(self, user: User) -> None:
        # Lazy import: billing_ledger imports this module.
        from overbae.services.billing_ledger import InsufficientCredits, balance_usd

        if balance_usd(user) <= 0:
            raise InsufficientCredits("Insufficient credits.")

    def grant_signup_credits(self, user: User) -> BillingTelemetry | None:
        # Lazy import: billing_ledger imports this module.
        from overbae.models import BillingService
        from overbae.services.billing_ledger import SIGNUP_CREDITS_USD, append_entry

        return append_entry(
            user=user,
            amount=SIGNUP_CREDITS_USD,
            service=BillingService.GRANT,
            idempotency_key=f"signup:{user.pk}",
            metadata={"reason": "signup"},
        )


_STRIPE = StripeBilling()
_UNCAPPED = UncappedBilling()


def get_billing() -> Billing:
    return _STRIPE if commercial_billing_enabled() else _UNCAPPED
