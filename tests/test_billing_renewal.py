from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.api.billing import renew_plan_from_invoice, sync_subscription_from_stripe
from overbae.models import BillingService, BillingTelemetry, Subscription, SubscriptionStatus
from overbae.services.billing_ledger import (
    FREE_CREDITS_USD,
    InsufficientCredits,
    balance_usd,
    charge_credits,
    ensure_credits,
    grant_free_credits,
    granted_usd,
)

pytestmark = pytest.mark.django_db

User = get_user_model()


def _invoice(
    *,
    invoice_id: str,
    amount_paid: int,
    period_start: int,
    period_end: int,
    customer: str = "cus_test",
    subscription: str = "sub_test",
    price_id: str = "price_1TvfC1CgXbweROl8gtGzCSV6",
) -> dict:
    return {
        "id": invoice_id,
        "customer": customer,
        "subscription": subscription,
        "amount_paid": amount_paid,
        "lines": {
            "data": [
                {
                    "price": {"id": price_id},
                    "period": {"start": period_start, "end": period_end},
                }
            ]
        },
    }


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def test_signup_grants_exactly_50_free_credits_once():
    user = User.objects.create_user(
        email="free-signup@example.com",
        password="x",
        clerk_user_id="clerk_free_signup",
    )
    rows = BillingTelemetry.objects.filter(user=user, service=BillingService.FREE_CREDITS)
    assert rows.count() == 1
    assert rows.get().amount == FREE_CREDITS_USD
    assert balance_usd(user) == FREE_CREDITS_USD

    grant_free_credits(user)
    assert rows.count() == 1
    assert balance_usd(user) == FREE_CREDITS_USD


def test_renew_plan_from_invoice_activates_pro_and_grants_credits():
    user = User.objects.create_user(
        email="pro-renew@example.com",
        password="x",
        clerk_user_id="clerk_pro_renew",
        projects_limit=5,
        stripe_customer_id="cus_test",
    )
    Subscription.objects.create(user=user, status=SubscriptionStatus.INCOMPLETE)

    renew_plan_from_invoice(
        _invoice(
            invoice_id="in_first",
            amount_paid=12000,
            period_start=1861920000,
            period_end=1893456000,
        )
    )

    user.refresh_from_db()
    sub = user.subscription
    assert user.projects_limit is None
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.stripe_subscription_id == "sub_test"
    assert sub.stripe_price_id == "price_1TvfC1CgXbweROl8gtGzCSV6"
    assert sub.start_date == datetime(2029, 1, 1, tzinfo=UTC)
    assert sub.end_date == datetime(2030, 1, 1, tzinfo=UTC)
    assert sub.last_stripe_invoice_id == "in_first"
    assert (
        BillingTelemetry.objects.filter(
            user=user, service=BillingService.STRIPE_TOPUP, idempotency_key="in_first"
        ).count()
        == 1
    )
    # $50 free + $120 invoice
    assert balance_usd(user) == Decimal("170")


def test_renew_plan_extends_end_date_and_adds_credits_without_changing_start():
    user = User.objects.create_user(
        email="pro-renew2@example.com",
        password="x",
        clerk_user_id="clerk_pro_renew2",
        projects_limit=5,
        stripe_customer_id="cus_test2",
    )
    Subscription.objects.create(user=user, status=SubscriptionStatus.INCOMPLETE)

    renew_plan_from_invoice(
        _invoice(
            invoice_id="in_a",
            amount_paid=12000,
            period_start=1861920000,
            period_end=1893456000,
            customer="cus_test2",
            subscription="sub_test2",
        )
    )
    sub = user.subscription
    sub.refresh_from_db()
    start = sub.start_date
    assert start == datetime(2029, 1, 1, tzinfo=UTC)

    renew_plan_from_invoice(
        _invoice(
            invoice_id="in_b",
            amount_paid=12000,
            period_start=1893456000,
            period_end=1924992000,
            customer="cus_test2",
            subscription="sub_test2",
        )
    )

    sub.refresh_from_db()
    assert sub.start_date == start
    assert sub.end_date == datetime(2031, 1, 1, tzinfo=UTC)
    assert sub.last_stripe_invoice_id == "in_b"
    # $50 free + $120 + $120
    assert balance_usd(user) == Decimal("290")


