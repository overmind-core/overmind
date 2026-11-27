from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse
from factories import auth_client
from rest_framework.test import APIClient

from overbae.models import BillingService, BillingTelemetry
from overbae.services.billing_ledger import SIGNUP_CREDITS_USD, balance_usd
from overbae.services.credit_purchases import CREDIT_TOPUP_PURPOSE, WEBHOOK_EVENTS

pytestmark = [pytest.mark.django_db, pytest.mark.covers("ledger")]

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
    **extra,
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
        "currency": "usd",
        **extra,
    }


def test_topup_checkout_charges_a_fixed_credit_quantity(stripe, settings):
    settings.FRONTEND_URL = "https://console.example"
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
    assert session["success_url"] == (
        "https://console.example/settings?billing=topup&session_id={CHECKOUT_SESSION_ID}"
    )
    user.refresh_from_db()
    assert user.stripe_customer_id in stripe.customers


@pytest.mark.parametrize("amount", [0, -5, 1001])
def test_topup_rejects_out_of_range_amount(stripe, amount):
    user = _make_user(f"topup-range-{abs(amount)}")
    r = auth_client(user).post(reverse("billing-topup"), {"amount_usd": amount}, format="json")
    assert r.status_code == 400
    assert stripe.sessions == []


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
        user=user, service=BillingService.PURCHASE, idempotency_key="topup:cs_grant"
    )
    assert rows.get().amount == Decimal("25")
    assert balance_usd(user) == SIGNUP_CREDITS_USD + Decimal("25")


@pytest.mark.parametrize(
    ("currency", "conversion", "granted"),
    [
        ("inr", {"source_currency": "usd", "amount_total": 1000}, Decimal("10")),
        ("eur", None, Decimal("0")),
    ],
    ids=["adaptive pricing", "no usd total"],
)
def test_a_local_currency_payment_grants_its_usd_amount(stripe, currency, conversion, granted):
    user = _make_user(f"topup-{currency}", stripe_customer_id="cus_test")
    session = _session(
        session_id=f"cs_{currency}",
        amount_total=85000,
        user_id=str(user.pk),
        currency=currency,
        currency_conversion=conversion,
    )

    assert _deliver(stripe, "checkout.session.completed", session) < 300
    assert balance_usd(user) == SIGNUP_CREDITS_USD + granted


@pytest.mark.parametrize(
    "overrides",
    [{"purpose": None}, {"payment_status": "unpaid"}],
    ids=["another checkout", "unpaid"],
)
def test_a_session_that_is_not_a_paid_topup_grants_nothing(stripe, overrides):
    user = _make_user("topup-ignored", stripe_customer_id="cus_test")
    _deliver(
        stripe,
        "checkout.session.completed",
        _session(session_id="cs_other", user_id=str(user.pk), **overrides),
    )
    assert not BillingTelemetry.objects.filter(service=BillingService.PURCHASE).exists()
    assert balance_usd(user) == SIGNUP_CREDITS_USD


@pytest.mark.parametrize("reference", ["not-a-user-id", None], ids=["malformed", "absent"])
def test_a_topup_without_a_usable_user_reference_finds_the_customer(stripe, reference):
    user = _make_user("topup-by-customer", stripe_customer_id="cus_lonely")
    status = _deliver(
        stripe,
        "checkout.session.completed",
        _session(session_id="cs_cust", amount_total=500, user_id=reference, customer="cus_lonely"),
    )
    assert status < 300
    assert balance_usd(user) == SIGNUP_CREDITS_USD + Decimal("5")


