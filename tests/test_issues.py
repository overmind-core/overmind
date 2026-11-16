import uuid
from datetime import timedelta

import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from overbae.api import config
from overbae.innermind import signals
from overbae.innermind.signals import SignalRunner
from overbae.models import (
    APIToken,
    Capability,
    Dataset,
    EvalScore,
    FinetuningJob,
    Issue,
    IssueTrace,
    ModelHour,
    Project,
    ProjectMembership,
    SignalEvent,
    Span,
    TrafficHour,
    User,
)
from overbae.services.datasets.land import read_traces
from overbae.services.issue_overview import (
    capability_rows,
    issue_rows,
    overview,
    trace_issues,
    training_plan,
    trends,
)
from overbae.services.simulated_training import finish_simulated, refuse_paid_work

pytestmark = [pytest.mark.django_db, pytest.mark.covers("metrics-math")]


@pytest.fixture
def project():
    return Project.objects.create(name="Issues", slug=f"issues-{uuid.uuid4()}")


@pytest.fixture
def capability(project):
    return Capability.objects.create(project=project, name="Disputes", slug="disputes")


def make_span(project, capability, *, trace_id, parent=None, at=None, name="step", **fields):
    at = at or timezone.now() - timedelta(minutes=5)
    start = int(at.timestamp() * 1e9)
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=parent,
        project=project,
        capability=capability,
        name=name,
        start_time_ns=start,
        end_time_ns=start + 1_000_000,
        **fields,
    )


def timed_out_trace(project, capability, at=None, seconds=30):
    """A tool that fails three times under a root that fails because of it."""
    trace_id = uuid.uuid4().hex
    root = make_span(
        project,
        capability,
        trace_id=trace_id,
        at=at,
        name="resolve",
        status_code=2,
        status_message="dispute evidence unavailable",
    )
    for _ in range(3):
        make_span(
            project,
            capability,
            trace_id=trace_id,
            parent=root.span_id,
            at=at,
            name="fetch_evidence",
            span_type="tool_call",
            operation="fetch_evidence",
            status_code=2,
            status_message=f"store timeout after {seconds}s",
            attributes={"tool.name": "fetch_evidence"},
        )
    return trace_id


def run(project):
    async_to_sync(SignalRunner(project.id).run)()


def test_one_root_cause_seen_by_several_checks_is_one_issue(project, capability):
    first = timed_out_trace(project, capability, seconds=30)
    second = timed_out_trace(project, capability, seconds=31)
    run(project)
    run(project)

    issue = Issue.objects.get(project=project)
    assert issue.cause == Issue.Cause.SYSTEM
    assert issue.title == "fetch_evidence: store timeout after 30s"
    assert set(IssueTrace.objects.values_list("trace_id", flat=True)) == {first, second}
    assert {"errors", "tool_failure", "retry_storm"} <= set(
        SignalEvent.objects.values_list("signal_type", flat=True)
    )


def test_a_failed_expectation_is_a_trainable_issue(project, capability):
    make_span(
        project,
        capability,
        trace_id=uuid.uuid4().hex,
        status_code=1,
        eval_attributes={
            "score": 0.2,
            "metrics": {
                "grounded": {"expectation": "Stays grounded.", "score": 0.2, "verdict": "no"}
            },
        },
    )
    run(project)

    issue = Issue.objects.get(project=project)
    assert (issue.cause, issue.title) == (Issue.Cause.MODEL, "Fails: Stays grounded.")


def test_a_tool_error_from_bad_arguments_is_a_model_issue(project, capability):
    make_span(
        project,
        capability,
        trace_id=uuid.uuid4().hex,
        name="book",
        status_code=2,
        status_message="validation error: missing required argument",
    )
    run(project)

    assert Issue.objects.get(project=project).cause == Issue.Cause.MODEL


