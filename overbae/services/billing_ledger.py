"""Append-only credits ledger. Balance = SUM(BillingTelemetry.amount) per user."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum

from overbae.models import BillingService, BillingTelemetry, User
from overbae.services import model_catalog

logger = logging.getLogger(__name__)

FREE_CREDITS_USD = Decimal("50")

# Display/purchase denomination: the ledger stores USD, users buy and see credits.
CREDITS_PER_USD = 100
MIN_TOPUP_USD = 1
MAX_TOPUP_USD = 1000


def credit_report(
    user: User,
    *,
    since: datetime | None,
    observed_at: datetime,
) -> dict[str, Any]:
    window = Q(amount__lt=0) & (Q(timestamp__gte=since) if since is not None else Q())
    rows = list(
        BillingTelemetry.objects.filter(user=user, timestamp__lte=observed_at)
        .values("service")
        .annotate(
            balance=Sum("amount"),
            granted=Sum("amount", filter=Q(amount__gt=0), default=Decimal("0")),
            spent=Sum("amount", filter=Q(amount__lt=0), default=Decimal("0")),
            used=Sum("amount", filter=window, default=Decimal("0")),
        )
        .order_by("service")
    )
    by_service = [
        {"service": row["service"], "spent_credits": -row["used"] * CREDITS_PER_USD}
        for row in rows
        if row["used"]
    ]
    return {
        "balance_credits": sum((row["balance"] for row in rows), Decimal("0")) * CREDITS_PER_USD,
        "granted_credits": sum((row["granted"] for row in rows), Decimal("0")) * CREDITS_PER_USD,
        "spent_credits": -sum((row["spent"] for row in rows), Decimal("0")) * CREDITS_PER_USD,
        "usage": {
            "spent_credits": -sum((row["used"] for row in rows), Decimal("0")) * CREDITS_PER_USD,
            "by_service": by_service,
        },
    }


class InsufficientCredits(Exception):  # noqa: N818
    """Raised when the user has no credits left for paid work."""


def to_credits(amount_usd: Decimal) -> Decimal:
    return (amount_usd * CREDITS_PER_USD).quantize(Decimal("0.01"))


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


def _attempt_cost(stats: dict[str, Any]) -> tuple[float, bool]:
    if stats.get("cached"):
        return 0.0, False
    cost = stats.get("response_cost")
    if cost is not None:
        return float(cost), False
    attempts = stats.get("attempts")
    if attempts:
        priced = [_attempt_cost(attempt) for attempt in attempts]
        return sum(value for value, _ in priced), any(unknown for _, unknown in priced)
    estimate = model_catalog.estimate_cost(
        str(stats.get("served_model") or ""),
        int(stats.get("prompt_tokens") or 0),
        int(stats.get("completion_tokens") or 0),
        cached_tokens=int(stats.get("cached_tokens") or 0),
    )
    return (estimate, False) if estimate is not None else (0.0, True)


def charge_llm_usage(
    user: User,
    stats: dict[str, Any] | None,
    *,
    service: str,
    project_id: UUID | str | None = None,
    idempotency_key: str,
    metadata: dict[str, Any] | None = None,
) -> BillingTelemetry | None:
    if user is None or not stats:
        return None
    if stats.get("cached"):
        return None
    cost, incomplete = _attempt_cost(stats)
    metadata = {**(metadata or {}), "cost_incomplete": incomplete}
    if not cost:
        return None
    try:
        return charge_credits(
            user,
            Decimal(str(cost)),
            service,
            project_id=project_id,
            idempotency_key=idempotency_key,
            metadata={"llm_usage": stats, **(metadata or {})},
        )
    except Exception:
        logger.exception(
            "Failed to charge llm usage key=%s user_id=%s",
            idempotency_key,
            getattr(user, "pk", None),
        )
        return None
