from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from factories import auth_client
from rest_framework.test import APIClient

from overbae.api.billing import CREDIT_TOPUP_PURPOSE
from overbae.models import BillingService, BillingTelemetry, Subscription, SubscriptionStatus
from overbae.services.billing_ledger import FREE_CREDITS_USD, balance_usd

pytestmark = pytest.mark.django_db

User = get_user_model()

SANDBOX_PRICE = "price_1TxoWdCjF8jVtWKgOWHw2oFE"


def _make_user(slug: str, **extra) -> User:
    return User.objects.create_user(
        email=f"{slug}@example.com", password="x", clerk_user_id=f"clerk_{slug}", **extra
    )


@pytest.fixture
def stripe(stripe_api, settings):
    settings.STRIPE_CREDIT_PRICE_ID = SANDBOX_PRICE
    return stripe_api


def _deliver(stripe, event_type: str, obj: dict) -> int:
    payload, signature = stripe.signed(
        {"id": f"evt_{obj['id']}", "type": event_type, "data": {"object": obj}}
    )
    return (
        APIClient()
        .post(
            "/api/billing/webhook/",
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=signature,
        )
        .status_code
    )


def _session(
    *,
    session_id: str,
    amount_total: int = 2500,
    user_id: str | None = None,
    purpose: str | None = CREDIT_TOPUP_PURPOSE,
    payment_status: str = "paid",
    customer: str = "cus_test",
) -> dict:
    metadata = {k: v for k, v in (("purpose", purpose), ("user_id", user_id)) if v is not None}
    return {
        "id": session_id,
        "object": "checkout.session",
        "customer": customer,
        "amount_total": amount_total,
        "payment_status": payment_status,
        "client_reference_id": user_id,
        "metadata": metadata,
    }


def test_topup_checkout_charges_a_fixed_credit_quantity(stripe):
    user = _make_user("topup-quantity")

    r = auth_client(user).post(reverse("billing-topup"), {"amount_usd": 25}, format="json")

    assert r.status_code == 200
    [session] = stripe.sessions
    assert r.data["checkout_url"] == session["url"]
    assert session["mode"] == "payment"
    assert session["line_items"] == [{"price": SANDBOX_PRICE, "quantity": "2500"}]
    assert session["client_reference_id"] == str(user.pk)
    assert session["metadata"]["purpose"] == CREDIT_TOPUP_PURPOSE
    assert session["metadata"]["user_id"] == str(user.pk)
    assert session["payment_intent_data"]["metadata"]["credits"] == "2500"
    user.refresh_from_db()
    assert user.stripe_customer_id in stripe.customers


@pytest.mark.parametrize("amount", [0, -5, 1001])
def test_topup_rejects_out_of_range_amount(stripe, amount):
    user = _make_user(f"topup-range-{abs(amount)}")
    r = auth_client(user).post(reverse("billing-topup"), {"amount_usd": amount}, format="json")
    assert r.status_code == 400
    assert stripe.sessions == []


def test_topup_available_on_free_plan(stripe):
    user = _make_user("topup-free")
    r = auth_client(user).post(reverse("billing-topup"), {"amount_usd": 1}, format="json")
    assert r.status_code == 200
    assert stripe.sessions[0]["line_items"][0]["quantity"] == "100"


def test_topup_requires_configured_price(stripe, settings):
    settings.STRIPE_CREDIT_PRICE_ID = ""
    user = _make_user("topup-unconfigured")
    r = auth_client(user).post(reverse("billing-topup"), {"amount_usd": 10}, format="json")
    assert r.status_code == 503


def test_a_stripe_outage_during_checkout_is_a_bad_gateway(stripe):
    stripe.down = True
    user = _make_user("topup-outage")
    r = auth_client(user).post(reverse("billing-topup"), {"amount_usd": 10}, format="json")
    assert r.status_code == 502


def test_a_paid_topup_grants_its_amount_once(stripe):
    user = _make_user("topup-grant", stripe_customer_id="cus_test")
    session = _session(session_id="cs_grant", amount_total=2500, user_id=str(user.pk))

    assert _deliver(stripe, "checkout.session.completed", session) < 300
    assert _deliver(stripe, "checkout.session.completed", session) < 300

    rows = BillingTelemetry.objects.filter(
        user=user, service=BillingService.STRIPE_TOPUP, idempotency_key="topup:cs_grant"
    )
    assert rows.get().amount == Decimal("25")
    assert balance_usd(user) == FREE_CREDITS_USD + Decimal("25")


@pytest.mark.parametrize(
    "overrides",
    [{"purpose": None}, {"payment_status": "unpaid"}],
    ids=["subscription checkout", "unpaid"],
)
def test_a_session_that_is_not_a_paid_topup_grants_nothing(stripe, overrides):
    user = _make_user("topup-ignored", stripe_customer_id="cus_test")
    _deliver(
        stripe,
        "checkout.session.completed",
        _session(session_id="cs_other", user_id=str(user.pk), **overrides),
    )
    assert not BillingTelemetry.objects.filter(service=BillingService.STRIPE_TOPUP).exists()
    assert balance_usd(user) == FREE_CREDITS_USD


@pytest.mark.parametrize("reference", ["not-a-user-id", None], ids=["malformed", "absent"])
def test_a_topup_without_a_usable_user_reference_finds_the_customer(stripe, reference):
    user = _make_user("topup-by-customer", stripe_customer_id="cus_lonely")
    status = _deliver(
        stripe,
        "checkout.session.completed",
        _session(session_id="cs_cust", amount_total=500, user_id=reference, customer="cus_lonely"),
    )
    assert status < 300
    assert balance_usd(user) == FREE_CREDITS_USD + Decimal("5")


def test_an_invoice_without_a_subscription_does_not_activate_pro(stripe):
    user = _make_user("topup-invoice-guard", projects_limit=5, stripe_customer_id="cus_test")
    Subscription.objects.create(user=user, status=SubscriptionStatus.INCOMPLETE)

    _deliver(
        stripe,
        "invoice.paid",
        {
            "id": "in_topup",
            "object": "invoice",
            "customer": "cus_test",
            "amount_paid": 2500,
            "lines": {"data": [{"period": {"start": 1861920000, "end": 1893456000}}]},
        },
    )

    user.refresh_from_db()
    assert user.projects_limit == 5
    assert user.subscription.status == SubscriptionStatus.INCOMPLETE
    assert balance_usd(user) == FREE_CREDITS_USD
