"""Sessions — traces grouped under one ``conversation.id``.

Covers the three seams of the feature:

1. Ingest: ``process_span`` resolves the ``conversation.id`` span attribute
   into a project-scoped :class:`Conversation` and stamps the span FK.
2. API: ``GET /api/sessions/`` aggregates (trace/span counts, activity window,
   tokens/cost) and the ``?capability=`` / ``?session=`` filters.
3. Connectors: LangFuse ``sessionId`` / LangSmith ``session_name`` map onto
   the canonical ``conversation.id`` attribute and resolve to a session.
"""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.api.otlp import process_span
from overbae.models import Capability, Conversation, Project, ProjectMembership, Span, User
from overbae.tasks.connector_sync import _upsert_spans

pytestmark = pytest.mark.django_db

SESSIONS_URL = "/api/sessions/"
TRACES_URL = "/api/traces/"


def _client_and_project() -> tuple[APIClient, Project]:
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client, project


def _span(
    project, *, trace_id=None, parent=None, attributes=None, capability=None, **kwargs
) -> Span:
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id or uuid.uuid4().hex[:32],
        parent_span_id=parent,
        project=project,
        attributes=attributes or {},
        capability=capability,
        **kwargs,
    )


def test_process_span_creates_and_links_session():
    _, project = _client_and_project()
    span = _span(project, attributes={"conversation.id": "conv-abc"})

    process_span(span.span_id, str(project.id))

    span.refresh_from_db()
    conversation = Conversation.objects.get(project=project, external_id="conv-abc")
    assert span.conversation_id == conversation.id


def test_process_span_groups_multiple_traces_under_one_session():
    _, project = _client_and_project()
    spans = [_span(project, attributes={"conversation.id": "conv-multi"}) for _ in range(3)]
    for span in spans:
        process_span(span.span_id, str(project.id))

    assert Conversation.objects.filter(project=project, external_id="conv-multi").count() == 1
    conversation = Conversation.objects.get(project=project, external_id="conv-multi")
    assert conversation.spans.count() == 3
    assert conversation.spans.values("trace_id").distinct().count() == 3


def test_process_span_accepts_legacy_namespaced_key():
    _, project = _client_and_project()
    span = _span(project, attributes={"overmind.conversation.id": "conv-legacy"})

    process_span(span.span_id, str(project.id))

    span.refresh_from_db()
    assert span.conversation.external_id == "conv-legacy"


def test_process_span_without_conversation_id_leaves_fk_null():
    _, project = _client_and_project()
    span = _span(project, attributes={"genai.total_tokens": 5})

    process_span(span.span_id, str(project.id))

    span.refresh_from_db()
    assert span.conversation_id is None
    assert Conversation.objects.filter(project=project).count() == 0


def test_process_span_sets_first_capability_as_convenience_pointer():
    _, project = _client_and_project()
    bot = Capability.objects.create(project=project, name="Support Bot", slug="support-bot")
    span = _span(
        project,
        attributes={"conversation.id": "conv-agented", "overmind.capability.id": str(bot.id)},
    )
    process_span(span.span_id, str(project.id))

    conversation = Conversation.objects.get(project=project, external_id="conv-agented")
    assert conversation.capability is not None
    assert conversation.capability.name == "Support Bot"


def test_sessions_scoped_per_project():
    _, project_a = _client_and_project()
    _, project_b = _client_and_project()
    span_a = _span(project_a, attributes={"conversation.id": "shared-id"})
    span_b = _span(project_b, attributes={"conversation.id": "shared-id"})
    process_span(span_a.span_id, str(project_a.id))
    process_span(span_b.span_id, str(project_b.id))

    assert Conversation.objects.filter(external_id="shared-id").count() == 2


def _session_with_traces(project, external_id: str, *, capability=None) -> Conversation:
    """Two traces (root + child each) linked to one session, with usage attrs."""
    conversation = Conversation.objects.create(
        project=project, external_id=external_id, capability=capability
    )
    for i in range(2):
        trace_id = uuid.uuid4().hex[:32]
        _span(
            project,
            trace_id=trace_id,
            capability=capability,
            conversation=conversation,
            start_time_ns=1_000 + i,
            end_time_ns=2_000 + i,
        )
        _span(
            project,
            trace_id=trace_id,
            parent="a" * 16,
            conversation=conversation,
            attributes={"genai.total_tokens": 10, "genai.cost": 0.01},
            start_time_ns=1_500 + i,
            end_time_ns=1_900 + i,
        )
    return conversation


def test_sessions_list_aggregates():
    client, project = _client_and_project()
    conversation = _session_with_traces(project, "conv-agg")

    res = client.get(SESSIONS_URL)
    assert res.status_code == 200
    row = next(r for r in res.json()["results"] if r["id"] == str(conversation.id))
    assert row["external_id"] == "conv-agg"
    assert row["trace_count"] == 2
    assert row["span_count"] == 4
    assert row["first_span_ns"] == 1_000
    assert row["last_span_ns"] == 2_001
    assert row["total_tokens"] == 20
    assert row["total_cost"] == 0.02


