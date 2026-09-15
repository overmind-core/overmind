"""Append-only credits ledger. Balance = SUM(BillingTelemetry.amount) per user."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Sum

from overbae.models import BillingService, BillingTelemetry, User

logger = logging.getLogger(__name__)

FREE_CREDITS_USD = Decimal("50")

# Display/purchase denomination: the ledger stores USD, users buy and see credits.
CREDITS_PER_USD = 100
MIN_TOPUP_USD = 1
MAX_TOPUP_USD = 1000


class InsufficientCredits(Exception):  # noqa: N818
    """Raised when the user has no credits left for paid work."""


def balance_usd(user: User) -> Decimal:
    total = BillingTelemetry.objects.filter(user=user).aggregate(s=Sum("amount"))["s"]
    return total if total is not None else Decimal("0")


def granted_usd(user: User) -> Decimal:
    """Lifetime credits granted (positive rows only) — the usage meters' denominator."""
    total = BillingTelemetry.objects.filter(user=user, amount__gt=0).aggregate(s=Sum("amount"))["s"]
    return total if total is not None else Decimal("0")


def spent_usd(user: User) -> Decimal:
    """Lifetime usage (absolute sum of negative rows)."""
    total = BillingTelemetry.objects.filter(user=user, amount__lt=0).aggregate(s=Sum("amount"))["s"]
    if total is None:
        return Decimal("0")
    return -total


def append_entry(
    *,
    user: User,
    amount: Decimal,
    service: str,
    project_id: UUID | str | None = None,
    idempotency_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> BillingTelemetry | None:
    """Insert a ledger row. Duplicate ``idempotency_key`` → no-op (returns None)."""
    try:
        with transaction.atomic():
            return BillingTelemetry.objects.create(
                user=user,
                project_id=project_id,
                amount=amount,
                service=service,
                idempotency_key=idempotency_key,
                metadata=metadata or {},
            )
    except IntegrityError:
        logger.info(
            "billing ledger idempotent skip key=%s user_id=%s",
            idempotency_key,
            user.pk,
        )
        return None


def grant_free_credits(user: User) -> BillingTelemetry | None:
    from overbae.services.billing_provider import get_billing

    return get_billing().grant_free_credits(user)


def grant_stripe_topup(user: User, amount: Decimal, invoice_id: str) -> BillingTelemetry | None:
    if amount <= 0:
        return None
    return append_entry(
        user=user,
        amount=amount,
        service=BillingService.STRIPE_TOPUP,
        idempotency_key=invoice_id,
    )


def grant_credit_purchase(user: User, amount: Decimal, session_id: str) -> BillingTelemetry | None:
    """One-time credit purchase. The ``topup:`` key namespace is distinct from
    ``grant_stripe_topup``'s bare invoice id so the two Stripe paths cannot collide.
    """
    if amount <= 0:
        return None
    return append_entry(
        user=user,
        amount=amount,
        service=BillingService.STRIPE_TOPUP,
        idempotency_key=f"topup:{session_id}",
        metadata={"checkout_session_id": session_id},
    )


def charge_credits(
    user: User,
    amount: Decimal,
    service: str,
    *,
    project_id: UUID | str | None = None,
    idempotency_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> BillingTelemetry | None:
    """Record usage as a negative ledger row."""
    if amount <= 0:
        return None
    return append_entry(
        user=user,
        amount=-amount,
        service=service,
        project_id=project_id,
        idempotency_key=idempotency_key,
        metadata=metadata,
    )


def ensure_credits(user: User) -> None:
    from overbae.services.billing_provider import get_billing

    get_billing().ensure_credits(user)


# Composer is a Cursor model with no OpenRouter listing, so its turns are priced
# against the closest stand-in: Cursor built Composer 2.5 on Kimi K2.5, and the two
# list within 10% of each other ($0.45/$2.25 against $0.50/$2.50 per 1M tokens).
CURSOR_BILLING_PROXY_MODEL = "moonshotai/kimi-k2.5"


def charge_cursor_usage(
    user: User,
    usage: dict[str, Any] | None,
    *,
    service: str,
    project_id: UUID | str | None = None,
    idempotency_key: str,
    metadata: dict[str, Any] | None = None,
) -> BillingTelemetry | None:
    """Debit credits from Cursor SDK token usage. Never raises."""
    from overbae.services.model_catalog import estimate_cost

    if user is None or not usage:
        return None
    cost = estimate_cost(
        CURSOR_BILLING_PROXY_MODEL,
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        cached_tokens=int(usage.get("cache_read_tokens") or 0),
    )
    if not cost:
        return None
    meta = {"cursor_usage": usage, **(metadata or {})}
    try:
        return charge_credits(
            user,
            Decimal(str(cost)),
            service,
            project_id=project_id,
            idempotency_key=idempotency_key,
            metadata=meta,
        )
    except Exception:
        logger.exception(
            "Failed to charge cursor usage key=%s user_id=%s",
            idempotency_key,
            getattr(user, "pk", None),
        )
        return None
