import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from overbae.innermind.signals import (
    LeakSignal,
    PlanDriftSignal,
    SignalRunner,
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
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)


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
    result = asyncio.run(CATALOG.call(name, arguments, context))
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

    assert asyncio.run(signal.check([output('"step_interval": 3.8305869102478027')])) is None
    assert asyncio.run(signal.check([output("card 4242 4242 4242 4242")])) == (
        "sensitive data in outputs: run (card number)"
    )


def test_defaults_generate_session_evidence_without_subscriptions_or_model_calls(
    context, monkeypatch
):
    ctx, client = context

    def unexpected_judge(*args, **kwargs):
        pytest.fail("Default checks must not call the plan-drift judge")

    monkeypatch.setattr(PlanDriftSignal, "check", unexpected_judge)
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
    asyncio.run(SignalRunner(ctx.project.id).run())
    asyncio.run(SignalRunner(ctx.project.id).run())
    event = SignalEvent.objects.get(project=ctx.project, conversation=session, signal_type="errors")
    assert set(event.details["trace_ids"]) == {first.trace_id, second.trace_id}
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


def test_project_and_capability_mutes_override_defaults(context):
    ctx, _ = context
    cap = Capability.objects.create(project=ctx.project, name="Answer", slug="answer")
    scoped = span(ctx.project, capability=cap)
    unscoped = span(ctx.project)
    call(ctx, "set_signal_subscription", {"signal_type": "errors", "enabled": False})
    assert not next(
        s for s in call(ctx, "list_signals", {})["signals"] if s["signal_type"] == "errors"
    )["enabled"]
    asyncio.run(SignalRunner(ctx.project.id).run())
    assert not SignalEvent.objects.filter(signal_type="errors").exists()
    call(
        ctx,
        "set_signal_subscription",
        {"signal_type": "errors", "capability": "answer", "enabled": True},
    )
    asyncio.run(SignalRunner(ctx.project.id).run())
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
    asyncio.run(SignalRunner(ctx.project.id).run())
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
        asyncio.run(SignalRunner(ctx.project.id).run())
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
        asyncio.run(SignalRunner(ctx.project.id).run())
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
            asyncio.run(
                SignalRunner(ctx.project.id).run_batch(
                    {item.pk: f"trace:{item.trace_id}"}, [], [], "2026-10-17"
                )
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
        asyncio.run(ConcurrentGrowthRunner(ctx.project.id).run())
    assert set(
        SignalEvent.objects.filter(signal_type="errors").values_list("trace_id", flat=True)
    ) == {item.trace_id for item in items[1:]}
