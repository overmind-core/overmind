"""Credit purchases through one-time Stripe Checkout.

A paid session grants its amount once, keyed on the session id. The Console
confirms the session when the buyer returns, and the webhook covers payment
methods that settle later; whichever arrives first grants.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import stripe
from django.conf import settings

from overbae.models import BillingTelemetry, User
from overbae.services.billing_ledger import CREDITS_PER_USD, balance_usd, grant_credit_purchase

logger = logging.getLogger(__name__)

__all__ = [
    "CREDIT_TOPUP_PURPOSE",
    "WEBHOOK_EVENTS",
    "PurchaseUnavailable",
    "SessionNotFound",
    "configure_stripe",
    "confirm_session",
    "create_topup_session",
    "grant_from_session",
    "sync_webhook_endpoint",
]

CREDIT_TOPUP_PURPOSE = "credit-topup"
WEBHOOK_EVENTS = ("checkout.session.completed", "checkout.session.async_payment_succeeded")


class PurchaseUnavailable(Exception):  # noqa: N818
    """Checkout cannot start: the credit price is not configured."""


class SessionNotFound(Exception):  # noqa: N818
    """The session is not a credit top-up that belongs to the caller."""


def configure_stripe() -> bool:
    key = (getattr(settings, "STRIPE_SECRET_KEY", "") or "").strip()
    if not key:
        return False
    stripe.api_key = key
    # The SDK's default 80s read timeout parks a web worker per request during a
    # Stripe brownout.
    stripe.max_network_retries = 1
    if getattr(stripe.default_http_client, "_overmind_timeout", None) != 20:
        client = stripe.new_default_http_client(timeout=20)
        client._overmind_timeout = 20
        stripe.default_http_client = client
    return True


def _as_dict(obj: Any) -> dict[str, Any]:
    """Stripe SDK objects have no ``.get``; every handler here works on dicts."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    try:
        return dict(obj)
    except (TypeError, ValueError):
        return {}


def _stripe_id(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    data = _as_dict(value)
    if data:
        return data.get("id") or ""
    return getattr(value, "id", None) or ""


def _cents_to_usd(raw: Any) -> Decimal:
    if raw is None:
        return Decimal("0")
    return (Decimal(int(raw)) / Decimal("100")).quantize(Decimal("0.0001"))


def _customer_for(user: User) -> str:
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = stripe.Customer.create(email=user.email, metadata={"user_id": str(user.pk)})
    user.stripe_customer_id = customer.id
    user.save(update_fields=["stripe_customer_id"])
    return customer.id


def _remember_customer(user: User, customer_id: str) -> None:
    if not customer_id or user.stripe_customer_id == customer_id:
        return
    user.stripe_customer_id = customer_id
    user.save(update_fields=["stripe_customer_id"])


def _user_by_pk(raw_id: Any) -> User | None:
    """A malformed id must not 500: Stripe would retry the event forever."""
    try:
        return User.objects.filter(pk=raw_id).first()
    except (TypeError, ValueError):
        logger.warning("Ignoring malformed user id from Stripe payload: %r", raw_id)
        return None


def _buyer(session: dict[str, Any]) -> User | None:
    """Precedence: client_reference_id, then metadata, then the customer id."""
    metadata = _as_dict(session.get("metadata"))
    for raw_id in (session.get("client_reference_id"), metadata.get("user_id")):
        if raw_id:
            user = _user_by_pk(raw_id)
            if user:
                return user
    customer_id = _stripe_id(session.get("customer"))
    if customer_id:
        return User.objects.filter(stripe_customer_id=customer_id).first()
    return None


def _is_topup(session: dict[str, Any]) -> bool:
    return _as_dict(session.get("metadata")).get("purpose") == CREDIT_TOPUP_PURPOSE


def _grant(user: User, session: dict[str, Any]) -> BillingTelemetry | None:
    session_id = _stripe_id(session.get("id"))
    # Async payment methods complete the session before the money lands.
    if session.get("payment_status") != "paid":
        return None
    # amount_total is what Stripe charged; request metadata is only what we asked for.
    amount = _cents_to_usd(session.get("amount_total"))
    if amount <= 0:
        logger.error("Checkout session=%s has no amount_total", session_id)
        return None
    _remember_customer(user, _stripe_id(session.get("customer")))
    entry = grant_credit_purchase(user, amount, session_id)
    if entry is not None:
        logger.info(
            "Credit purchase granted user_id=%s session=%s amount=%s balance=%s",
            user.pk,
            session_id,
            amount,
            balance_usd(user),
        )
    return entry


def create_topup_session(user: User, amount_usd: int) -> str:
    """Returns the Checkout URL. Raises ``stripe.StripeError`` when Stripe fails."""
    price_id = (getattr(settings, "STRIPE_CREDIT_PRICE_ID", "") or "").strip()
    if not price_id:
        raise PurchaseUnavailable("Credit price is not configured.")
    # The Stripe price is one credit at $0.01, so quantity is the credit count.
    credit_quantity = amount_usd * CREDITS_PER_USD
    frontend = (getattr(settings, "FRONTEND_URL", "") or "http://localhost:5173").rstrip("/")
    metadata = {
        "user_id": str(user.pk),
        "purpose": CREDIT_TOPUP_PURPOSE,
        "amount_usd": str(amount_usd),
        "credits": str(credit_quantity),
    }
    session = stripe.checkout.Session.create(
        mode="payment",
        customer=_customer_for(user),
        line_items=[{"price": price_id, "quantity": credit_quantity}],
        # Stripe substitutes the literal {CHECKOUT_SESSION_ID} on redirect.
        success_url=f"{frontend}/settings?billing=topup&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{frontend}/settings?billing=cancel",
        client_reference_id=str(user.pk),
        metadata=metadata,
        payment_intent_data={"metadata": metadata},
    )
    if not session.url:
        raise stripe.APIError("Stripe did not return a checkout URL.")
    return session.url


def grant_from_session(session: Any) -> BillingTelemetry | None:
    """Webhook path: the buyer is resolved from the session itself."""
    session = _as_dict(session)
    session_id = _stripe_id(session.get("id"))
    if not _is_topup(session):
        return None
    user = _buyer(session)
    if user is None:
        logger.error(
            "Checkout session=%s: no user for customer=%s",
            session_id,
            _stripe_id(session.get("customer")),
        )
        return None
    return _grant(user, session)


def confirm_session(user: User, session_id: str) -> bool:
    """Return path: grants a paid session the caller bought. Returns whether it is paid.

    Raises ``SessionNotFound`` for another user's session or one that is not a
    top-up, and ``stripe.StripeError`` when Stripe fails.
    """
    try:
        session = _as_dict(stripe.checkout.Session.retrieve(session_id))
    except stripe.InvalidRequestError as exc:
        raise SessionNotFound(session_id) from exc
    buyer = _buyer(session)
    if not _is_topup(session) or buyer is None or buyer.pk != user.pk:
        raise SessionNotFound(session_id)
    _grant(user, session)
    return session.get("payment_status") == "paid"


def sync_webhook_endpoint(url: str) -> list[str]:
    """Sets the endpoint's events to exactly ``WEBHOOK_EVENTS``. Raises ``LookupError``."""
    for endpoint in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter():
        if endpoint.url == url:
            updated = stripe.WebhookEndpoint.modify(
                endpoint.id, enabled_events=list(WEBHOOK_EVENTS)
            )
            return list(updated.enabled_events)
    raise LookupError(f"No Stripe webhook endpoint for {url}")