def test_a_failure_keeps_its_own_time_through_a_late_check(project, capability):
    failed_at = timezone.now() - timedelta(days=9)
    trace_id = timed_out_trace(project, capability, at=failed_at)
    run(project)

    assert abs(Issue.objects.get(project=project).first_seen_at - failed_at) < timedelta(seconds=1)
    event = SignalEvent.objects.filter(trace_id=trace_id).first()
    assert abs(event.occurred_at - failed_at) < timedelta(seconds=1)


def test_a_failure_returning_after_silence_regresses(project, capability):
    timed_out_trace(project, capability, at=timezone.now() - timedelta(days=20))
    run(project)
    assert Issue.objects.get(project=project).regressed_at is None

    timed_out_trace(project, capability)
    run(project)
    assert Issue.objects.get(project=project).regressed_at is not None


def test_every_span_is_read_and_counted_past_a_page(project, capability, monkeypatch):
    monkeypatch.setattr(signals, "PAGE", 2)
    traces = [timed_out_trace(project, capability) for _ in range(3)]
    run(project)

    assert set(IssueTrace.objects.values_list("trace_id", flat=True)) == set(traces)
    assert sum(TrafficHour.objects.values_list("traces", flat=True)) == 3


def test_a_late_score_raises_an_issue_on_an_old_span(project, capability):
    root = make_span(
        project, capability, trace_id=uuid.uuid4().hex, at=timezone.now() - timedelta(days=3)
    )
    run(project)
    assert not Issue.objects.filter(project=project).exists()

    metrics = {"grounded": {"expectation": "Stays grounded.", "score": 0.2, "verdict": "no"}}
    Span.objects.filter(pk=root.pk).update(
        received_at=timezone.now() - timedelta(days=3),
        eval_attributes={"score": 0.2, "metrics": metrics},
    )
    EvalScore.objects.create(
        project=project,
        source_type=EvalScore.SourceType.SPAN,
        source_id=root.span_id,
        score=0.2,
        metrics=metrics,
    )
    run(project)

    assert Issue.objects.get(project=project).title == "Fails: Stays grounded."
    hour = TrafficHour.objects.get(project=project)
    assert (hour.scored, hour.score_sum) == (1, 0.2)


def test_rollups_recompute_and_keep_unknown_cost_unknown(project, capability):
    trace_id = uuid.uuid4().hex
    root = make_span(project, capability, trace_id=trace_id, name="run")
    llm = {"genai.model": "m", "genai.total_tokens": 10, "genai.cost": 0.5}
    make_span(project, capability, trace_id=trace_id, parent=root.span_id, attributes=llm)
    make_span(
        project,
        capability,
        trace_id=trace_id,
        parent=root.span_id,
        attributes={"genai.model": "unpriced", "genai.total_tokens": "n/a"},
    )
    run(project)
    run(project)

    assert TrafficHour.objects.get(project=project).traces == 1
    rows = {row.model: row for row in ModelHour.objects.filter(project=project)}
    assert (rows["m"].calls, rows["m"].tokens, rows["m"].cost) == (1, 10, 0.5)
    assert rows["unpriced"].cost is None


def failing_trace(project, capability, at, expectation="Stays grounded."):
    metrics = {"check": {"expectation": expectation, "score": 0.2, "verdict": "no"}}
    return make_span(
        project,
        capability,
        trace_id=uuid.uuid4().hex,
        at=at,
        status_code=1,
        eval_attributes={"score": 0.2, "metrics": metrics},
    )


def passing_traces(project, capability, count, at):
    for _ in range(count):
        make_span(project, capability, trace_id=uuid.uuid4().hex, at=at, status_code=1)


def test_a_failure_that_does_not_repeat_is_not_an_issue_to_show(project, capability):
    now = timezone.now()
    for _ in range(4):
        failing_trace(project, capability, now - timedelta(hours=2))
    failing_trace(project, capability, now - timedelta(minutes=5), expectation="Other.")
    run(project)
    assert issue_rows(project.pk, "7d") == []

    failing_trace(project, capability, now - timedelta(hours=8))
    run(project)
    assert [row["title"] for row in issue_rows(project.pk, "7d")] == ["Fails: Stays grounded."]


