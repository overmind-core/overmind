from __future__ import annotations

from datetime import date, datetime, timedelta

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
    ProjectMembership,
    Subscription,
    SubscriptionStatus,
    User,
)
from overbae.services import overwatch_insights

pytestmark = pytest.mark.django_db

TOKEN = "overwatch-test-token"


@pytest.fixture(autouse=True)
def _token(settings):
    settings.SALES_DASHBOARD_TOKEN = TOKEN


def overwatch_client(token: str = TOKEN) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.fixture
def accounts():
    guest = make_user(email="guest@example.com", is_guest=True)
    guest_project = make_project(member=guest)
    make_span(guest_project, trace_id="a" * 32)

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
    paying = make_user(email="paying@example.com")

    old = make_user(email="old@example.com")
    User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=60))
    return {"active": active, "paying": paying, "old": old}


@pytest.mark.parametrize(
    "path", ["overview", "activation", "users", "users/{id}"]
)
def test_rejects_anything_but_the_configured_token(settings, path):
    user = make_user()
    url = f"/api/internal/overwatch/{path.format(id=user.id)}/"

    assert APIClient().get(url).status_code == 403
    assert overwatch_client("wrong").get(url).status_code == 403
    assert auth_client(user).get(url).status_code == 403
    assert api_key_client(user).get(url).status_code == 403
    assert overwatch_client().get(url).status_code == 200

    settings.SALES_DASHBOARD_TOKEN = ""
    assert overwatch_client("").get(url).status_code == 403


def test_overview_counts_customers_only(accounts):
    body = overwatch_client().get("/api/internal/overwatch/overview/").json()

    assert body["users_total"] == 3
    assert sum(point["value"] for point in body["signups"]) == 2
    assert body["trace_active_users_7d"] == 1
    assert sum(point["value"] for point in body["spans"]) == 2
    assert sum(point["value"] for point in body["traces"]) == 1
    assert body["training_jobs"] == {"succeeded": 1}
    assert body["top_base_models"] == [{"base_model": "Qwen/Qwen3-4B", "jobs": 1}]
    assert body["live_deployments"] == 1


def test_overview_retains_subscription_spend_and_payment_insights(accounts):
    paying = accounts["paying"]
    Subscription.objects.create(
        user=paying,
        status=SubscriptionStatus.ACTIVE,
        cancel_at_period_end=True,
    )
    BillingTelemetry.objects.create(
        user=paying,
        amount="-3.2500000",
        service=BillingService.INFERENCE,
    )
    BillingTelemetry.objects.create(
        user=paying,
        amount="5.0000000",
        service=BillingService.STRIPE_TOPUP,
    )

    body = overwatch_client().get("/api/internal/overwatch/overview/").json()

    assert body["subscriptions"] == {SubscriptionStatus.ACTIVE: 1}
    assert body["paying_users"] == 1
    assert body["cancelling_users"] == 1
    assert body["free_users"] == 1
    assert body["low_balance_users"] == 1
    assert body["spend_by_service"] == {BillingService.INFERENCE: "3.2500000"}
    assert sum(float(point["value"]) for point in body["topups_usd"]) == 5
    assert sum(float(point["value"]) for point in body["spend_usd"]) == 3.25


def test_user_activity_retains_lifetime_billing_and_window_history(accounts):
    paying = accounts["paying"]
    Subscription.objects.create(user=paying, status=SubscriptionStatus.PAST_DUE)
    BillingTelemetry.objects.create(
        user=paying,
        amount="-7.5000000",
        service=BillingService.FINETUNING_JOB,
    )
    BillingTelemetry.objects.create(
        user=paying,
        amount="10.0000000",
        service=BillingService.STRIPE_TOPUP,
    )

    body = overwatch_client().get(f"/api/internal/overwatch/users/{paying.id}/").json()

    assert body["user"]["subscription_status"] == SubscriptionStatus.PAST_DUE
    assert body["user"]["spend_usd"] == "7.5000000"
    assert body["user"]["topups_usd"] == "10.0000000"
    assert body["user"]["balance_usd"] == "2.5000000"
    assert body["billing"]["subscription_status"] == SubscriptionStatus.PAST_DUE
    assert body["billing"]["spend_usd"] == "7.5000000"
    assert body["billing"]["topups_usd"] == "10.0000000"
    assert body["billing"]["balance_usd"] == "2.5000000"
    assert sum(float(point["value"]) for point in body["billing"]["spend_daily"]) == 7.5
    assert sum(float(point["value"]) for point in body["billing"]["topups_daily"]) == 10