def test_sessions_list_reports_the_ledger_session_score():
    from django.utils import timezone

    from overbae.models import TaskExecution

    client, project = _client_and_project()
    scored = _session_with_traces(project, "conv-scored")
    unscored = _session_with_traces(project, "conv-unscored")
    TaskExecution.objects.create(
        project=project,
        trace_id=uuid.uuid4().hex,
        unit_span_id=uuid.uuid4().hex[:16],
        conversation_id="conv-scored",
        started_at=timezone.now(),
        session_score=0.3,
    )

    res = client.get(SESSIONS_URL)
    assert res.status_code == 200
    rows = {r["id"]: r for r in res.json()["results"]}
    assert rows[str(scored.id)]["session_score"] == 0.3
    assert rows[str(unscored.id)]["session_score"] is None


def test_sessions_list_orders_by_total_tokens():
    client, project = _client_and_project()
    low = _session_with_traces(project, "conv-low")
    high = Conversation.objects.create(project=project, external_id="conv-high")
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id, conversation=high, start_time_ns=1, end_time_ns=2)
    _span(
        project,
        trace_id=trace_id,
        parent="a" * 16,
        conversation=high,
        attributes={"genai.total_tokens": 500},
        start_time_ns=1,
        end_time_ns=2,
    )

    res = client.get(SESSIONS_URL, {"ordering": "-total_tokens", "project": project.id})
    assert res.status_code == 200
    ids = [r["id"] for r in res.json()["results"] if r["id"] in {str(low.id), str(high.id)}]
    assert ids == [str(high.id), str(low.id)]


def test_sessions_list_capability_filter_matches_span_attribution():
    client, project = _client_and_project()
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    with_capability = _session_with_traces(project, "conv-with-capability", capability=capability)
    without_capability = _session_with_traces(project, "conv-no-capability")

    res = client.get(SESSIONS_URL, {"capability": str(capability.id)})
    assert res.status_code == 200
    ids = {r["id"] for r in res.json()["results"]}
    assert str(with_capability.id) in ids
    assert str(without_capability.id) not in ids


def test_sessions_list_capability_filter_keeps_project_wide_counts():
    """Filtering by capability must not shrink the session's aggregates."""
    client, project = _client_and_project()
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    conversation = _session_with_traces(project, "conv-mixed", capability=capability)
    # One extra capability-less trace in the same session.
    _span(project, conversation=conversation, start_time_ns=5_000, end_time_ns=6_000)

    res = client.get(SESSIONS_URL, {"capability": str(capability.id)})
    row = next(r for r in res.json()["results"] if r["id"] == str(conversation.id))
    assert row["trace_count"] == 3
    assert row["span_count"] == 5


def test_sessions_invisible_across_projects():
    client, project = _client_and_project()
    _session_with_traces(project, "conv-mine")
    _, other_project = _client_and_project()
    other = _session_with_traces(other_project, "conv-theirs")

    res = client.get(SESSIONS_URL)
    ids = {r["id"] for r in res.json()["results"]}
    assert str(other.id) not in ids


def test_session_retrieve_returns_aggregates():
    client, project = _client_and_project()
    conversation = _session_with_traces(project, "conv-detail")

    res = client.get(f"{SESSIONS_URL}{conversation.id}/")
    assert res.status_code == 200
    body = res.json()
    assert body["trace_count"] == 2
    assert body["total_tokens"] == 20


def test_traces_list_filters_by_session():
    client, project = _client_and_project()
    conversation = _session_with_traces(project, "conv-traces")
    _span(project)  # unrelated root span

    res = client.get(TRACES_URL, {"session": str(conversation.id)})
    assert res.status_code == 200
    rows = res.json()["results"]
    assert len(rows) == 2
    assert all(r["conversation"] == str(conversation.id) for r in rows)


def _langfuse_span_dicts(project, *, n=1, session_id="lf-sess"):
    from types import SimpleNamespace

    from overbae.services.connectors.langfuse.client import LangFuseObservation
    from overbae.services.connectors.langfuse.mapping import LANGFUSE
    from overbae.services.connectors.mapping import observations_to_span_dicts

    cred = SimpleNamespace(
        id="22222222-2222-2222-2222-222222222222",
        name="LF",
        project=project,
        capability_mapping={},
    )
    out = []
    for i in range(n):
        obs = LangFuseObservation(
            id=f"lf-{i}",
            trace_id=f"lf-{i}",
            parent_observation_id=None,
            type="SPAN",
            name=f"t{i}",
            start_time="2026-07-01T00:00:00Z",
            end_time=None,
            session_id=session_id,
            is_root_observation=True,
        )
        out.extend(observations_to_span_dicts([obs], credential=cred, conventions=LANGFUSE))
    return out


def test_langfuse_trace_maps_session_id_to_conversation_attr():
    _, project = _client_and_project()
    span_dict = _langfuse_span_dicts(project, n=1, session_id="lf-session-1")[0]
    assert span_dict["attributes"]["conversation.id"] == "lf-session-1"
    assert span_dict["service_name"].startswith("langfuse/")


def test_upsert_spans_resolves_and_stamps_sessions():
    _, project = _client_and_project()
    span_dicts = _langfuse_span_dicts(project, n=2, session_id="lf-sess")
    created = _upsert_spans(project, span_dicts)
    assert created == 2

    conversation = Conversation.objects.get(project=project, external_id="lf-sess")
    assert conversation.spans.count() == 2
