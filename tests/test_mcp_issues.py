import uuid
from datetime import timedelta

import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone
from mcp_fixtures import call_tool

from overbae.innermind.signals import SignalRunner
from overbae.models import (
    APIToken,
    Capability,
    Issue,
    Project,
    ProjectMembership,
    Span,
    User,
)
from overbae.services.mcp.context import MCPContext

pytestmark = [pytest.mark.django_db, pytest.mark.covers("metrics-math")]


@pytest.fixture
def setup():
    user = User.objects.create_user(email=f"{uuid.uuid4()}@test.com", password="pw")
    project = Project.objects.create(name="Issues", slug=f"issues-{uuid.uuid4()}")
    ProjectMembership.objects.create(user=user, project=project)
    capability = Capability.objects.create(project=project, name="Answers", slug="answers")
    now = timezone.now()
    for hours in (2, 3, 4, 5, 6):
        start = int((now - timedelta(hours=hours)).timestamp() * 1e9)
        Span.objects.create(
            span_id=uuid.uuid4().hex[:16],
            trace_id=uuid.uuid4().hex,
            project=project,
            capability=capability,
            name="answer",
            status_code=1,
            start_time_ns=start,
            end_time_ns=start + 1_000_000,
            eval_attributes={
                "score": 0.2,
                "metrics": {
                    "grounded": {"expectation": "Stays grounded.", "score": 0.2, "verdict": "no"}
                },
            },
        )
    async_to_sync(SignalRunner(project.id).run)()
    key, token = APIToken.create_for_user(user, project=project)
    return project, MCPContext(user=user, project=project, token=token)


def call(context, name, arguments):
    result = call_tool(name, arguments, context)
    assert not result.isError, result.content
    return result.structuredContent


def test_training_signals_lead_an_agent_to_the_data(setup):
    _, context = setup
    result = call(context, "list_training_signals", {})
    [signal] = result["signals"]
    assert (signal["capability_name"], signal["failing_traces"]) == ("Answers", 5)
    assert signal["next_actions"][0]["tool"] == "create_dataset_from_traces"
    assert signal["next_actions"][0]["arguments"]["filters"]["outcome"] == "passing"
    assert signal["next_actions"][0]["arguments"]["split"]["eval_percent"] == 10
    assert result["page"]["total"] == 1


def test_issues_can_be_listed_inspected_and_renamed(setup):
    _, context = setup
    [row] = call(context, "list_issues", {"cause": "model"})["issues"]
    assert call(context, "list_issues", {"cause": "system"})["issues"] == []

    detail = call(context, "inspect_issue", {"issue": row["id"]})
    assert detail["examples"][0]["reason"]
    assert detail["next_actions"][-1]["arguments"] == {"issue": row["id"]}
    traces = call(context, "query_traces", {"issue": row["id"]})["traces"]
    assert len(traces) >= 1

    renamed = call(context, "update_issue", {"issue": row["id"], "title": "Invents citations"})
    assert renamed["issue"]["title"] == "Invents citations"
    muted = call(context, "update_issue", {"issue": row["id"], "state": "mute"})
    assert muted["issue"]["state"] == "muted"


def test_issue_tools_stay_inside_the_project(setup):
    project, context = setup
    other = Project.objects.create(name="Other", slug=f"other-{uuid.uuid4()}")
    issue = Issue.objects.get(project=project)
    Issue.objects.filter(pk=issue.pk).update(project=other)
    result = call_tool("inspect_issue", {"issue": str(issue.pk)}, context)
    assert result.isError
    assert result.structuredContent["error"]["code"] == "issue_not_found"