def test_returning_from_a_paid_checkout_grants_once_without_a_webhook(stripe):
    user = _make_user("topup-return")
    client = auth_client(user)
    client.post(reverse("billing-topup"), {"amount_usd": 25}, format="json")
    [session] = stripe.sessions
    stripe.pay(session, amount_total=2500)

    first = client.post(reverse("billing-topup-confirm"), {"session_id": session["id"]})
    again = client.post(reverse("billing-topup-confirm"), {"session_id": session["id"]})
    late_webhook = _deliver(stripe, "checkout.session.completed", session)

    assert first.status_code == 200
    assert first.data["paid"] is True
    assert first.data["account"]["credits_usd"] == "75.0000"
    assert again.data["account"]["credits_usd"] == "75.0000"
    assert late_webhook < 300
    assert balance_usd(user) == SIGNUP_CREDITS_USD + Decimal("25")
    assert BillingTelemetry.objects.filter(user=user, service=BillingService.PURCHASE).count() == 1


def test_returning_before_payment_settles_grants_nothing(stripe):
    user = _make_user("topup-pending")
    client = auth_client(user)
    client.post(reverse("billing-topup"), {"amount_usd": 25}, format="json")
    [session] = stripe.sessions

    r = client.post(reverse("billing-topup-confirm"), {"session_id": session["id"]})

    assert r.status_code == 200
    assert r.data["paid"] is False
    assert balance_usd(user) == SIGNUP_CREDITS_USD


@pytest.mark.parametrize("session_id", ["theirs", "cs_test_missing"])
def test_confirming_a_session_the_caller_did_not_buy_is_not_found(stripe, session_id):
    buyer = _make_user("topup-buyer")
    other = _make_user("topup-other")
    auth_client(buyer).post(reverse("billing-topup"), {"amount_usd": 25}, format="json")
    [session] = stripe.sessions
    stripe.pay(session, amount_total=2500)
    target = session["id"] if session_id == "theirs" else session_id

    r = auth_client(other).post(reverse("billing-topup-confirm"), {"session_id": target})

    assert r.status_code == 404
    assert not BillingTelemetry.objects.filter(service=BillingService.PURCHASE).exists()


@pytest.mark.parametrize("session_id", ["cs_test_a/../../v1/customers", "pi_123", ""])
def test_confirming_a_malformed_session_id_never_reaches_stripe(stripe, session_id):
    stripe.down = True
    user = _make_user("topup-malformed")

    r = auth_client(user).post(reverse("billing-topup-confirm"), {"session_id": session_id})

    assert r.status_code == 400


def test_a_stripe_failure_on_return_does_not_echo_stripe_text(stripe):
    user = _make_user("topup-confirm-outage")
    stripe.down = True

    r = auth_client(user).post(reverse("billing-topup-confirm"), {"session_id": "cs_test_down"})

    assert r.status_code == 502
    assert "unavailable" not in r.data["detail"]


def test_the_webhook_acknowledges_events_it_does_not_handle(stripe):
    user = _make_user("topup-unhandled", stripe_customer_id="cus_test")
    invoice = {"id": "in_other", "object": "invoice", "customer": "cus_test", "amount_paid": 2500}

    assert _deliver(stripe, "invoice.paid", invoice) == 204
    assert balance_usd(user) == SIGNUP_CREDITS_USD


def test_a_badly_signed_webhook_writes_nothing(stripe):
    user = _make_user("topup-bad-signature", stripe_customer_id="cus_test")
    r = APIClient().post(
        reverse("billing-webhook"),
        data=b'{"id":"evt_x"}',
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE="t=1,v1=bad",
    )
    assert r.status_code == 401
    assert balance_usd(user) == SIGNUP_CREDITS_USD


def test_syncing_the_webhook_sets_exactly_the_purchase_events(stripe):
    url = "https://api.example/api/billing/webhook/"
    endpoint = stripe.webhook_endpoint(url, ["invoice.paid", "customer.subscription.updated"])
    stripe.webhook_endpoint("https://elsewhere.example/hook/", ["invoice.paid"])

    call_command("sync_stripe_webhook", url=url)

    assert endpoint["enabled_events"] == list(WEBHOOK_EVENTS)
    assert stripe.webhook_endpoints[1]["enabled_events"] == ["invoice.paid"]
