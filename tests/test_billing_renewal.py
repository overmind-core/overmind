from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from factories import auth_client
from rest_framework.test import APIClient

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


def _deliver(stripe, event_type: str, obj: dict) -> None:
    payload, signature = stripe.signed(
        {"id": f"evt_{obj['id']}", "type": event_type, "data": {"object": obj}}
    )
    response = APIClient().post(
        reverse("billing-webhook"),
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=signature,
    )
    assert response.status_code < 300, response.data


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
        "object": "invoice",
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


def test_renew_plan_from_invoice_activates_pro_and_grants_credits(stripe_api):
    user = User.objects.create_user(
        email="pro-renew@example.com",
        password="x",
        clerk_user_id="clerk_pro_renew",
        projects_limit=5,
        stripe_customer_id="cus_test",
    )
    Subscription.objects.create(user=user, status=SubscriptionStatus.INCOMPLETE)

    _deliver(
        stripe_api,
        "invoice.paid",
        _invoice(
            invoice_id="in_first",
            amount_paid=12000,
            period_start=1861920000,
            period_end=1893456000,
        ),
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


def test_renew_plan_extends_end_date_and_adds_credits_without_changing_start(stripe_api):
    user = User.objects.create_user(
        email="pro-renew2@example.com",
        password="x",
        clerk_user_id="clerk_pro_renew2",
        projects_limit=5,
        stripe_customer_id="cus_test2",
    )
    Subscription.objects.create(user=user, status=SubscriptionStatus.INCOMPLETE)

    _deliver(
        stripe_api,
        "invoice.paid",
        _invoice(
            invoice_id="in_a",
            amount_paid=12000,
            period_start=1861920000,
            period_end=1893456000,
            customer="cus_test2",
            subscription="sub_test2",
        ),
    )
    sub = user.subscription
    sub.refresh_from_db()
    start = sub.start_date
    assert start == datetime(2029, 1, 1, tzinfo=UTC)

    _deliver(
        stripe_api,
        "invoice.paid",
        _invoice(
            invoice_id="in_b",
            amount_paid=12000,
            period_start=1893456000,
            period_end=1924992000,
            customer="cus_test2",
            subscription="sub_test2",
        ),
    )

    sub.refresh_from_db()
    assert sub.start_date == start
    assert sub.end_date == datetime(2031, 1, 1, tzinfo=UTC)
    assert sub.last_stripe_invoice_id == "in_b"
    # $50 free + $120 + $120
    assert balance_usd(user) == Decimal("290")


def test_renew_plan_idempotent_on_same_invoice_id(stripe_api):
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

    _deliver(stripe_api, "invoice.paid", payload)
    _deliver(stripe_api, "invoice.paid", payload)

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
    r = auth_client(user).get(reverse("billing-subscription"))
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
    r = auth_client(user).get(reverse("billing-subscription"))
    assert r.status_code == 200
    assert r.data["plan"] == "pro"
    assert r.data["status"] == "active"
    assert r.data["credits_usd"] == "50.0000"


@pytest.mark.parametrize(
    ("endpoint", "before", "after"),
    [("billing-cancel", False, True), ("billing-renew", True, False)],
)
def test_cancelling_and_resuming_change_the_stripe_subscription(
    stripe_api, endpoint, before, after
):
    user = User.objects.create_user(
        email=f"{endpoint}@example.com", password="x", clerk_user_id=f"clerk_{endpoint}"
    )
    remote = stripe_api.subscription(cancel_at_period_end=before)
    Subscription.objects.create(
        user=user,
        status=SubscriptionStatus.ACTIVE,
        stripe_subscription_id=remote["id"],
        cancel_at_period_end=before,
        end_date=datetime(2030, 1, 1, tzinfo=UTC),
    )

    r = auth_client(user).post(reverse(endpoint))

    assert r.status_code == 200
    assert r.data["cancel_at_period_end"] is after
    assert r.data["plan"] == "pro"
    assert remote["cancel_at_period_end"] is after
    user.subscription.refresh_from_db()
    assert user.subscription.cancel_at_period_end is after


def test_a_stripe_outage_leaves_the_subscription_unchanged(stripe_api):
    user = User.objects.create_user(
        email="cancel-outage@example.com", password="x", clerk_user_id="clerk_cancel_outage"
    )
    remote = stripe_api.subscription()
    Subscription.objects.create(
        user=user, status=SubscriptionStatus.ACTIVE, stripe_subscription_id=remote["id"]
    )
    stripe_api.down = True

    r = auth_client(user).post(reverse("billing-cancel"))

    assert r.status_code >= 500
    user.subscription.refresh_from_db()
    assert user.subscription.cancel_at_period_end is False


@pytest.mark.parametrize(
    ("event_type", "remote", "status", "cancel_at_period_end", "projects_limit"),
    [
        ("customer.subscription.deleted", "canceled", SubscriptionStatus.CANCELED, False, 5),
        ("customer.subscription.updated", "active", SubscriptionStatus.ACTIVE, True, None),
    ],
)
def test_subscription_events_sync_the_plan(
    stripe_api, event_type, remote, status, cancel_at_period_end, projects_limit
):
    user = User.objects.create_user(
        email=f"sync-{remote}@example.com",
        password="x",
        clerk_user_id=f"clerk_sync_{remote}",
        projects_limit=None,
    )
    Subscription.objects.create(
        user=user,
        status=SubscriptionStatus.ACTIVE,
        stripe_subscription_id="sub_sync",
        cancel_at_period_end=not cancel_at_period_end,
    )

    _deliver(
        stripe_api,
        event_type,
        {
            "id": "sub_sync",
            "object": "subscription",
            "customer": "cus_sync",
            "status": remote,
            "cancel_at_period_end": cancel_at_period_end,
            "current_period_end": 1893456000,
        },
    )

    user.refresh_from_db()
    sub = user.subscription
    sub.refresh_from_db()
    assert sub.status == status
    assert sub.cancel_at_period_end is cancel_at_period_end
    assert sub.end_date == datetime(2030, 1, 1, tzinfo=UTC)
    assert user.projects_limit == projects_limit


def test_a_newer_invoice_names_its_subscription_under_parent_details(stripe_api):
    user = User.objects.create_user(
        email="pro-parent@example.com",
        password="x",
        clerk_user_id="clerk_pro_parent",
        projects_limit=5,
        stripe_customer_id="cus_parent",
    )
    Subscription.objects.create(user=user, status=SubscriptionStatus.INCOMPLETE)

    _deliver(
        stripe_api,
        "invoice.paid",
        {
            "id": "in_parent",
            "object": "invoice",
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
    )

    sub = user.subscription
    sub.refresh_from_db()
    assert sub.stripe_subscription_id == "sub_parent"
    assert balance_usd(user) == Decimal("100")


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

    r = auth_client(user).get(reverse("billing-ledger"))
    assert r.status_code == 200
    results = r.data["results"]
    assert r.data["count"] == 2  # free-credits + inference debit
    assert results[0]["service"] == BillingService.INFERENCE
    assert results[0]["service_label"] == "Inference"
    assert results[0]["amount"] == "-1.2500000"
    assert results[1]["service"] == BillingService.FREE_CREDITS
    assert results[1]["amount"] == "50.0000000"
