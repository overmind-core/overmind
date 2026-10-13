from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import (
    Project,
    ProjectMembership,
    Span,
    User,
)

pytestmark = pytest.mark.django_db

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


def _span(project, *, trace_id, parent=None, capability=None, start_ns=0, attributes=None) -> Span:
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=parent,
        project=project,
        capability=capability,
        start_time_ns=start_ns,
        attributes=attributes or {},
    )


def _age(span: Span, minutes: int) -> None:
    Span.objects.filter(pk=span.pk).update(received_at=timezone.now() - timedelta(minutes=minutes))


def _rows(client, **params) -> list[dict]:
    response = client.get(TRACES_URL, params)
    assert response.status_code == 200
    return response.data["results"]


def test_rootless_trace_listed_live_by_earliest_span():
    client, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    early = _span(project, trace_id=trace_id, parent="a" * 16, start_ns=1)
    _span(project, trace_id=trace_id, parent="a" * 16, start_ns=2)

    rows = _rows(client, project=str(project.id))
    assert [r["trace_id"] for r in rows] == [trace_id]
    assert rows[0]["span_id"] == early.span_id
    assert rows[0]["trace_status"] == "live"


def test_rootless_trace_reads_interrupted_past_settle_window(settings):
    settings.TRACE_SETTLE_SECONDS = 60
    client, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    span = _span(project, trace_id=trace_id, parent="a" * 16)
    _age(span, minutes=5)

    rows = _rows(client, project=str(project.id))
    assert rows[0]["trace_status"] == "interrupted"


def test_root_arrival_completes_trace_and_rekeys_row():
    client, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id, parent="a" * 16, start_ns=5)

    root = _span(project, trace_id=trace_id, start_ns=1)
    rows = _rows(client, project=str(project.id))
    assert rows[0]["span_id"] == root.span_id
    assert rows[0]["trace_status"] == "completed"


def test_all_spans_false_lists_same_heads():
    client, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id, parent="a" * 16)

    rows = _rows(client, project=str(project.id), all_spans="false")
    assert [r["trace_id"] for r in rows] == [trace_id]


def _statuses_on_every_read_path(client, project, trace_id) -> set[str]:
    statuses = {
        r["trace_status"]
        for r in _rows(client, project=str(project.id))
        if r["trace_id"] == trace_id
    }
    all_span_rows = _rows(client, project=str(project.id), all_spans="true")
    statuses |= {r["trace_status"] for r in all_span_rows if r["trace_id"] == trace_id}
    detail = client.get(f"{TRACES_URL}{trace_id}/")
    assert detail.status_code == 200
    statuses.add(detail.data["trace_status"])
    return statuses


def test_rooted_trace_reads_completed_on_every_path():
    client, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    root = _span(project, trace_id=trace_id, start_ns=1)
    _span(project, trace_id=trace_id, parent=root.span_id, start_ns=2)
    _span(project, trace_id=trace_id, parent=root.span_id, start_ns=3)

    assert _statuses_on_every_read_path(client, project, trace_id) == {"completed"}


def test_rootless_fresh_trace_reads_live_on_every_path():
    client, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id, parent="a" * 16, start_ns=1)
    _span(project, trace_id=trace_id, parent="a" * 16, start_ns=2)

    assert _statuses_on_every_read_path(client, project, trace_id) == {"live"}


def test_rootless_stale_trace_reads_interrupted_on_every_path(settings):
    settings.TRACE_SETTLE_SECONDS = 60
    client, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    for span in (
        _span(project, trace_id=trace_id, parent="a" * 16, start_ns=1),
        _span(project, trace_id=trace_id, parent="a" * 16, start_ns=2),
    ):
        _age(span, minutes=5)

    assert _statuses_on_every_read_path(client, project, trace_id) == {"interrupted"}


def test_list_page_batches_status_into_one_query(django_assert_num_queries):
    from overbae.api.serializers import RootSpanListSerializer

    _, project = _client_and_project()
    spans = []
    for _ in range(3):
        trace_id = uuid.uuid4().hex[:32]
        root = _span(project, trace_id=trace_id, start_ns=1)
        spans += [root, _span(project, trace_id=trace_id, parent=root.span_id, start_ns=2)]

    with django_assert_num_queries(1):
        rows = RootSpanListSerializer(spans, many=True).data
    assert {r["trace_status"] for r in rows} == {"completed"}


def test_serializer_derives_status_without_any_view_context():
    from overbae.api.serializers import RootSpanListSerializer

    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    root = _span(project, trace_id=trace_id, start_ns=1)
    child = _span(project, trace_id=trace_id, parent=root.span_id, start_ns=2)

    assert RootSpanListSerializer(child).data["trace_status"] == "completed"


def test_has_model_filter_matches_rootless_trace():
    client, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id, parent="a" * 16, start_ns=1)
    _span(
        project,
        trace_id=trace_id,
        parent="a" * 16,
        start_ns=2,
        attributes={"genai.model": "gpt-5-nano"},
    )

    rows = _rows(client, project=str(project.id), has_model="true")
    assert [r["trace_id"] for r in rows] == [trace_id]


def test_trace_detail_reports_status():
    client, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id, parent="a" * 16)

    response = client.get(f"{TRACES_URL}{trace_id}/")
    assert response.status_code == 200
    assert response.data["trace_status"] == "live"

    _span(project, trace_id=trace_id)
    response = client.get(f"{TRACES_URL}{trace_id}/")
    assert response.data["trace_status"] == "completed"
