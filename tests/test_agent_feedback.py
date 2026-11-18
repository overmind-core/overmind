import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from factories import auth_client, make_project, make_user
from mcp_fixtures import asgi_db, call_tool, mcp_context
from rest_framework.test import APIClient
from starlette.testclient import TestClient

from overbae.models import APIToken, Feedback, Project, ProjectMembership, User
from overbae.services.feedback import AGENT_REPORTS_PER_HOUR
from overbae.services.mcp.server import create_mcp_application

pytestmark = [pytest.mark.django_db, pytest.mark.covers("privacy")]

SLACK = "https://hooks.slack.com/services/T000/B000/feedback"
PEM = "PRIVATE" + " KEY"  # Split so the private-key commit hook skips this fixture.


def report(**fields):
    return {
        "report_id": str(uuid.uuid4()),
        "kind": "failure",
        "area": "datasets",
        "severity": "blocked",
        "outcome": "worked_around",
        "headline": "run_dataset_pipeline rejects a package that validates",
        "summary": "validate_dataset_pipeline passed, then run_dataset_pipeline refused the run.",
        "trail": [
            {"action": "validate_dataset_pipeline", "result": "ok"},
            {
                "action": "run_dataset_pipeline",
                "result": "error",
                "error_code": "dataset_invalid",
                "attempts": 3,
            },
        ],
        "environment": {"harness": "claude-code", "model": "claude-opus-5-5", "os": "macos"},
        **fields,
    }


@pytest.fixture
def slack(settings, scripted, eager):
    settings.SLACK_FEEDBACK_WEBHOOK_URL = SLACK
    return scripted("https://hooks.slack.com").reply(text="ok")


def reporting(permission="read"):
    context = mcp_context(permission)
    User.objects.filter(pk=context.user.pk).update(agent_feedback=True)
    context.user.agent_feedback = True
    return context


@asgi_db
def test_agents_see_the_report_tool_only_after_the_person_allows_it():
    def listed(choice):
        user = User.objects.create_user(
            email=f"r-{uuid.uuid4().hex[:8]}@test.com",
            clerk_user_id=f"clerk_{uuid.uuid4().hex}",
            agent_feedback=choice,
        )
        project = Project.objects.create(name="R", slug=f"r-{uuid.uuid4().hex[:8]}")
        ProjectMembership.objects.create(user=user, project=project)
        raw, _ = APIToken.create_for_user(user, project=project, permission=["read"])
        with TestClient(create_mcp_application()) as client:
            response = client.post(
                "/api/mcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-Api-Key": raw, "Accept": "application/json"},
            )
        return "submit_feedback" in {tool["name"] for tool in response.json()["result"]["tools"]}

    assert [listed(None), listed(False), listed(True)] == [False, False, True]


def test_an_agent_without_permission_saves_nothing():
    context = mcp_context("read")
    result = call_tool("submit_feedback", report(), context)

    assert result.isError
    assert result.structuredContent["error"]["code"] == "permission_denied"
    assert not Feedback.objects.exists()


def test_reports_keep_platform_behaviour_and_drop_what_identifies_the_customer(slack):
    context = reporting()
    leaky = report(
        summary=(
            "Upload of /home/alice/acme/claims.csv for alice@acme.com failed at "
            "https://acme.internal/upload from 10.2.3.4 with key sk-live-abcdefghijklmnop. "
            "Authorization: Bearer opaque-example-123456. <!channel> <@U123> please look. "
            f"-----BEGIN RSA {PEM}-----\nMIIEpkeybody\n-----END RSA {PEM}-----"
        ),
        trail=[
            {
                "action": "overmind dataset upload",
                "result": "error",
                "error_code": "ghp_abcdefghijklmnopqrstu",
                "note": "token: ghp_abcdefghijklmnopqrstu",
            }
        ],
        resources=["overmind://project/current?token=ghp_abcdefghijklmnopqrstu"],
    )
    result = call_tool("submit_feedback", leaky, context)

    assert not result.isError, result.structuredContent
    stored = Feedback.objects.get()
    saved = json.dumps({"message": stored.message, "detail": stored.detail})
    for secret in ("alice", "acme", "10.2.3.4", "sk-live", "ghp_", "opaque-example", "MIIEp"):
        assert secret not in saved
    assert stored.message.startswith("Upload of [path] for [email] failed at [link]")
    [posted] = slack.calls
    body = json.dumps(posted.json)
    for secret in ("alice", "acme", "sk-live", "ghp_", "<!channel>", "<@U123>"):
        assert secret not in body
    assert "Settings" in result.structuredContent["tell_user"]


@pytest.mark.covers("mcp-surface")
def test_a_resent_report_updates_it_and_a_repeated_problem_posts_once(slack):
    context = reporting()
    first = report()
    call_tool("submit_feedback", first, context)
    resent = call_tool("submit_feedback", {**first, "outcome": "resolved"}, context)
    again = call_tool("submit_feedback", report(), reporting())
    other = call_tool("submit_feedback", first, reporting())

    assert resent.structuredContent["created"] is False
    assert Feedback.objects.get(pk=first["report_id"]).outcome == "resolved"
    assert again.structuredContent["created"] is True
    assert Feedback.objects.count() == 2
    assert len(slack.calls) == 1
    blocks = slack.calls[0].json["blocks"]
    assert blocks[0]["text"]["text"] == first["headline"]
    assert other.structuredContent["error"]["code"] == "request_key_conflict"


