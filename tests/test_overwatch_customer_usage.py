from datetime import UTC, datetime, timedelta

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from factories import make_project, make_user
from rest_framework.test import APIClient

from overbae.models import (
    BillingTelemetry,
    DeployedModel,
    InferenceCall,
    ProjectMembership,
    Subscription,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.covers("measured-facts")]
URL = "/api/internal/overwatch/agent/query/"


@pytest.fixture(autouse=True)
def _sandbox(settings):
    settings.SANDBOX_URL = "http://sandbox.test"
    settings.SANDBOX_TOKEN = "analytics-test"


@pytest.fixture(autouse=True)
def configured(settings, time_machine):
    settings.OVERWATCH_SERVICE_TOKEN = "usage-test-token"
    settings.SALES_DASHBOARD_TOKEN = "dashboard-test-token"
    cache.clear()
    time_machine.move_to(datetime(2026, 11, 9, 12, tzinfo=UTC), tick=False)


def query(arguments, tool="customer_activity"):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer usage-test-token")
    return client.post(URL, {"tool": tool, "arguments": arguments}, format="json")


@pytest.mark.parametrize("search", ["Chandra", "chandra PATEL", "example.com", "CHAN@EXAMPLE.COM"])
def test_name_full_name_domain_and_email_resolve_without_lifetime_report(search):
    make_user(email="chan@example.com", first_name="Chandra", last_name="Patel")
    make_user(email="guest@example.com", first_name="Chandra", is_guest=True)
    with CaptureQueriesContext(connection) as queries:
        response = query({"query": search, "days": 7})
    assert response.status_code == 200
    assert response.data["private"] is True
    assert "chan@example.com" in response.data["content"]
    assert "guest@example.com" not in response.data["content"]
    assert "0 successful" in response.data["content"]
    # Lookup plus a bounded report, not the dashboard's whole lifetime history.
    assert len([q for q in queries if q["sql"].startswith("SELECT")]) <= 18


def test_ambiguous_name_lists_bounded_candidates_and_never_picks_a_customer():
    for index in range(8):
        make_user(email=f"chandra{index}@example.com", first_name="Chandra")
    response = query({"query": "Chandra", "days": 7})
    assert response.status_code == 200
    text = response.data["content"]
    assert "Multiple customers" in text and "exact email" in text
    assert text.count("@example.com") == 5
    assert "successful" not in text


def test_exact_email_never_falls_back_to_another_customer():
    make_user(email="somebody@example.com")
    response = query({"email": "missing@example.com", "days": 7})
    assert response.status_code == 200
    assert "No matching customer" in response.data["content"]
    assert "somebody" not in response.data["content"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": " ", "days": 7},
        {"query": "x", "days": 7},
        {"query": "person", "email": "a@example.com", "days": 7},
        {"query": "person", "days": 7, "sql": "SELECT *"},
    ],
)
def test_lookup_arguments_remain_bounded(arguments):
    assert query(arguments).status_code == 400


def test_usage_counts_window_sources_tokens_and_models_without_duplicate_memberships():
    user = make_user(email="usage@example.com", first_name="Usage")
    project = make_project(member=user)
    ProjectMembership.objects.create(project=project, user=make_user())
    deployed = DeployedModel.objects.create(project=project, model_id="ft-test", status="ready")
    for source, outcome, prompt, completion in [
        ("application", "succeeded", 100, 20),
        ("playground", "succeeded", 40, 10),
        ("internal", "succeeded", 30, 5),
        ("unknown", "succeeded", 0, 0),
        ("application", "failed", 10, 0),
    ]:
        InferenceCall.objects.create(
            project=project,
            deployed_model=deployed,
            source=source,
            outcome=outcome,
            prompt_tokens=prompt,
            completion_tokens=completion,
        )
    older = InferenceCall.objects.create(
        project=project,
        deployed_model=deployed,
        source="application",
        prompt_tokens=900,
        completion_tokens=100,
    )
    InferenceCall.objects.filter(pk=older.pk).update(created_at=datetime(2026, 10, 30, tzinfo=UTC))
    response = query({"query": "Usage", "days": 7})
    assert response.status_code == 200
    text = response.data["content"]
    assert "4 successful" in text and "1 unsuccessful" in text
    assert "180 input / 35 output" in text
    assert "Application: 1 successful" in text
    assert "Playground: 1 successful" in text and "Internal: 1 successful" in text
    assert "Unknown source: 1 successful" in text
    assert "Previous 7 UTC days: 1 successful" in text
    assert "ft-test: 4 successful" in text
    assert "individual caller" in text
    assert "zero" in text.lower() and "unavailable" in text.lower()
    global_response = query({"days": 7}, tool="model_usage")
    assert global_response.status_code == 200
    assert global_response.data["private"] is True
    assert "4 successful" in global_response.data["content"]
    assert "usage@example.com" not in global_response.data["content"]


def test_usage_omits_guest_only_projects_and_keeps_zero_activity_explicit():
    guest_project = make_project(member=make_user(is_guest=True))
    deployed = DeployedModel.objects.create(project=guest_project, model_id="ft-guest")
    InferenceCall.objects.create(project=guest_project, deployed_model=deployed, prompt_tokens=999)
    response = query({"days": 7}, tool="model_usage")
    assert response.status_code == 200
    assert "0 successful" in response.data["content"]
    assert "ft-guest" not in response.data["content"]
    assert "999" not in response.data["content"]


def test_customer_snapshot_is_reused_briefly_and_not_across_utc_midnight(time_machine):
    make_user(email="fresh@example.com")
    first = query({"query": "fresh", "days": 7})
    assert first.status_code == 200
    time_machine.shift(timedelta(seconds=30))
    cached = query({"query": "fresh", "days": 7})
    assert cached.data["as_of"] == first.data["as_of"]
    assert cached.data["refreshes_at"] > cached.data["as_of"]
    time_machine.shift(timedelta(minutes=6))
    assert query({"query": "fresh", "days": 7}).data["as_of"] != first.data["as_of"]
    time_machine.move_to(datetime(2026, 11, 9, 23, 59, tzinfo=UTC), tick=False)
    previous_day = query({"query": "fresh", "days": 7})
    time_machine.shift(timedelta(minutes=2))
    today = query({"query": "fresh", "days": 7})
    assert today.data["window_start"] != previous_day.data["window_start"]


def test_customer_billing_remains_user_attributed_and_keeps_lifetime_totals(settings):
    settings.STRIPE_SECRET_KEY = ""
    user = make_user(email="billing@example.com")
    other = make_user()
    project = make_project(member=user)
    ProjectMembership.objects.create(project=project, user=other)
    Subscription.objects.create(user=user, status="active")
    BillingTelemetry.objects.create(
        user=user, project=project, amount="-2.50", service="inference-ft-model"
    )
    BillingTelemetry.objects.create(
        user=user, project=project, amount="20.00", service="stripe-topup"
    )
    old = BillingTelemetry.objects.create(
        user=user, project=project, amount="-5.00", service="inference-ft-model"
    )
    BillingTelemetry.objects.filter(pk=old.pk).update(timestamp=datetime(2026, 10, 1, tzinfo=UTC))
    BillingTelemetry.objects.create(
        user=other, project=project, amount="-99.00", service="inference-ft-model"
    )
    response = query({"email": user.email, "days": 7})
    assert response.status_code == 200
    text = response.data["content"]
    assert "$2.50 spent in window" in text
    assert "$7.50 lifetime spend" in text
    assert "$20.00 lifetime top-ups" in text
    assert "$12.50 current balance" in text
    assert "Subscription: active" in text
    assert "$99" not in text