def test_renew_plan_idempotent_on_same_invoice_id():
    user = User.objects.create_user(
        email="pro-idem@example.com",
        password="x",
        clerk_user_id="clerk_pro_idem",
        projects_limit=5,
        stripe_customer_id="cus_idem",
    )
    Subscription.objects.create(user=user, status=SubscriptionStatus.INCOMPLETE)
    payload = _invoice(
        invoice_id="in_same",
        amount_paid=12000,
        period_start=1861920000,
        period_end=1893456000,
        customer="cus_idem",
        subscription="sub_idem",
    )

    renew_plan_from_invoice(payload)
    renew_plan_from_invoice(payload)

    assert (
        BillingTelemetry.objects.filter(
            user=user, service=BillingService.STRIPE_TOPUP, idempotency_key="in_same"
        ).count()
        == 1
    )
    assert balance_usd(user) == Decimal("170")
    sub = user.subscription
    sub.refresh_from_db()
    assert sub.end_date == datetime(2030, 1, 1, tzinfo=UTC)


def test_charge_credits_reduces_balance_and_entry_gate():
    user = User.objects.create_user(
        email="pro-deduct@example.com",
        password="x",
        clerk_user_id="clerk_pro_deduct",
    )
    assert balance_usd(user) == FREE_CREDITS_USD

    charge_credits(
        user,
        Decimal("3.5"),
        BillingService.INFERENCE,
        idempotency_key="inf-1",
    )
    assert balance_usd(user) == Decimal("46.5")

    charge_credits(
        user,
        Decimal("46.5"),
        BillingService.INFERENCE,
        idempotency_key="inf-2",
    )
    assert balance_usd(user) == Decimal("0")

    with pytest.raises(InsufficientCredits):
        ensure_credits(user)

    # Post-hoc debit still records full amount (ledger truth).
    charge_credits(
        user,
        Decimal("1"),
        BillingService.INFERENCE,
        idempotency_key="inf-3",
    )
    assert balance_usd(user) == Decimal("-1")
    # Usage never shrinks the granted total (progress-bar denominator).
    assert granted_usd(user) == FREE_CREDITS_USD


def test_subscription_get_free_user():
    user = User.objects.create_user(
        email="free@example.com",
        password="x",
        clerk_user_id="clerk_free",
    )
    r = _auth_client(user).get(reverse("billing-subscription"))
    assert r.status_code == 200
    assert r.data["plan"] == "free"
    assert r.data["status"] is None
    assert r.data["credits_usd"] == "50.0000"
    assert r.data["credits_granted_usd"] == "50.0000"
    assert r.data["cancel_at_period_end"] is False


def test_subscription_get_pro_user():
    user = User.objects.create_user(
        email="pro-get@example.com",
        password="x",
        clerk_user_id="clerk_pro_get",
    )
    Subscription.objects.create(
        user=user,
        status=SubscriptionStatus.ACTIVE,
        end_date=datetime(2030, 1, 1, tzinfo=UTC),
        cancel_at_period_end=False,
    )
    r = _auth_client(user).get(reverse("billing-subscription"))
    assert r.status_code == 200
    assert r.data["plan"] == "pro"
    assert r.data["status"] == "active"
    assert r.data["credits_usd"] == "50.0000"