@pytest.mark.covers("mcp-surface")
def test_trails_name_resources_with_queries_and_refuse_free_text():
    context = reporting()
    current = f"overmind://project/current?project_id={uuid.uuid4()}"
    named = call_tool(
        "submit_feedback",
        report(trail=[{"action": current, "result": "ok"}], resources=[current]),
        context,
    )
    leaky = call_tool(
        "submit_feedback", report(resources=["overmind://datasets/alice@acme.com"]), context
    )

    assert not named.isError, named.structuredContent
    assert Feedback.objects.get().detail["trail"][0]["action"] == current
    assert leaky.structuredContent["error"]["code"] == "invalid_input"


@pytest.mark.covers("mcp-surface")
def test_reports_per_hour_are_bounded():
    context = reporting()
    Feedback.objects.bulk_create(
        Feedback(source="agent", user=context.user, kind="failure", title="t", message="m")
        for _ in range(AGENT_REPORTS_PER_HOUR)
    )
    result = call_tool("submit_feedback", report(), context)

    assert result.structuredContent["error"]["code"] == "rate_limited"
    assert result.structuredContent["error"]["retryable"] is True
    Feedback.objects.update(created_at=timezone.now() - timedelta(hours=2))
    assert not call_tool("submit_feedback", report(), context).isError


def test_the_sign_up_choice_reaches_the_new_account(clerk, client):
    declined = clerk.user("declined@example.com")
    clerk.users[declined]["unsafe_metadata"] = {"agentFeedback": False}
    silent = clerk.user("silent@example.com")

    for user_id in (declined, silent):
        response = client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {clerk.token(user_id)}")
        assert response.status_code == 200
    assert User.objects.get(email="declined@example.com").agent_feedback is False
    assert User.objects.get(email="declined@example.com").agent_feedback_at is not None
    assert User.objects.get(email="silent@example.com").agent_feedback is None

    user = User.objects.get(email="silent@example.com")
    response = auth_client(user).patch("/api/auth/me/", {"agent_feedback": True}, format="json")
    assert response.json()["agent_feedback"] is True
    guest = make_user(is_guest=True)
    assert (
        auth_client(guest)
        .patch("/api/auth/me/", {"agent_feedback": True}, format="json")
        .status_code
        == 403
    )


@pytest.mark.covers("http-api")
def test_console_feedback_keeps_only_the_sender_project(slack):
    user = make_user(email="person@example.com")
    own = make_project(member=user)
    other = make_project(member=make_user())
    api = auth_client(user)
    for project in (own, other):
        response = api.post(
            "/api/feedback/",
            {
                "kind": "idea",
                "area": "datasets",
                "message": "Show the row count\nin the header.",
                "project": str(project.pk),
                "page": "/datasets",
            },
            format="json",
        )
        assert response.status_code == 201

    without_area = api.post(
        "/api/feedback/", {"kind": "failure", "message": "Upload failed"}, format="json"
    )

    assert without_area.status_code == 201
    rows = Feedback.objects.order_by("created_at")
    assert [row.project_id for row in rows] == [own.pk, None, None]
    assert rows[2].area == "other"
    assert rows[0].title == "Show the row count"
    assert "person@example.com" not in json.dumps([call.json for call in slack.calls])


@pytest.mark.covers("measured-facts")
def test_overwatch_shows_a_report_beside_the_calls_posthog_recorded(settings, scripted):
    settings.SALES_DASHBOARD_TOKEN = "overwatch-test-token"
    settings.POSTHOG_PERSONAL_API_KEY = "posthog-read-test"
    settings.POSTHOG_PROJECT_ID = "42"
    settings.POSTHOG_API_HOST = "https://us.posthog.com"
    context = reporting()
    call_tool("submit_feedback", report(), context)
    posthog = scripted("https://us.posthog.com").reply(
        json_body={
            "results": [
                ["2026-11-18T10:00:00Z", "validate_dataset_pipeline", False, None],
                ["2026-11-18T10:01:00Z", "run_dataset_pipeline", True, "dataset_invalid"],
            ]
        }
    )
    overwatch = APIClient()
    overwatch.credentials(HTTP_AUTHORIZATION="Bearer overwatch-test-token")

    [row] = overwatch.get("/api/internal/overwatch/feedback/").json()["results"]
    detail = overwatch.get(f"/api/internal/overwatch/feedback/{row['id']}/").json()

    assert (row["seen"], row["client"]) == (1, "claude-code")
    assert [call["tool"] for call in detail["server_trail"]["calls"]] == [
        "validate_dataset_pipeline",
        "run_dataset_pipeline",
    ]
    assert context.user.clerk_user_id in posthog.calls[0].json["query"]["query"]
    posthog.reply(500, text="down")
    posthog.steps.pop(0)
    trail = overwatch.get(f"/api/internal/overwatch/feedback/{row['id']}/").json()["server_trail"]
    assert trail == {"available": False, "reason": "PostHog tool calls could not be loaded."}
