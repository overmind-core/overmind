"""The agent credential can only run bounded analytics reads, never product actions."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from django.core.cache import cache
from django.db import connection
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

from overbae.models import Dataset, DeployedModel, EvalRun, FinetuningJob, ProjectMembership, User

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/api/internal/overwatch/agent/query/"
TOKEN = "agent-test-service-token"


@pytest.fixture(autouse=True)
def _sandbox(settings):
    settings.SANDBOX_URL = "http://sandbox.test"
    settings.SANDBOX_TOKEN = "analytics-test"


@pytest.fixture(autouse=True)
def configured(settings):
    settings.OVERWATCH_SERVICE_TOKEN = TOKEN
    settings.SALES_DASHBOARD_TOKEN = "dashboard-test-token"
    cache.clear()


def client(token=TOKEN):
    instance = APIClient()
    instance.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return instance


@pytest.mark.parametrize("kind", ["anonymous", "dashboard", "staff", "project"])
def test_service_boundary_rejects_other_credentials(kind):
    user = make_user(is_staff=True)
    caller = {
        "anonymous": APIClient,
        "dashboard": lambda: client("dashboard-test-token"),
        "staff": lambda: auth_client(user),
        "project": lambda: api_key_client(user),
    }[kind]()
    assert (
        caller.post(
            URL, {"tool": "billing_summary", "arguments": {"days": 7}}, format="json"
        ).status_code
        == 403
    )


def test_blank_or_shared_credentials_fail_closed(settings):
    for value in ("", settings.SALES_DASHBOARD_TOKEN):
        settings.OVERWATCH_SERVICE_TOKEN = value
        assert client(value).post(URL, {}, format="json").status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {"tool": "request_leave", "arguments": {}},
        {"tool": "billing_summary", "arguments": {"days": 366}},
        {"tool": "billing_summary", "arguments": {"days": True}},
        {"tool": "billing_summary", "arguments": {"days": 7, "sql": "SELECT * FROM users"}},
        {"tool": "customer_activity", "arguments": {"days": 7, "email": "bad"}},
        {"tool": "billing_summary", "arguments": {"days": 7}, "employee_id": "invented"},
    ],
)
def test_invalid_queries_are_rejected(payload):
    assert client().post(URL, payload, format="json").status_code == 400


def test_aggregate_query_runs_under_database_read_only_budget():
    observed = []
    read_only = []

    def record(execute, sql, params, many, context):
        observed.append(sql)
        if sql.startswith("SELECT") and "transaction_read_only" not in sql:
            execute("SHOW transaction_read_only", None, False, context)
            read_only.append(context["cursor"].fetchone()[0])
        return execute(sql, params, many, context)

    with connection.execute_wrapper(record):
        response = client().post(
            URL, {"tool": "billing_summary", "arguments": {"days": 7}}, format="json"
        )
    assert response.status_code == 200
    assert response.data["private"] is False
    assert response.data["source"] == "Overmind product/billing records"
    assert response.data["window_days"] == 7
    assert response.data["as_of"]
    assert response["Cache-Control"] == "private, no-store"
    assert read_only and set(read_only) == {"on"}
    assert any("SET TRANSACTION READ ONLY" in sql for sql in observed)
    assert not any(
        sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in observed
    )


def test_customer_records_are_terminal_private_reports():
    make_user(email="customer@example.com")
    response = client().post(
        URL,
        {"tool": "customer_activity", "arguments": {"email": "customer@example.com", "days": 7}},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["private"] is True
    assert "customer@example.com" in response.data["content"]
    assert "Signed up" in response.data["content"]


def test_service_token_cannot_use_dashboard_or_product_writes():
    assert client().get("/api/internal/overwatch/overview/").status_code == 403
    assert client().post("/api/projects/", {"name": "not allowed"}).status_code in (401, 403)
    assert client().get(URL).status_code == 405


def test_retired_backend_slack_paths_are_gone():
    for path in ("command", "events"):
        assert (
            APIClient()
            .post(f"/api/internal/overwatch/slack/{path}/", {}, format="json")
            .status_code
            == 404
        )


@pytest.mark.parametrize(
    "tool,args",
    [
        ("activation_funnel", {"days": 7}),
        ("product_usage", {"days": 7}),
        ("customers_at_step", {"days": 7, "step": "signed_up", "limit": 2}),
    ],
)
def test_all_read_tools_share_the_validated_contract(tool, args):
    response = client().post(URL, {"tool": tool, "arguments": args}, format="json")
    assert response.status_code == 200
    assert response.data["private"] is (tool == "customers_at_step")
    assert response.data["content"]


@pytest.mark.parametrize("step", ["deployed_model", "called_alias"])
def test_resettable_milestones_do_not_claim_first_time_customers(step):
    user = make_user(email="returning@example.com")
    project = make_project(member=user)
    earlier = timezone.now() - timedelta(days=60)
    ProjectMembership.objects.filter(project=project).update(created_at=earlier)
    deployed = DeployedModel.objects.create(
        project=project,
        model_id="ft-returning",
        status=DeployedModel.Status.READY,
        deployed_at=earlier,
    )
    capability = make_capability(project, first_application_request_at=earlier)
    # The persisted state after a redeploy/model switch no longer retains the first date.
    DeployedModel.objects.filter(pk=deployed.pk).update(deployed_at=timezone.now())
    type(capability).objects.filter(pk=capability.pk).update(
        first_application_request_at=timezone.now()
    )
    response = client().post(
        URL,
        {"tool": "customers_at_step", "arguments": {"days": 7, "step": step}},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["private"] is True
    assert "cannot be determined" in response.data["content"]
    assert "returning@example.com" not in response.data["content"]
    assert "0 users" not in response.data["content"]


@pytest.mark.parametrize(
    "step",
    [
        "signed_up",
        "joined_project",
        "sent_first_span",
        "synced_capabilities",
        "ran_evaluation",
        "started_training",
    ],
)
def test_milestone_report_counts_all_matches_but_fetches_only_newest_customers(step, time_machine):
    time_machine.move_to(datetime(2026, 10, 25, 12, tzinfo=UTC), tick=False)
    now = timezone.now()

    def add_milestone(user, at):
        if step == "signed_up":
            User.objects.filter(pk=user.pk).update(date_joined=at)
            return
        project = make_project(member=user)
        ProjectMembership.objects.filter(project=project).update(
            created_at=at if step == "joined_project" else at - timedelta(days=1)
        )
        if step == "joined_project":
            return
        if step == "sent_first_span":
            record = make_span(project, trace_id="a" * 32)
            field = "received_at"
        elif step == "synced_capabilities":
            record = make_capability(project)
            field = "created_at"
        elif step == "ran_evaluation":
            record = EvalRun.objects.create(project=project, status=EvalRun.Status.COMPLETED)
            field = "created_at"
        else:
            dataset = Dataset.objects.create(project=project, name="Train", intent="train")
            record = FinetuningJob.objects.create(
                project=project,
                dataset=dataset,
                base_model="Qwen/Qwen3-4B",
                status=FinetuningJob.Status.SUCCEEDED,
            )
            field = "created_at"
        type(record).objects.filter(pk=record.pk).update(**{field: at})

    expected = []
    for index in range(30):
        user = make_user(email=f"bounded-{index:02}@example.com")
        # Ties must keep deterministic user order, not duplicate a customer.
        at = now - timedelta(hours=index // 2 + 1)
        add_milestone(user, at)
        expected.append(user.email)
    guest = make_user(email="guest@example.com", is_guest=True)
    add_milestone(guest, now)
    old = make_user(email="old-milestone@example.com")
    add_milestone(old, now - timedelta(days=60))
    if step != "signed_up":
        add_milestone(old, now - timedelta(hours=1))
    row_counts = []

    def observe_rows(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if sql.startswith("SELECT") and User._meta.db_table in sql:
            row_counts.append(context["cursor"].rowcount)
        return result

    with connection.execute_wrapper(observe_rows):
        response = client().post(
            URL,
            {"tool": "customers_at_step", "arguments": {"days": 7, "step": step, "limit": 3}},
            format="json",
        )
    assert response.status_code == 200
    report = response.data["content"]
    assert "30 users" in report and "Showing 3 results" in report
    actual = [line.split(": ")[0] for line in report.splitlines() if "@" in line]
    assert actual == expected[:3]
    # Observe real PostgreSQL result sizes: a small response must not fetch all matches.
    assert row_counts and max(row_counts) <= 3


def test_agent_reads_share_one_snapshot_for_an_hour(time_machine):
    time_machine.move_to(datetime(2026, 10, 25, 12, tzinfo=UTC), tick=False)
    make_user(email="cached@example.com")
    calls = [
        ("activation_funnel", {"days": 7}),
        ("billing_summary", {"days": 7}),
        ("customer_activity", {"days": 7, "email": "Cached@example.com"}),
        ("customers_at_step", {"days": 7, "step": "signed_up"}),
    ]

    def ask(tool, arguments):
        response = client().post(URL, {"tool": tool, "arguments": arguments}, format="json")
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        return response.data

    first = {tool: ask(tool, arguments) for tool, arguments in calls}
    make_user(email="later@example.com")
    User.objects.filter(email="cached@example.com").update(
        date_joined=timezone.now() - timedelta(days=2)
    )
    time_machine.shift(3599)
    observed = []

    def record(execute, sql, params, many, context):
        observed.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(record):
        again = ask("billing_summary", {"days": 7})
    assert again == first["billing_summary"]
    assert observed == []
    assert ask("activation_funnel", {"days": 7}) == first["activation_funnel"]
    assert (
        ask("customer_activity", {"days": 7, "email": "cached@example.com"})
        == first["customer_activity"]
    )
    assert (
        ask("customers_at_step", {"days": 7, "step": "signed_up", "limit": 25})
        == first["customers_at_step"]
    )
    other_window = ask("activation_funnel", {"days": 30})
    assert other_window["content"] != first["activation_funnel"]["content"]
    assert other_window["as_of"] != first["activation_funnel"]["as_of"]
    assert "later@example.com" not in first["customers_at_step"]["content"]

    time_machine.shift(2)
    refreshed = ask("activation_funnel", {"days": 7})
    customer = ask("customer_activity", {"days": 7, "email": "cached@example.com"})
    assert refreshed["content"] != first["activation_funnel"]["content"]
    assert refreshed["as_of"] != first["activation_funnel"]["as_of"]
    assert customer["content"] != first["customer_activity"]["content"]
    assert (
        "later@example.com"
        in ask("customers_at_step", {"days": 7, "step": "signed_up", "limit": 25})["content"]
    )


@pytest.fixture
def posthog_usage_source(settings, scripted):
    settings.POSTHOG_PERSONAL_API_KEY = "posthog-cache-test"
    settings.POSTHOG_PROJECT_ID = "42"
    settings.POSTHOG_API_HOST = "https://eu.posthog.com"
    state = {"available": True, "count": 1}

    def answer(request):
        if not state["available"]:
            return 503, {"content-type": "application/json"}, b"{}"
        query = request.json["query"]["query"]
        rows = []
        if "SELECT toDate(" in query:
            rows = [[timezone.now().date().isoformat(), "cli.completed", state["count"]]]
        elif "SELECT uniq(person_id)" in query:
            rows = [[state["count"]]]
        elif "countIf(" in query:
            rows = [[0, 0]]
        return 200, {"content-type": "application/json"}, json.dumps({"results": rows}).encode()

    return scripted("https://eu.posthog.com").then(answer), state


def usage(days=7):
    response = client().post(
        URL, {"tool": "product_usage", "arguments": {"days": days}}, format="json"
    )
    assert response.status_code == 200
    return json.loads(response.data["content"])


def test_agent_usage_reuses_source_snapshot_for_an_hour_without_caching_product_rows(
    posthog_usage_source, time_machine
):
    time_machine.move_to(datetime(2026, 10, 25, 12, tzinfo=UTC), tick=False)
    api, state = posthog_usage_source
    first = usage()
    assert first["posthog"]["available"] is True and len(api.calls) == 5
    state["count"] = 2
    make_user()
    time_machine.shift(3599)
    cached = usage()
    assert cached["posthog"] == first["posthog"]
    assert cached["registered_users"] == first["registered_users"] + 1
    assert len(api.calls) == 5
    time_machine.shift(2)
    refreshed = usage()
    assert refreshed["posthog"]["totals"]["cli_completions"] == 2
    assert refreshed["posthog"]["window_end"] != first["posthog"]["window_end"]
    assert len(api.calls) == 10


def test_agent_usage_failure_is_cached_briefly_then_recovers(posthog_usage_source, time_machine):
    time_machine.move_to(datetime(2026, 10, 25, 12, tzinfo=UTC), tick=False)
    api, state = posthog_usage_source
    state["available"] = False
    first = usage()["posthog"]
    assert first["available"] is False and "totals" not in first
    assert len(api.calls) == 5
    state["available"] = True
    time_machine.shift(59)
    assert usage()["posthog"] == first
    assert len(api.calls) == 5
    time_machine.shift(2)
    assert usage()["posthog"]["available"] is True
    assert len(api.calls) == 10


def test_agent_usage_cache_separates_window_source_and_utc_day(
    posthog_usage_source, time_machine, settings
):
    time_machine.move_to(datetime(2026, 10, 25, 23, 59, tzinfo=UTC), tick=False)
    api, state = posthog_usage_source
    first = usage()["posthog"]
    assert first["available"] is True
    state["count"] = 2
    assert usage(30)["posthog"]["totals"]["cli_completions"] == 2
    settings.POSTHOG_PROJECT_ID = "43"
    assert usage()["posthog"]["totals"]["cli_completions"] == 2
    settings.POSTHOG_PERSONAL_API_KEY = "rotated-read-key"
    state["count"] = 3
    assert usage()["posthog"]["totals"]["cli_completions"] == 3
    time_machine.shift(120)
    today = usage()["posthog"]
    assert today["window_start"] != first["window_start"]
    assert len(api.calls) == 25
