import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone
from mcp_fixtures import call_tool
from rest_framework.test import APIClient

from overbae.innermind.signals import (
    LeakSignal,
    PlanDriftSignal,
    ScoreDropSignal,
    SignalRunner,
    UnscorableSignal,
    projects_with_recent_spans,
)
from overbae.models import (
    APIToken,
    Capability,
    Conversation,
    Project,
    ProjectMembership,
    SignalEvent,
    SignalSubscription,
    Span,
    User,
)
from overbae.services.mcp.context import MCPContext

pytestmark = [pytest.mark.django_db, pytest.mark.covers("metrics-math")]


@pytest.fixture
def context():
    user = User.objects.create_user(email=f"{uuid.uuid4()}@test.com", password="pw")
    project = Project.objects.create(name="Signals", slug=f"signals-{uuid.uuid4()}")
    ProjectMembership.objects.create(user=user, project=project)
    key, token = APIToken.create_for_user(user, project=project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key)
    return MCPContext(user=user, project=project, token=token), client


def call(context, name, arguments):
    result = call_tool(name, arguments, context)
    assert not result.isError, result.content
    return result.structuredContent


def span(project, *, capability=None, conversation=None):
    return Span.objects.create(
        project=project,
        capability=capability,
        conversation=conversation,
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        name="failed step",
        start_time_ns=1,
        status_code=2,
    )


def test_leak_signal_does_not_treat_timing_decimals_as_card_numbers():
    signal = LeakSignal("test")

    def output(value):
        return SimpleNamespace(name="run", attributes={"output": value})

    assert async_to_sync(signal.evaluate)([output('"step_interval": 3.8305869102478027')]) is None
    finding = async_to_sync(signal.evaluate)([output("card 4242 4242 4242 4242")])
    assert finding.message == "sensitive data in outputs: run (card number)"


def test_signal_details_carry_redacted_evidence_and_handoffs():
    signal = LeakSignal("test")
    clean = Span(span_id="a" * 16, trace_id="1" * 32, name="plan", attributes={"output": "ok"})
    leaking = Span(
        span_id="b" * 16,
        trace_id="2" * 32,
        name="run",
        status_message="",
        attributes={"output": "card 4242 4242 4242 4242"},
    )
    group = [clean, leaking]
    finding = async_to_sync(signal.evaluate)(group)
    details = signal.details(finding, group, "conversation:1", capability_id=7)

    assert details["trace_ids"] == [leaking.trace_id]
    assert details["group_trace_ids"] == [clean.trace_id, leaking.trace_id]
    assert details["span_ids"] == [leaking.span_id]
    assert details["measurements"] == {"leaks": {"card number in output": 1}}
    [item] = details["evidence"]
    assert item["reason"] == "card number in output"
    assert "4242" not in item["output"]
    assert "[card number]" in item["output"]
    assert {action["kind"] for action in details["recommended_actions"]} == {
        "fix",
        "inspect_traces",
        "create_dataset",
        "open_optimiser",
    }


def test_unscorable_counts_only_spans_whose_expectations_errored():
    signal = UnscorableSignal("test")

    def scored_span(span_id, metrics):
        return Span(
            span_id=span_id,
            trace_id="1" * 32,
            eval_attributes={"score": None, "metrics": metrics},
        )

    nothing_applied = scored_span("a" * 16, {})
    abstained = scored_span("b" * 16, {"tool ran": {"score": None, "error": ""}})
    assert async_to_sync(signal.evaluate)([nothing_applied, abstained]) is None

    errored = scored_span("c" * 16, {"answers": {"score": None, "error": "judge timed out"}})
    finding = async_to_sync(signal.evaluate)([nothing_applied, abstained, errored])
    assert finding.message == "1 of 2 evaluated spans failed scoring"
    assert finding.spans == [(errored, "judge timed out")]


def test_score_drop_measures_both_windows():
    signal = ScoreDropSignal("test")
    spans = [
        Span(span_id=f"{index:016x}", trace_id="1" * 32, eval_attributes={"score": score})
        for index, score in enumerate([0.9] * 5 + [0.4] * 5)
    ]
    finding = async_to_sync(signal.evaluate)(spans)
    assert finding.message == "mean score fell from 0.90 to 0.40 over the last 5 scored spans"
    assert finding.measurements == {
        "before_mean": 0.9,
        "recent_mean": 0.4,
        "drop": 0.5,
        "before_count": 5,
    }
    assert [span for span, _ in finding.spans] == spans[5:]