def test_posthog_usage_uses_captured_cli_and_mcp_event_properties(settings, monkeypatch):
    settings.POSTHOG_PERSONAL_API_KEY = "posthog-read-key"
    settings.POSTHOG_PROJECT_ID = "42"
    queries = []

    def query(hogql):
        queries.append(hogql)
        if "SELECT toDate(timestamp), event, count()" in hogql:
            return [[date(2026, 10, 25), "cli.completed", 4]]
        if "SELECT uniq(person_id)" in hogql:
            return [[3]]
        if "properties.$mcp_tool_name" in hogql:
            return [["list_projects", 5, 2]]
        if "properties.command_path" in hogql:
            return [["sync", 7, 3]]
        if "countIf(toBool(properties.$mcp_is_error))" in hogql:
            return [[5, 1]]
        if "properties.funnel_step" in hogql:
            return [["joined_project", 2], ["ran_evaluation", 1]]
        raise AssertionError(f"Unexpected PostHog query: {hogql}")

    monkeypatch.setattr(overwatch_insights, "_query", query)

    result = overwatch_insights.product_usage(14)

    assert result["available"] is True
    assert result["active_users"] == 3
    assert result["totals"] == {
        "cli_completions": 4,
        "mcp_tool_calls": 0,
        "training_results_available": 0,
        "training_checks_finished": 0,
    }
    assert result["funnel_steps"] == {"joined_project": 2, "ran_evaluation": 1}
    assert result["top_mcp_tools"] == [{"name": "list_projects", "calls": 5, "users": 2}]
    assert result["top_cli_commands"] == [{"name": "sync", "completions": 7, "users": 3}]
    assert result["mcp_tool_errors"] == 1
    assert result["mcp_tool_error_rate"] == 0.2
    assert all("timestamp >= now() - INTERVAL 14 DAY" in query for query in queries)


def test_funnel_follows_window_signups(accounts):
    body = overwatch_client().get("/api/internal/overwatch/activation/").json()

    assert body["steps"] == [
        {"step": "signed_up", "users": 2},
        {"step": "joined_project", "users": 2},
        {"step": "sent_first_span", "users": 1},
        {"step": "synced_capabilities", "users": 1},
        {"step": "ran_evaluation", "users": 1},
        {"step": "started_training", "users": 1},
        {"step": "deployed_model", "users": 1},
        {"step": "called_alias", "users": 1},
    ]
    assert body["median_hours_to_first_span"] is not None

    widened = overwatch_client().get("/api/internal/overwatch/activation/?days=90").json()
    assert widened["steps"][0] == {"step": "signed_up", "users": 3}


def test_project_activity_only_counts_after_a_user_joins(accounts):
    active = accounts["active"]
    project = active.project_memberships.get().project
    invited = make_user(email="invited@example.com")
    ProjectMembership.objects.create(user=invited, project=project)

    body = overwatch_client().get("/api/internal/overwatch/activation/").json()
    steps = {step["step"]: step["users"] for step in body["steps"]}

    assert steps["signed_up"] == 3
    assert steps["joined_project"] == 2
    assert steps["sent_first_span"] == 1
    assert steps["synced_capabilities"] == 1
    assert steps["ran_evaluation"] == 1
    assert steps["started_training"] == 1
    assert steps["deployed_model"] == 1
    assert steps["called_alias"] == 1


def test_accounts_table_pages_and_orders(accounts):
    first = overwatch_client().get("/api/internal/overwatch/users/?ordering=-spans&page_size=1")
    body = first.json()

    assert body["count"] == 3
    assert body["next"]
    row = body["results"][0]
    assert row["email"] == "active@example.com"
    assert row["projects"] == 1
    assert row["seats"] == 1
    assert row["spans"] == 2
    assert row["training_jobs"] == 1
    assert row["live_deployments"] == 1
    assert row["stage"] == "called_alias"

    by_projects = overwatch_client().get("/api/internal/overwatch/users/?ordering=-projects").json()
    assert by_projects["results"][0]["email"] == "active@example.com"

    assert overwatch_client().get("/api/internal/overwatch/overview/?days=0").status_code == 400


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

    body = overwatch_client().get(f"/api/internal/overwatch/users/{active.id}/").json()

    assert body["user"]["email"] == "active@example.com"
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
    assert overwatch_client().get(f"/api/internal/overwatch/users/{guest.id}/").status_code == 404


def test_responses_are_cached_for_an_hour(accounts):
    first = overwatch_client().get("/api/internal/overwatch/overview/").json()
    make_user()
    again = overwatch_client().get("/api/internal/overwatch/overview/").json()

    assert again["users_total"] == first["users_total"] == 3
    assert again["computed_at"] == first["computed_at"]
    computed = datetime.fromisoformat(first["computed_at"])
    assert datetime.fromisoformat(first["refreshes_at"]) - computed == timedelta(hours=1)

    other_window = overwatch_client().get("/api/internal/overwatch/overview/?days=7").json()
    assert other_window["users_total"] == 4

    page = overwatch_client().get("/api/internal/overwatch/users/").json()
    assert page["count"] == 4 and page["computed_at"]
