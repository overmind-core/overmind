"""The agent credential can only run bounded analytics reads, never product actions."""

import json

import pytest
from django.core.cache import cache
from django.db import connection
from factories import (
    api_key_client,
    auth_client,
    make_user,
)
from rest_framework.test import APIClient

from overbae.services.overwatch_privacy import model_question, posthog_url

pytestmark = [pytest.mark.django_db, pytest.mark.covers("authentication")]
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


def usage(days=7):
    response = client().post(
        URL, {"tool": "product_usage", "arguments": {"days": days}}, format="json"
    )
    assert response.status_code == 200
    return json.loads(response.data["content"])


@pytest.mark.covers("privacy")
def test_agent_questions_drop_credentials_emails_and_links_and_reads_stay_on_posthog():
    with pytest.raises(ValueError, match="credential_like_input"):
        model_question("Why does sk-live_abcdefghijkl fail?")
    question, identities = model_question(
        "Did jane@example.com and <mailto:Jane@Example.com|Jane> open https://x.example/a?"
    )
    assert question == "Did customer_1 and customer_1 open [link omitted]"
    assert identities == {"customer_1": "jane@example.com"}
    with pytest.raises(ValueError, match="unapproved_posthog_host"):
        posthog_url("https://posthog.example", "/api/projects/1/query/")
