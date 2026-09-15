from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.api.credit_gate import require_credits
from overbae.models import BillingService, BillingTelemetry
from overbae.services.billing_ledger import charge_credits, grant_free_credits, spent_usd
from overbae.services.billing_provider import get_billing
from overbae.services.plan_limits import effective_projects_limit, require_plan_quota

pytestmark = pytest.mark.django_db

User = get_user_model()


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


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
    assert grant_free_credits(user) is None
    assert not BillingTelemetry.objects.filter(user=user).exists()


def test_uncapped_require_credits_does_not_raise(uncapped):
    user = _make_user("uncapped-gate")
    require_credits(user)


def test_uncapped_plan_quota_is_unlimited(uncapped):
    user = _make_user("uncapped-quota")
    require_plan_quota(user, "training_jobs")
    assert effective_projects_limit(user) is None


def test_uncapped_charges_still_meter(uncapped):
    user = _make_user("uncapped-spend")
    charge_credits(
        user,
        Decimal("1.25"),
        BillingService.INFERENCE,
        idempotency_key="inf-uncapped",
    )
    assert spent_usd(user) == Decimal("1.25")
    assert BillingTelemetry.objects.filter(user=user).count() == 1


def test_uncapped_me_and_spend_routes(uncapped):
    user = _make_user("uncapped-api")
    charge_credits(
        user,
        Decimal("2"),
        BillingService.INFERENCE,
        idempotency_key="inf-api",
    )
    client = _auth_client(user)

    me = client.get(reverse("user-me"))
    assert me.status_code == 200
    assert me.data["billing_enabled"] is False

    sub = client.get(reverse("billing-subscription"))
    assert sub.status_code == 200
    assert sub.data["billing_enabled"] is False
    assert sub.data["credits_spent_usd"] == "2.0000"
    assert sub.data["usage"]["training_jobs"]["limit"] is None

    ledger = client.get(reverse("billing-ledger"))
    assert ledger.status_code == 200
    assert ledger.data["count"] == 1

    checkout = client.post(reverse("billing-checkout"))
    assert checkout.status_code == 404

    topup = client.post(reverse("billing-topup"), {"amount_usd": 10}, format="json")
    assert topup.status_code == 404


def test_commercial_me_enables_billing():
    user = _make_user("capped-api")
    r = _auth_client(user).get(reverse("user-me"))
    assert r.status_code == 200
    assert r.data["billing_enabled"] is True
    assert BillingTelemetry.objects.filter(user=user).exists()
