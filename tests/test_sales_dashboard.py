from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from factories import (
    api_key_client,
    auth_client,
    make_capability,
    make_project,
    make_span,
    make_user,
)
from rest_framework.test import APIClient

from overbae.models import (
    BillingService,
    BillingTelemetry,
    Dataset,
    DeployedModel,
    EvalRun,
    FinetuningJob,
    InferenceCall,
    OptimizerExperiment,
    Subscription,
    SubscriptionStatus,
    User,
)
from overbae.services.billing_ledger import FREE_CREDITS_USD

pytestmark = pytest.mark.django_db

TOKEN = "sales-test-token"


@pytest.fixture(autouse=True)
def _token(settings):
    settings.SALES_DASHBOARD_TOKEN = TOKEN


def sales_client(token: str = TOKEN) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.fixture
def accounts():
    guest = make_user(email="guest@example.com", is_guest=True)
    guest_project = make_project(member=guest)
    make_span(guest_project, trace_id="a" * 32)
    BillingTelemetry.objects.create(
        user=guest, amount=Decimal("-9"), service=BillingService.INFERENCE
    )

    active = make_user(email="active@example.com")
    project = make_project(member=active)
    make_span(project, trace_id="b" * 32)
    make_span(project, trace_id="b" * 32, parent_span_id="c" * 16)
    capability = make_capability(project)
    dataset = Dataset.objects.create(project=project, name="Train", intent="train")
    EvalRun.objects.create(project=project, name="Eval", status=EvalRun.Status.COMPLETED)
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="Qwen/Qwen3-4B",
        status=FinetuningJob.Status.SUCCEEDED,
    )
    deployed = DeployedModel.objects.create(
        project=project,
        finetuning_job=job,
        model_id="ft-qwen-1",
        status=DeployedModel.Status.READY,
        deployed_at=timezone.now(),
    )
    capability.active_model = deployed
    capability.first_application_request_at = timezone.now()
    capability.save(update_fields=["active_model", "first_application_request_at"])
    BillingTelemetry.objects.create(
        user=active, project=project, amount=Decimal("-1.5"), service=BillingService.INFERENCE
    )
    BillingTelemetry.objects.create(
        user=active, project=project, amount=Decimal("-0.5"), service=BillingService.EVALUATION
    )

    paying = make_user(email="paying@example.com")
    make_project(member=paying)
    Subscription.objects.create(user=paying, status=SubscriptionStatus.ACTIVE)
    BillingTelemetry.objects.create(
        user=paying, amount=Decimal("25"), service=BillingService.STRIPE_TOPUP
    )

    old = make_user(email="old@example.com")
    User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=60))
    return {"active": active, "paying": paying, "old": old}


@pytest.mark.parametrize("path", ["overview", "funnel", "accounts", "accounts/{id}"])
def test_rejects_anything_but_the_configured_token(settings, path):
    user = make_user()
    url = f"/api/internal/sales/{path.format(id=user.id)}/"

    assert APIClient().get(url).status_code == 403
    assert sales_client("wrong").get(url).status_code == 403
    assert auth_client(user).get(url).status_code == 403
    assert api_key_client(user).get(url).status_code == 403
    assert sales_client().get(url).status_code == 200

    settings.SALES_DASHBOARD_TOKEN = ""
    assert sales_client("").get(url).status_code == 403


def test_overview_counts_customers_only(accounts):
    body = sales_client().get("/api/internal/sales/overview/").json()

    assert body["accounts_total"] == 3
    assert sum(point["value"] for point in body["signups"]) == 2
    assert body["active_accounts_7d"] == 1
    assert body["subscriptions"] == {"active": 1}
    assert body["paying_accounts"] == 1
    assert body["free_accounts"] == 1
    assert Decimal(body["topups_usd"][0]["value"]) == Decimal("25")
    assert {k: Decimal(v) for k, v in body["spend_by_service"].items()} == {
        "inference": Decimal("1.5"),
        "evaluation": Decimal("0.5"),
    }
    assert sum(point["value"] for point in body["spans"]) == 2
    assert sum(point["value"] for point in body["traces"]) == 1
    assert body["training_jobs"] == {"succeeded": 1}
    assert body["top_base_models"] == [{"base_model": "Qwen/Qwen3-4B", "jobs": 1}]
    assert body["live_deployments"] == 1


