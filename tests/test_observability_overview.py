import uuid

import pytest
from rest_framework.test import APIClient

from overbae.models import APIToken, Capability, Project, ProjectMembership, SignalEvent, Span, User

pytestmark = pytest.mark.django_db


def test_overview_counts_affected_runs_and_keeps_missing_measurements_distinct():
    user = User.objects.create_user(email=f"overview-{uuid.uuid4()}@test.com", password="pw")
    project = Project.objects.create(name="Overview", slug=f"overview-{uuid.uuid4()}")
    ProjectMembership.objects.create(user=user, project=project)
    capability = Capability.objects.create(project=project, name="Answer", slug="answer")
    key, _ = APIToken.create_for_user(user, project=project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key)
    trace_id = uuid.uuid4().hex
    Span.objects.create(
        project=project,
        capability=capability,
        trace_id=trace_id,
        span_id=uuid.uuid4().hex[:16],
        name="Answer",
        duration_ns=2_000_000,
        status_code=1,
        eval_attributes={"score": 0.9, "metrics": {"Completeness": {"score": 0.2}}},
    )
    Span.objects.create(
        project=project,
        capability=capability,
        trace_id=trace_id,
        span_id=uuid.uuid4().hex[:16],
        parent_span_id="parent",
        name="model",
        span_type="llm_call",
        status_code=2,
        attributes={"genai.model": "test/model", "genai.cost": 0.25},
        eval_attributes={"score": 0.8, "metrics": {"Completeness": {"score": 0.1}}},
    )
    Span.objects.create(
        project=project,
        trace_id=uuid.uuid4().hex,
        span_id=uuid.uuid4().hex[:16],
        name="Unscored run",
        status_code=1,
    )
    SignalEvent.objects.create(
        project=project,
        signal_type="repeated_failure",
        trace_id=trace_id,
        message="Repeated failure",
        dedupe_key=uuid.uuid4().hex,
    )

    response = client.get("/api/observability/overview/", {"project": str(project.id), "days": 7})
    assert response.status_code == 200
    body = response.json()
    assert body["runs"] == 2
    assert body["error_runs"] == 1
    assert body["checks"] == 2
    assert body["failed_checks"] == 2
    assert body["check_pass_rate"] == 0
    assert body["p95_duration_ms"] == 2
    assert body["measured_cost_runs"] == 1
    assert body["average_cost_usd"] == 0.25
    assert body["final_spans"] == 2
    assert body["issues"][0]["affected_runs"] == 1
    assert body["issues"][0]["occurrences"] == 2
    assert body["issues"][1]["kind"] == "signal"
    assert body["issues"][1]["affected_runs"] is None
    assert body["models"][0]["recorded_cost_usd"] == 0.25
    assert body["capabilities"][0]["runs"] == 1
    assert body["capabilities"][0]["failed_checks"] == 2
    assert len(body["daily"]) == 7

    assert (
        client.get("/api/observability/overview/", {"project": project.id, "days": 8}).status_code
        == 400
    )
    stranger = User.objects.create_user(email=f"stranger-{uuid.uuid4()}@test.com", password="pw")
    stranger_key, _ = APIToken.create_for_user(stranger)
    client.credentials(HTTP_X_API_KEY=stranger_key)
    assert client.get("/api/observability/overview/", {"project": project.id}).status_code == 404