def test_defaults_generate_session_evidence_without_subscriptions_or_model_calls(
    context, monkeypatch
):
    ctx, client = context

    def unexpected_judge(*args, **kwargs):
        pytest.fail("Default checks must not call the plan-drift judge")

    monkeypatch.setattr(PlanDriftSignal, "evaluate", unexpected_judge)
    listed = call(ctx, "list_signals", {})
    assert len([s for s in listed["signals"] if s["enabled"]]) == 24
    assert (
        next(s for s in listed["signals"] if s["signal_type"] == "plan_drift")["enabled"] is False
    )
    assert SignalSubscription.objects.count() == 0
    session = Conversation.objects.create(project=ctx.project, external_id="session-one")
    first = span(ctx.project, conversation=session)
    second = span(ctx.project, conversation=session)
    unrelated = span(ctx.project)
    other_project = Project.objects.create(name="Other", slug=f"other-{uuid.uuid4()}")
    other = span(other_project)
    assert ctx.project.id in projects_with_recent_spans()
    async_to_sync(SignalRunner(ctx.project.id).run)()
    async_to_sync(SignalRunner(ctx.project.id).run)()
    event = SignalEvent.objects.get(project=ctx.project, conversation=session, signal_type="errors")
    assert set(event.details["trace_ids"]) == {first.trace_id, second.trace_id}
    assert event.details["measurements"] == {"errored": 2, "spans": 2}
    assert {item["span_id"] for item in event.details["evidence"]} == {
        first.span_id,
        second.span_id,
    }
    assert event.subscription_id is None
    assert not SignalEvent.objects.filter(project=other_project).exists()

    response = client.get(
        "/api/signal-events/",
        {"project": ctx.project.id, "conversation": session.id, "signal_type": "errors"},
    )
    assert response.status_code == 200
    assert response.data["count"] == 1
    response = client.get("/api/traces/", {"project": ctx.project.id, "signal_event": event.id})
    assert response.status_code == 200
    assert {s["trace_id"] for s in response.data["results"]} == {first.trace_id, second.trace_id}
    inspected = call(ctx, "query_traces", {"signal_event": str(event.id)})
    assert {s["trace_id"] for s in inspected["traces"]} == {first.trace_id, second.trace_id}
    foreign_event = SignalEvent.objects.create(
        project=other_project,
        signal_type="errors",
        trace_id=other.trace_id,
        message="Other",
        dedupe_key="other",
        details={"trace_ids": [other.trace_id, unrelated.trace_id]},
    )
    response = client.get(
        "/api/traces/", {"project": ctx.project.id, "signal_event": foreign_event.id}
    )
    assert response.status_code == 200
    assert response.data["count"] == 0
    assert call(ctx, "query_traces", {"signal_event": str(foreign_event.id)})["page"]["total"] == 0


def test_signal_trend_counts_each_day_and_filters_the_list_by_day(context):
    ctx, client = context
    today = timezone.localdate()

    def event(signal_type, days_ago, *, project=None, seen=False):
        created = SignalEvent.objects.create(
            project=project or ctx.project,
            signal_type=signal_type,
            message=signal_type,
            dedupe_key=uuid.uuid4().hex,
            seen_at=timezone.now() if seen else None,
        )
        SignalEvent.objects.filter(pk=created.pk).update(
            occurred_at=timezone.now() - timedelta(days=days_ago)
        )
        return created

    event("errors", 0)
    event("errors", 0)
    yesterday = event("errors", 1, seen=True)
    event("leak", 1)
    event("leak", 20)
    event("errors", 0, project=Project.objects.create(name="Other", slug=f"o-{uuid.uuid4()}"))

    response = client.get("/api/signal-events/trend/", {"project": ctx.project.id, "days": 3})
    assert response.status_code == 200
    assert response.data["days"] == [
        (today - timedelta(days=offset)).isoformat() for offset in (2, 1, 0)
    ]
    assert response.data["series"] == [
        {"signal_type": "errors", "total": 3, "counts": [0, 1, 2]},
        {"signal_type": "leak", "total": 1, "counts": [0, 1, 0]},
    ]
    unseen = client.get(
        "/api/signal-events/trend/", {"project": ctx.project.id, "days": 3, "unseen": "true"}
    )
    assert unseen.data["series"][0] == {"signal_type": "errors", "total": 2, "counts": [0, 0, 2]}

    listed = client.get(
        "/api/signal-events/",
        {
            "project": ctx.project.id,
            "signal_type": "errors",
            "occurred_on": (today - timedelta(days=1)).isoformat(),
        },
    )
    assert [row["id"] for row in listed.data["results"]] == [str(yesterday.id)]