def test_rate_uses_all_traffic_and_sets_severity(project, capability):
    now = timezone.now()
    for hours in (2, 3, 4, 5, 6):
        failing_trace(project, capability, now - timedelta(hours=hours))
    passing_traces(project, capability, 45, now - timedelta(hours=2))
    run(project)

    row = issue_rows(project.pk, "7d")[0]
    assert row["rate"] == pytest.approx(5 / 50)
    assert (row["severity"], row["state"]) == ("high", "new")

    passing_traces(project, capability, 150, now - timedelta(hours=2))
    run(project)
    row = issue_rows(project.pk, "7d")[0]
    assert (row["rate"], row["severity"]) == (pytest.approx(5 / 200), "medium")


def test_a_leak_is_critical_at_one_trace(project, capability):
    make_span(
        project,
        capability,
        trace_id=uuid.uuid4().hex,
        status_code=1,
        attributes={"overmind.output.data": "Reach them at owner@example.com"},
    )
    passing_traces(project, capability, 99, timezone.now() - timedelta(hours=1))
    run(project)

    row = next(row for row in issue_rows(project.pk, "7d") if row["signal_type"] == "leak")
    assert row["severity"] == "critical"
    assert row["title"] == "Email in output"


def test_model_issues_become_training_signals_and_system_issues_do_not(project, capability):
    now = timezone.now()
    other = Capability.objects.create(project=project, name="Other", slug="other")
    for hours in (2, 3, 4, 5, 6):
        failing_trace(project, capability, now - timedelta(hours=hours))
        timed_out_trace(project, other, at=now - timedelta(hours=hours))
    passing_traces(project, capability, 15, now - timedelta(hours=2))
    run(project)

    result = overview(project.pk, "7d")
    assert [item["capability_name"] for item in result["training_signals"]] == ["Disputes"]
    signal = result["training_signals"][0]
    assert (signal["failing_traces"], signal["passing_traces"]) == (5, 15)
    assert [row["capability_name"] for row in result["system_issues"]] == ["Other"]
    assert result["traces_checked"] == 25


def test_spend_spikes_stay_out_of_what_to_know_first(project, capability):
    now = timezone.now()
    for hours in (2, 3, 4, 5, 6):
        trace_id = uuid.uuid4().hex
        root = make_span(project, capability, trace_id=trace_id, at=now - timedelta(hours=hours))
        for cost in (0.01, 0.01, 0.01, 0.5):
            make_span(
                project,
                capability,
                trace_id=trace_id,
                parent=root.span_id,
                at=now - timedelta(hours=hours),
                span_type="llm_call",
                attributes={"genai.model": "m", "genai.cost": cost, "genai.total_tokens": 10},
            )
    run(project)

    assert any(row["signal_type"] == "cost_spike" for row in issue_rows(project.pk, "7d"))
    assert overview(project.pk, "7d")["system_issues"] == []


def test_trends_count_each_failing_trace_once_and_keep_unknowns(project, capability):
    timed_out_trace(project, capability)
    run(project)

    result = trends(project.pk, "7d")
    assert result["current"]["traces"] == 1
    assert result["current"]["failing"] == 1
    assert result["current"]["score"] is None
    assert result["current"]["cost"] is None
    assert result["previous"]["failure_rate"] is None
    assert result["comparable"] is False


def test_a_full_prior_window_is_comparable(project, capability):
    make_span(
        project, capability, trace_id=uuid.uuid4().hex, at=timezone.now() - timedelta(days=15)
    )
    run(project)
    assert trends(project.pk, "7d")["comparable"] is True


