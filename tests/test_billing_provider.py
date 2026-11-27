from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from factories import auth_client

from overbae.models import BillingService, BillingTelemetry
from overbae.services.billing_ledger import charge_credits, grant_signup_credits
from overbae.services.billing_provider import get_billing

pytestmark = [pytest.mark.django_db, pytest.mark.covers("ledger")]

User = get_user_model()


def _make_user(slug: str) -> User:
    return User.objects.create_user(
        email=f"{slug}@example.com",
        password="x",
        clerk_user_id=f"clerk_{slug}",
    )


@pytest.fixture
def uncapped(_commercial_billing, settings):
    settings.STRIPE_SECRET_KEY = ""
    return settings


def test_uncapped_skips_signup_grant(uncapped):
    user = _make_user("uncapped-grant")
    assert not get_billing().enabled
    assert grant_signup_credits(user) is None
    assert not BillingTelemetry.objects.filter(user=user).exists()


def test_uncapped_me_and_spend_routes(uncapped):
    user = _make_user("uncapped-api")
    charge_credits(
        user,
        Decimal("2"),
        BillingService.INFERENCE,
        idempotency_key="inf-api",
    )
    client = auth_client(user)

    me = client.get(reverse("user-me"))
    assert me.status_code == 200
    assert me.data["billing_enabled"] is False

    account = client.get(reverse("billing-account"))
    assert account.status_code == 200
    assert account.data["billing_enabled"] is False
    assert account.data["credits_spent_usd"] == "2.0000"

    ledger = client.get(reverse("billing-ledger"))
    assert ledger.status_code == 200
    assert ledger.data["count"] == 1

    topup = client.post(reverse("billing-topup"), {"amount_usd": 10}, format="json")
    assert topup.status_code == 404

    confirm = client.post(reverse("billing-topup-confirm"), {"session_id": "cs_any"})
    assert confirm.status_code == 404


def test_commercial_me_enables_billing():
    user = _make_user("capped-api")
    r = auth_client(user).get(reverse("user-me"))
    assert r.status_code == 200
    assert r.data["billing_enabled"] is True
    assert BillingTelemetry.objects.filter(user=user).exists()
