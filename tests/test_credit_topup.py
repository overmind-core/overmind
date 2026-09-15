from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.api.billing import (
    CREDIT_TOPUP_PURPOSE,
    grant_credits_from_checkout_session,
    renew_plan_from_invoice,
)
from overbae.models import BillingService, BillingTelemetry, Subscription, SubscriptionStatus
from overbae.services.billing_ledger import FREE_CREDITS_USD, balance_usd

pytestmark = pytest.mark.django_db

User = get_user_model()

SANDBOX_PRICE = "price_1TxoWdCjF8jVtWKgOWHw2oFE"


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def _make_user(slug: str, **extra) -> User:
    return User.objects.create_user(
        email=f"{slug}@example.com",
        password="x",
        clerk_user_id=f"clerk_{slug}",
        **extra,
    )


def _stub_stripe(monkeypatch, settings, calls: list[dict]) -> None:
    settings.STRIPE_CREDIT_PRICE_ID = SANDBOX_PRICE

    def fake_create(**kwargs):
        calls.append(kwargs)
        return type("Session", (), {"id": "cs_test", "url": "https://checkout.stripe.test/cs_test"})

    monkeypatch.setattr("overbae.api.billing._configure_stripe", lambda: True)
    monkeypatch.setattr("overbae.api.billing._get_or_create_customer", lambda user: "cus_test")
    monkeypatch.setattr("overbae.api.billing.stripe.checkout.Session.create", fake_create)


def _session(
    *,
    session_id: str = "cs_test",
    amount_total: int = 2500,
    user_id: str | None = None,
    purpose: str | None = CREDIT_TOPUP_PURPOSE,
    payment_status: str = "paid",
    customer: str = "cus_test",
) -> dict:
    metadata: dict[str, str] = {}
    if purpose is not None:
        metadata["purpose"] = purpose
    if user_id is not None:
        metadata["user_id"] = user_id
    return {
        "id": session_id,
        "customer": customer,
        "amount_total": amount_total,
        "payment_status": payment_status,
        "client_reference_id": user_id,
        "metadata": metadata,
    }


def test_topup_checkout_uses_credit_quantity(monkeypatch, settings):
    """quantity is the CREDIT count, not the dollar count — the price is $0.01/credit."""
    user = _make_user("topup-quantity")
    calls: list[dict] = []
    _stub_stripe(monkeypatch, settings, calls)

    r = _auth_client(user).post(reverse("billing-topup"), {"amount_usd": 25}, format="json")

    assert r.status_code == 200
    assert r.data["checkout_url"] == "https://checkout.stripe.test/cs_test"
    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["mode"] == "payment"
    assert kwargs["line_items"] == [{"price": SANDBOX_PRICE, "quantity": 2500}]
    assert kwargs["client_reference_id"] == str(user.pk)
    assert kwargs["metadata"]["purpose"] == CREDIT_TOPUP_PURPOSE
    assert kwargs["metadata"]["user_id"] == str(user.pk)
    assert kwargs["payment_intent_data"]["metadata"]["credits"] == "2500"
    # No adjustable_quantity: the amount must stay fixed server-side.
    assert "adjustable_quantity" not in kwargs["line_items"][0]


@pytest.mark.parametrize("amount", [0, -5, 1001])
def test_topup_rejects_out_of_range_amount(monkeypatch, settings, amount):
    user = _make_user(f"topup-range-{abs(amount)}")
    calls: list[dict] = []
    _stub_stripe(monkeypatch, settings, calls)

    r = _auth_client(user).post(reverse("billing-topup"), {"amount_usd": amount}, format="json")

    assert r.status_code == 400
    assert calls == []


def test_topup_available_on_free_plan(monkeypatch, settings):
    user = _make_user("topup-free")
    calls: list[dict] = []
    _stub_stripe(monkeypatch, settings, calls)

    r = _auth_client(user).post(reverse("billing-topup"), {"amount_usd": 1}, format="json")

    assert r.status_code == 200
    assert calls[0]["line_items"][0]["quantity"] == 100