def test_funnel_follows_window_signups(accounts):
    body = sales_client().get("/api/internal/sales/funnel/").json()

    assert body["steps"] == [
        {"step": "signed_up", "accounts": 2},
        {"step": "created_project", "accounts": 2},
        {"step": "sent_first_span", "accounts": 1},
        {"step": "synced_capabilities", "accounts": 1},
        {"step": "ran_evaluation", "accounts": 1},
        {"step": "started_training", "accounts": 1},
        {"step": "deployed_model", "accounts": 1},
        {"step": "called_alias", "accounts": 1},
    ]
    assert body["median_hours_to_first_span"] is not None

    widened = sales_client().get("/api/internal/sales/funnel/?days=90").json()
    assert widened["steps"][0] == {"step": "signed_up", "accounts": 3}


def test_accounts_table_pages_and_orders(accounts):
    first = sales_client().get("/api/internal/sales/accounts/?ordering=-spend_usd&page_size=1")
    body = first.json()

    assert body["count"] == 3
    assert body["next"]
    row = body["results"][0]
    assert row["email"] == "active@example.com"
    assert Decimal(row["spend_usd"]) == Decimal("2")
    assert row["projects"] == 1
    assert row["seats"] == 1
    assert row["spans"] == 2
    assert row["training_jobs"] == 1
    assert row["live_deployments"] == 1
    assert row["stage"] == "called_alias"

    by_topups = sales_client().get("/api/internal/sales/accounts/?ordering=-topups_usd").json()
    paying = by_topups["results"][0]
    assert paying["email"] == "paying@example.com"
    assert paying["subscription_status"] == "active"
    assert Decimal(paying["balance_usd"]) == FREE_CREDITS_USD + Decimal("25")
    assert paying["stage"] == "created_project"

    assert sales_client().get("/api/internal/sales/overview/?days=0").status_code == 400


def test_account_activity_breaks_down_each_workflow(accounts):
    active = accounts["active"]
    project = active.project_memberships.get().project
    dataset = project.datasets.get()
    FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="Qwen/Qwen3-4B",
        status=FinetuningJob.Status.FAILED,
    )
    deployed = DeployedModel.objects.get(model_id="ft-qwen-1")
    InferenceCall.objects.create(deployed_model=deployed, project=project)
    InferenceCall.objects.create(deployed_model=deployed, project=project)
    Dataset.objects.create(project=project, name="Traces", source_kind="traces", intent="eval")
    OptimizerExperiment.objects.create(
        project=project, capability=project.capabilities.get(), dataset=dataset
    )

    body = sales_client().get(f"/api/internal/sales/accounts/{active.id}/").json()

    assert body["account"]["email"] == "active@example.com"
    training = body["training"]
    assert training["totals"] == {
        "started": 2,
        "completed": 1,
        "failed": 1,
        "cancelled": 0,
        "in_progress": 0,
    }
    assert sum(sum(point["values"].values()) for point in training["daily"]) == 2
    assert {job["status"] for job in training["recent"]} == {"succeeded", "failed"}
    assert body["traces"]["total"] == 1
    assert sum(point["value"] for point in body["traces"]["daily"]) == 1
    assert body["inference"]["calls"] == 2
    assert body["inference"]["models"] == 1
    assert body["inference"]["by_model"] == {"ft-qwen-1": 2}
    assert body["datasets"]["total"] == 2
    assert body["datasets"]["by_source"] == {"file": 1, "traces": 1}
    assert body["datasets"]["by_intent"] == {"train": 1, "eval": 1}
    assert body["optimizer"]["by_status"] == {"scheduled": 1}
    assert body["evaluations"]["by_status"] == {"completed": 1}

    guest = User.objects.get(email="guest@example.com")
    assert sales_client().get(f"/api/internal/sales/accounts/{guest.id}/").status_code == 404


def test_responses_are_cached_for_an_hour(accounts):
    first = sales_client().get("/api/internal/sales/overview/").json()
    make_user()
    again = sales_client().get("/api/internal/sales/overview/").json()

    assert again["accounts_total"] == first["accounts_total"] == 3
    assert again["computed_at"] == first["computed_at"]
    computed = datetime.fromisoformat(first["computed_at"])
    assert datetime.fromisoformat(first["refreshes_at"]) - computed == timedelta(hours=1)

    other_window = sales_client().get("/api/internal/sales/overview/?days=7").json()
    assert other_window["accounts_total"] == 4

    page = sales_client().get("/api/internal/sales/accounts/").json()
    assert page["count"] == 4 and page["computed_at"]