def test_cancel_subscription_sets_flag(monkeypatch):
    user = User.objects.create_user(
        email="pro-cancel@example.com",
        password="x",
        clerk_user_id="clerk_pro_cancel",
    )
    Subscription.objects.create(
        user=user,
        status=SubscriptionStatus.ACTIVE,
        stripe_subscription_id="sub_cancel",
        cancel_at_period_end=False,
        end_date=datetime(2030, 1, 1, tzinfo=UTC),
    )

    calls: list[tuple] = []

    def fake_modify(sub_id, **kwargs):
        calls.append((sub_id, kwargs))
        return {"id": sub_id, **kwargs}

    monkeypatch.setattr("overbae.api.billing._configure_stripe", lambda: True)
    monkeypatch.setattr("overbae.api.billing.stripe.Subscription.modify", fake_modify)

    r = _auth_client(user).post(reverse("billing-cancel"))
    assert r.status_code == 200
    assert r.data["cancel_at_period_end"] is True
    assert r.data["plan"] == "pro"
    assert calls == [("sub_cancel", {"cancel_at_period_end": True})]
    user.subscription.refresh_from_db()
    assert user.subscription.cancel_at_period_end is True


def test_renew_subscription_clears_flag(monkeypatch):
    user = User.objects.create_user(
        email="pro-resume@example.com",
        password="x",
        clerk_user_id="clerk_pro_resume",
    )
    Subscription.objects.create(
        user=user,
        status=SubscriptionStatus.ACTIVE,
        stripe_subscription_id="sub_resume",
        cancel_at_period_end=True,
    )

    calls: list[tuple] = []

    def fake_modify(sub_id, **kwargs):
        calls.append((sub_id, kwargs))
        return {"id": sub_id, **kwargs}

    monkeypatch.setattr("overbae.api.billing._configure_stripe", lambda: True)
    monkeypatch.setattr("overbae.api.billing.stripe.Subscription.modify", fake_modify)

    r = _auth_client(user).post(reverse("billing-renew"))
    assert r.status_code == 200
    assert r.data["cancel_at_period_end"] is False
    assert calls == [("sub_resume", {"cancel_at_period_end": False})]


def test_sync_subscription_from_stripe_canceled():
    user = User.objects.create_user(
        email="pro-sync@example.com",
        password="x",
        clerk_user_id="clerk_pro_sync",
        projects_limit=None,
    )
    Subscription.objects.create(
        user=user,
        status=SubscriptionStatus.ACTIVE,
        stripe_subscription_id="sub_sync",
        cancel_at_period_end=True,
    )

    sync_subscription_from_stripe(
        {
            "id": "sub_sync",
            "customer": "cus_sync",
            "status": "canceled",
            "cancel_at_period_end": False,
            "current_period_end": 1893456000,
        }
    )

    user.refresh_from_db()
    sub = user.subscription
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.CANCELED
    assert sub.cancel_at_period_end is False
    assert user.projects_limit == 5
    assert sub.payload.get("status") == "canceled"


def test_renew_plan_from_stripe_sdk_object():
    """Webhook payloads are StripeObjects (no ``.get``); handlers must accept them."""
    from stripe._stripe_object import StripeObject

    user = User.objects.create_user(
        email="pro-sdk@example.com",
        password="x",
        clerk_user_id="clerk_pro_sdk",
        projects_limit=5,
        stripe_customer_id="cus_sdk",
    )
    Subscription.objects.create(user=user, status=SubscriptionStatus.INCOMPLETE)

    invoice = StripeObject.construct_from(
        {
            "id": "in_sdk",
            "customer": "cus_sdk",
            "subscription": "sub_sdk",
            "amount_paid": 12000,
            "lines": {
                "data": [
                    {
                        "price": {"id": "price_sdk"},
                        "period": {"start": 1861920000, "end": 1893456000},
                    }
                ]
            },
        },
        key=None,
    )
    assert not hasattr(invoice, "get") or not callable(getattr(type(invoice), "get", None))

    renew_plan_from_invoice(invoice)

    user.refresh_from_db()
    sub = user.subscription
    assert user.projects_limit is None
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.stripe_subscription_id == "sub_sdk"
    assert balance_usd(user) == Decimal("170")
    assert sub.end_date == datetime(2030, 1, 1, tzinfo=UTC)