def test_topup_requires_configured_price(monkeypatch, settings):
    user = _make_user("topup-unconfigured")
    settings.STRIPE_CREDIT_PRICE_ID = ""
    monkeypatch.setattr("overbae.api.billing._configure_stripe", lambda: True)

    r = _auth_client(user).post(reverse("billing-topup"), {"amount_usd": 10}, format="json")

    assert r.status_code == 503


def test_completed_session_grants_credits_from_amount_total():
    user = _make_user("topup-grant", stripe_customer_id="cus_test")

    grant_credits_from_checkout_session(
        _session(session_id="cs_grant", amount_total=2500, user_id=str(user.pk))
    )

    rows = BillingTelemetry.objects.filter(
        user=user, service=BillingService.STRIPE_TOPUP, idempotency_key="topup:cs_grant"
    )
    assert rows.count() == 1
    assert rows.get().amount == Decimal("25")
    assert balance_usd(user) == FREE_CREDITS_USD + Decimal("25")


def test_completed_session_is_idempotent():
    user = _make_user("topup-replay", stripe_customer_id="cus_test")
    session = _session(session_id="cs_replay", amount_total=1000, user_id=str(user.pk))

    grant_credits_from_checkout_session(session)
    grant_credits_from_checkout_session(session)

    assert BillingTelemetry.objects.filter(idempotency_key="topup:cs_replay").count() == 1
    assert balance_usd(user) == FREE_CREDITS_USD + Decimal("10")


def test_session_without_topup_purpose_is_ignored():
    """A Pro subscription checkout also fires checkout.session.completed."""
    user = _make_user("topup-other-purpose", stripe_customer_id="cus_test")

    grant_credits_from_checkout_session(
        _session(session_id="cs_sub", user_id=str(user.pk), purpose=None)
    )

    assert not BillingTelemetry.objects.filter(service=BillingService.STRIPE_TOPUP).exists()
    assert balance_usd(user) == FREE_CREDITS_USD


def test_unpaid_session_grants_nothing():
    user = _make_user("topup-unpaid", stripe_customer_id="cus_test")

    grant_credits_from_checkout_session(
        _session(session_id="cs_unpaid", user_id=str(user.pk), payment_status="unpaid")
    )

    assert not BillingTelemetry.objects.filter(service=BillingService.STRIPE_TOPUP).exists()


def test_malformed_client_reference_id_falls_back_to_customer():
    """A bad pk must not raise — a 500 here makes Stripe retry the delivery forever."""
    user = _make_user("topup-bad-ref", stripe_customer_id="cus_badref")

    grant_credits_from_checkout_session(
        _session(
            session_id="cs_badref",
            amount_total=300,
            user_id="not-a-user-id",
            customer="cus_badref",
        )
    )

    assert balance_usd(user) == FREE_CREDITS_USD + Decimal("3")


def test_session_resolves_user_by_customer_id_alone():
    user = _make_user("topup-by-customer", stripe_customer_id="cus_lonely")

    grant_credits_from_checkout_session(
        _session(session_id="cs_cust", amount_total=500, customer="cus_lonely")
    )

    assert balance_usd(user) == FREE_CREDITS_USD + Decimal("5")


def test_invoice_without_subscription_does_not_activate_pro():
    user = _make_user("topup-invoice-guard", projects_limit=5, stripe_customer_id="cus_test")
    Subscription.objects.create(user=user, status=SubscriptionStatus.INCOMPLETE)

    renew_plan_from_invoice(
        {
            "id": "in_topup",
            "customer": "cus_test",
            "amount_paid": 2500,
            "lines": {"data": [{"period": {"start": 1861920000, "end": 1893456000}}]},
        }
    )

    user.refresh_from_db()
    assert user.projects_limit == 5
    assert user.subscription.status == SubscriptionStatus.INCOMPLETE
    assert balance_usd(user) == FREE_CREDITS_USD