def test_project_and_capability_mutes_override_defaults(context):
    ctx, _ = context
    cap = Capability.objects.create(project=ctx.project, name="Answer", slug="answer")
    scoped = span(ctx.project, capability=cap)
    unscoped = span(ctx.project)
    call(ctx, "set_signal_subscription", {"signal_type": "errors", "enabled": False})
    assert not next(
        s for s in call(ctx, "list_signals", {})["signals"] if s["signal_type"] == "errors"
    )["enabled"]
    async_to_sync(SignalRunner(ctx.project.id).run)()
    assert not SignalEvent.objects.filter(signal_type="errors").exists()
    call(
        ctx,
        "set_signal_subscription",
        {"signal_type": "errors", "capability": "answer", "enabled": True},
    )
    async_to_sync(SignalRunner(ctx.project.id).run)()
    assert list(
        SignalEvent.objects.filter(signal_type="errors").values_list("trace_id", flat=True)
    ) == [scoped.trace_id]
    call(ctx, "set_signal_subscription", {"signal_type": "errors", "enabled": True})
    call(
        ctx,
        "set_signal_subscription",
        {"signal_type": "errors", "capability": "answer", "enabled": False},
    )
    another = span(ctx.project, capability=cap)
    async_to_sync(SignalRunner(ctx.project.id).run)()
    assert set(
        SignalEvent.objects.filter(signal_type="errors").values_list("trace_id", flat=True)
    ) == {scoped.trace_id, unscoped.trace_id}
    assert not SignalEvent.objects.filter(trace_id=another.trace_id, signal_type="errors").exists()
    effective = call(ctx, "list_signals", {"capability": "answer"})
    assert not next(s for s in effective["signals"] if s["signal_type"] == "errors")["enabled"]
    Span.objects.filter(project=ctx.project).update(received_at=timezone.now() - timedelta(days=2))
    assert ctx.project.id not in projects_with_recent_spans()


def test_signal_scan_releases_payloads_between_complete_groups(context):
    import weakref

    from django.db.models.signals import post_init

    ctx, _ = context
    expected_last_trace = {}
    for index in range(4):
        session = Conversation.objects.create(project=ctx.project, external_id=f"bounded-{index}")
        for timestamp in (2, 1):
            item = span(ctx.project, conversation=session)
            if timestamp == 2:
                expected_last_trace[session.id] = item.trace_id
            Span.objects.filter(pk=item.pk).update(
                start_time_ns=timestamp, attributes={"padding": "x" * (2 * 1024 * 1024)}
            )
    alive = []
    peak = 0

    def observe(sender, instance, **kwargs):
        nonlocal peak
        alive.append(weakref.ref(instance))
        peak = max(peak, sum(ref() is not None for ref in alive))

    post_init.connect(observe, sender=Span)
    try:
        async_to_sync(SignalRunner(ctx.project.id).run)()
    finally:
        post_init.disconnect(observe, sender=Span)
    assert peak <= 2
    events = SignalEvent.objects.filter(project=ctx.project, signal_type="errors")
    assert events.count() == 4
    assert all(event.details["spans"] == 2 for event in events)
    assert all(event.trace_id == expected_last_trace[event.conversation_id] for event in events)


def test_signal_scan_reports_oversized_group_without_dropping_other_groups(context):
    from overbae.innermind.signals import SignalInputTooLargeError

    ctx, _ = context
    session = Conversation.objects.create(project=ctx.project, external_id="oversized")
    for _ in range(2):
        item = span(ctx.project, conversation=session)
        Span.objects.filter(pk=item.pk).update(attributes={"padding": "x" * (5 * 1024 * 1024)})
    small = span(ctx.project)
    with pytest.raises(SignalInputTooLargeError, match="1 signal groups exceed"):
        async_to_sync(SignalRunner(ctx.project.id).run)()
    assert not SignalEvent.objects.filter(conversation=session).exists()
    assert SignalEvent.objects.filter(trace_id=small.trace_id, signal_type="errors").exists()


def test_signal_batch_rechecks_bytes_before_materializing_payloads(context):
    from django.db.models.signals import post_init

    from overbae.innermind.signals import SignalInputTooLargeError

    ctx, _ = context
    item = span(ctx.project)
    Span.objects.filter(pk=item.pk).update(attributes={"padding": "x" * (9 * 1024 * 1024)})
    loaded = []

    def observe(sender, instance, **kwargs):
        loaded.append(instance.pk)

    post_init.connect(observe, sender=Span)
    try:
        with pytest.raises(SignalInputTooLargeError, match="changed or exceeded"):
            async_to_sync(SignalRunner(ctx.project.id).run_batch)(
                {item.pk: f"trace:{item.trace_id}"}, [], [], "2026-10-17"
            )
    finally:
        post_init.disconnect(observe, sender=Span)
    assert loaded == []


def test_signal_scan_continues_after_concurrent_payload_growth(context):
    from overbae.innermind.signals import SignalInputTooLargeError

    ctx, _ = context
    items = [span(ctx.project) for _ in range(3)]
    for index, item in enumerate(items):
        Span.objects.filter(pk=item.pk).update(
            start_time_ns=3 - index, attributes={"padding": "x" * (5 * 1024 * 1024)}
        )

    class ConcurrentGrowthRunner(SignalRunner):
        async def batches(self):
            first = True
            async for ids in super().batches():
                if first:
                    await Span.objects.filter(pk=items[0].pk).aupdate(
                        eval_attributes={"padding": "y" * (4 * 1024 * 1024)}
                    )
                    first = False
                yield ids

    with pytest.raises(SignalInputTooLargeError, match="1 signal groups changed"):
        async_to_sync(ConcurrentGrowthRunner(ctx.project.id).run)()
    assert set(
        SignalEvent.objects.filter(signal_type="errors").values_list("trace_id", flat=True)
    ) == {item.trace_id for item in items[1:]}
