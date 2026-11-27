from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from factories import auth_client, make_project, make_user

from overbae.models import BillingService, BillingTelemetry
from overbae.services.billing_ledger import (
    SIGNUP_CREDITS_USD,
    InsufficientCredits,
    balance_usd,
    charge_credits,
    ensure_credits,
    grant_signup_credits,
    granted_usd,
)

pytestmark = [pytest.mark.django_db, pytest.mark.covers("ledger")]

User = get_user_model()


def test_signup_grants_exactly_50_credits_once():
    user = User.objects.create_user(
        email="signup-grant@example.com",
        password="x",
        clerk_user_id="clerk_signup_grant",
    )
    rows = BillingTelemetry.objects.filter(user=user, service=BillingService.GRANT)
    assert rows.count() == 1
    assert rows.get().amount == SIGNUP_CREDITS_USD
    assert balance_usd(user) == SIGNUP_CREDITS_USD

    grant_signup_credits(user)
    assert rows.count() == 1
    assert balance_usd(user) == SIGNUP_CREDITS_USD


def test_charge_credits_reduces_balance_and_entry_gate():
    user = User.objects.create_user(
        email="deduct@example.com",
        password="x",
        clerk_user_id="clerk_deduct",
    )
    assert balance_usd(user) == SIGNUP_CREDITS_USD

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
    assert granted_usd(user) == SIGNUP_CREDITS_USD


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
    assert r.data["count"] == 2  # signup grant + inference debit
    assert results[0]["service"] == BillingService.INFERENCE
    assert results[0]["service_label"] == "Inference"
    assert results[0]["amount"] == "-1.2500000"
    assert results[1]["service"] == BillingService.GRANT
    assert results[1]["amount"] == "50.0000000"


@pytest.mark.parametrize("ordering", ["service", "-service", "project__name", "-project__name"])
def test_ledger_sorts_visible_labels_and_keeps_missing_projects_last(ordering):
    user = make_user()
    BillingTelemetry.objects.filter(user=user).delete()
    project = make_project(name="Alpha")
    first = BillingTelemetry.objects.create(
        user=user, project=project, amount=-1, service=BillingService.INFERENCE_FT_MODEL
    )
    second = BillingTelemetry.objects.create(user=user, amount=-2, service=BillingService.INFERENCE)
    response = auth_client(user).get(
        reverse("billing-ledger"), {"ordering": ordering, "page_size": 1}
    )
    assert response.status_code == 200, response.content
    expected = second if ordering == "-service" else first
    assert response.data["results"][0]["id"] == str(expected.id)