def test_issue_api_is_project_scoped_and_changes_state(project, capability):
    timed_out_trace(project, capability)
    run(project)
    issue = Issue.objects.get(project=project)
    member = User.objects.create_user(email=f"{uuid.uuid4()}@test.com", password="pw")
    ProjectMembership.objects.create(user=member, project=project)
    key, _ = APIToken.create_for_user(member, project=project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key)

    outsider = User.objects.create_user(email=f"{uuid.uuid4()}@test.com", password="pw")
    outside, _ = APIToken.create_for_user(outsider)
    stranger = APIClient()
    stranger.credentials(HTTP_X_API_KEY=outside)
    assert stranger.get("/api/issues/overview/", {"project": project.pk}).status_code == 403
    assert stranger.get(f"/api/issues/{issue.pk}/").status_code == 404

    assert client.get("/api/issues/overview/", {"project": project.pk}).status_code == 200
    detail = client.get(f"/api/issues/{issue.pk}/")
    assert detail.status_code == 200
    assert detail.data["examples"][0]["reason"]
    traces = client.get("/api/traces/", {"project": project.pk, "issue": issue.pk})
    assert {row["trace_id"] for row in traces.data["results"]} == {
        IssueTrace.objects.get(issue=issue).trace_id
    }

    muted = client.post(f"/api/issues/{issue.pk}/state/", {"action": "mute"}, format="json")
    assert muted.data["state"] == "muted"
    assert (
        client.post(f"/api/issues/{issue.pk}/state/", {"action": "reopen"}, format="json").data[
            "state"
        ]
        == "new"
    )


def test_a_shared_outage_across_capabilities_is_one_line_to_fix(project, capability):
    now = timezone.now()
    other = Capability.objects.create(project=project, name="Refunds", slug="refunds")
    for hours in (2, 3, 4, 5, 6):
        timed_out_trace(project, capability, at=now - timedelta(hours=hours))
        timed_out_trace(project, other, at=now - timedelta(hours=hours))
    run(project)

    assert len(issue_rows(project.pk, "7d")) == 2
    result = overview(project.pk, "7d")
    [line] = result["system_issues"]
    assert (line["capability_count"], line["traces"]) == (2, 10)
    assert line["rate"] == pytest.approx(1.0)


def test_training_queue_ranks_capabilities_and_counts_a_trace_once(project, capability):
    now = timezone.now()
    other = Capability.objects.create(project=project, name="Refunds", slug="refunds")
    for hours in (2, 3, 4, 5, 6):
        at = now - timedelta(hours=hours)
        failing_trace(project, capability, at)
        failing_trace(project, other, at)
        failing_trace(project, other, at)
        # One trace showing two model issues still counts as one failing trace.
    both = make_span(
        project,
        capability,
        trace_id=uuid.uuid4().hex,
        at=now - timedelta(hours=2),
        status_code=1,
        eval_attributes={
            "score": 0.2,
            "metrics": {
                "a": {"expectation": "Stays grounded.", "score": 0.2, "verdict": "no"},
                "b": {"expectation": "Cites sources.", "score": 0.2, "verdict": "no"},
            },
        },
    )
    passing_traces(project, capability, 10, now - timedelta(hours=2))
    run(project)

    result = overview(project.pk, "7d")
    assert result["signals_to_train"] == 2
    names = [item["capability_name"] for item in result["training_signals"]]
    assert names == ["Refunds", "Disputes"]
    assert result["training_signals"][1]["failing_traces"] == 6
    ranked = capability_rows(project.pk, "7d")
    assert [row["capability_name"] for row in ranked] == names
    assert all(row["can_train"] for row in ranked)
    assert len(trace_issues(project.pk, both.trace_id)) == 2


def test_training_plan_selects_each_side_of_the_split(project, capability):
    now = timezone.now()
    for hours in (2, 3, 4, 5, 6):
        failing_trace(project, capability, now - timedelta(hours=hours))
    timed_out_trace(project, capability, at=now - timedelta(hours=2))
    passing_traces(project, capability, 4, now - timedelta(hours=2))
    run(project)
    member = User.objects.create_user(email=f"{uuid.uuid4()}@test.com", password="pw")
    ProjectMembership.objects.create(user=member, project=project)
    key, _ = APIToken.create_for_user(member, project=project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key)

    plan = training_plan(project.pk, capability.pk, "7d")
    assert (plan["passing_traces"], plan["failing_traces"]) == (4, 5)
    assert plan["training_rows"] + plan["eval_rows"] == 4
    response = client.get("/api/traces/", {**plan["selection"]["filters"], "all_spans": "false"})
    assert response.data["count"] == 4