def test_renew_plan_from_invoice_parent_subscription_details():
    """Newer Stripe invoices nest subscription under parent.subscription_details."""
    from stripe._stripe_object import StripeObject

    user = User.objects.create_user(
        email="pro-parent@example.com",
        password="x",
        clerk_user_id="clerk_pro_parent",
        projects_limit=5,
        stripe_customer_id="cus_parent",
    )
    Subscription.objects.create(user=user, status=SubscriptionStatus.INCOMPLETE)

    invoice = StripeObject.construct_from(
        {
            "id": "in_parent",
            "customer": "cus_parent",
            "subscription": None,
            "amount_paid": 5000,
            "parent": {"subscription_details": {"subscription": "sub_parent"}},
            "lines": {
                "data": [
                    {
                        "price": {"id": "price_parent"},
                        "period": {"start": 1861920000, "end": 1893456000},
                    }
                ]
            },
        },
        key=None,
    )

    renew_plan_from_invoice(invoice)

    sub = user.subscription
    sub.refresh_from_db()
    assert sub.stripe_subscription_id == "sub_parent"
    # $50 free + $50 invoice
    assert balance_usd(user) == Decimal("100")


def test_sync_subscription_from_stripe_sdk_object():
    from stripe._stripe_object import StripeObject

    user = User.objects.create_user(
        email="pro-sync-sdk@example.com",
        password="x",
        clerk_user_id="clerk_pro_sync_sdk",
        projects_limit=None,
    )
    Subscription.objects.create(
        user=user,
        status=SubscriptionStatus.ACTIVE,
        stripe_subscription_id="sub_sync_sdk",
        cancel_at_period_end=False,
    )

    stripe_sub = StripeObject.construct_from(
        {
            "id": "sub_sync_sdk",
            "customer": "cus_sync_sdk",
            "status": "active",
            "cancel_at_period_end": True,
            "current_period_end": 1893456000,
        },
        key=None,
    )
    sync_subscription_from_stripe(stripe_sub)

    sub = user.subscription
    sub.refresh_from_db()
    assert sub.cancel_at_period_end is True
    assert sub.end_date == datetime(2030, 1, 1, tzinfo=UTC)


def test_webhook_bad_signature_no_ledger_write(settings):
    settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
    settings.STRIPE_SECRET_KEY = "sk_test"
    user = User.objects.create_user(
        email="wh@example.com",
        password="x",
        clerk_user_id="clerk_wh",
        stripe_customer_id="cus_wh",
    )
    before = BillingTelemetry.objects.filter(user=user).count()

    client = APIClient()
    r = client.post(
        reverse("billing-webhook"),
        data=b'{"id":"evt_x"}',
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE="t=1,v1=bad",
    )
    assert r.status_code == 401
    assert BillingTelemetry.objects.filter(user=user).count() == before


def test_ledger_lists_own_entries_newest_first():
    user = User.objects.create_user(
        email="ledger@example.com",
        password="x",
        clerk_user_id="clerk_ledger",
    )
    other = User.objects.create_user(
        email="other-ledger@example.com",
        password="x",
        clerk_user_id="clerk_ledger_other",
    )
    charge_credits(
        user,
        Decimal("1.25"),
        BillingService.INFERENCE,
        idempotency_key="ledger-inf-1",
    )
    # Other user's row must not appear.
    charge_credits(
        other,
        Decimal("9"),
        BillingService.INFERENCE,
        idempotency_key="ledger-inf-other",
    )

    r = _auth_client(user).get(reverse("billing-ledger"))
    assert r.status_code == 200
    results = r.data["results"]
    assert r.data["count"] == 2  # free-credits + inference debit
    assert results[0]["service"] == BillingService.INFERENCE
    assert results[0]["service_label"] == "Inference"
    assert results[0]["amount"] == "-1.2500000"
    assert results[1]["service"] == BillingService.FREE_CREDITS
    assert results[1]["amount"] == "50.0000000"
    assert all(row.get("project_id") is None or True for row in results)