def test_only_passing_traces_land_with_a_reference(project, capability):
    now = timezone.now()
    failing_trace(project, capability, now - timedelta(hours=2))
    passing_traces(project, capability, 1, now - timedelta(hours=2))
    Span.objects.filter(project=project, eval_attributes={}).update(
        attributes={"overmind.output.data": "The right answer"}
    )
    run(project)
    spec = {"filters": {"project": str(project.pk), "capability": str(capability.pk)}}

    passing = read_traces(
        project.pk, {**spec, "filters": {**spec["filters"], "outcome": "passing"}}
    )
    failing = read_traces(
        project.pk, {**spec, "filters": {**spec["filters"], "outcome": "failing"}}
    )
    assert passing.rows[0]["expected_output"] == passing.rows[0]["output"]
    assert "expected_output" not in failing.rows[0]


def test_a_simulated_project_finishes_training_without_a_provider(project, capability):
    project.settings = {"simulated": True}
    project.save(update_fields=["settings"])
    dataset = Dataset.objects.create(project=project, name="Passing traces")
    job = FinetuningJob.objects.create(
        project=project, capability=capability, dataset=dataset, name="Run"
    )

    finish_simulated(job)

    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.SUCCEEDED
    assert job.provider == "simulated"
    assert job.name == "Run (simulated)"
    assert job.cost_usd == 0
    with pytest.raises(ValidationError):
        refuse_paid_work(project)


def test_a_trace_names_its_issues_and_a_one_off_is_not_repeating(project, capability):
    now = timezone.now()
    first = failing_trace(project, capability, now - timedelta(hours=2))
    run(project)

    [issue] = trace_issues(project.pk, first.trace_id)
    assert issue["repeating"] is False
    assert issue["span_id"] == first.span_id

    for hours in (3, 4, 5, 6):
        failing_trace(project, capability, now - timedelta(hours=hours))
    run(project)
    assert trace_issues(project.pk, first.trace_id)[0]["repeating"] is True
    assert trace_issues(project.pk, "unknown") == []


def test_time_based_checks_read_only_spans_old_enough(project, capability, monkeypatch):
    seen = []

    class Recorder(SignalRunner):
        async def run_batch(self, ids, configured, subscriptions):
            seen.append({sub.signal_type for sub in subscriptions})
            return await super().run_batch(ids, configured, subscriptions)

    make_span(project, capability, trace_id=uuid.uuid4().hex, status_code=1)
    async_to_sync(Recorder(project.id).run)()
    assert len(seen) == 1
    assert len(seen[0]) > 2

    seen.clear()
    Span.objects.filter(project=project).update(received_at=timezone.now() - timedelta(minutes=20))
    async_to_sync(Recorder(project.id).run)()
    assert seen
    assert all(types <= {"abandoned", "abandoned_session"} for types in seen)


def test_trace_lists_stop_counting_past_the_cap(project, capability, monkeypatch):
    monkeypatch.setattr(config, "COUNT_CAP", 2)
    passing_traces(project, capability, 5, timezone.now() - timedelta(hours=1))
    member = User.objects.create_user(email=f"{uuid.uuid4()}@test.com", password="pw")
    ProjectMembership.objects.create(user=member, project=project)
    key, _ = APIToken.create_for_user(member, project=project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key)

    response = client.get("/api/traces/", {"project": project.pk, "all_spans": "false"})
    assert response.data["count"] == 3


def test_capability_lists_read_trace_counts_from_the_rollup(project, capability):
    passing_traces(project, capability, 3, timezone.now() - timedelta(hours=1))
    run(project)
    member = User.objects.create_user(email=f"{uuid.uuid4()}@test.com", password="pw")
    ProjectMembership.objects.create(user=member, project=project)
    key, _ = APIToken.create_for_user(member, project=project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key)

    response = client.get("/api/capabilities/", {"project": project.pk})
    assert response.data["results"][0]["trace_count"] == 3
