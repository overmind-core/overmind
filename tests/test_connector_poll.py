from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone
from factories import make_connector, sync_until_live
from fakes.vendors import LangfuseAPI, Step

from overbae.models import ConnectorCredential, Span
from overbae.services.connectors.sync import boundary_import_key, enqueue_connector_sync
from overbae.tasks import connector_sync

pytestmark = pytest.mark.django_db


@pytest.fixture
def langfuse(fake_llm, slept) -> LangfuseAPI:
    api = LangfuseAPI()
    fake_llm.network.vendors.append(api)
    return api


@pytest.fixture
def continuations(monkeypatch) -> list:
    queued: list = []
    monkeypatch.setattr(
        connector_sync.sync_connector_chunk,
        "apply_async",
        lambda *a, **k: queued.append(k.get("args", list(a))[0]),
    )
    return queued


@pytest.fixture
def credential(langfuse, continuations) -> ConnectorCredential:
    return make_connector("langfuse", base_url=langfuse.host, api_secret="sk", lookback_days=None)


def _traces(api: LangfuseAPI, count: int, *, day: int = 1, first: int = 0) -> None:
    for number in range(first, first + count):
        start = datetime(2026, 1, day, 0, 0, number, tzinfo=UTC)
        step = Step(f"t{number}", "span", None, start=start, end=start + timedelta(seconds=1))
        api.record(uuid.uuid4().hex, [step])


def _spans(credential) -> int:
    return Span.objects.filter(project=credential.project).count()


def _chunk(credential) -> dict:
    ConnectorCredential.objects.filter(id=credential.id).update(next_poll_at=None)
    return connector_sync.sync_connector_chunk(str(credential.id))


def test_backfill_imports_every_trace_once(credential, langfuse):
    _traces(langfuse, 5)

    cred = sync_until_live(credential)

    assert _spans(credential) == 5
    assert cred.backfill_imported == 5
    assert cred.sync_cursor.get("watermark") == "2026-01-01T00:00:04Z"

    _chunk(credential)
    assert _spans(credential) == 5


def test_live_polling_pulls_only_what_arrived_since_the_watermark(credential, langfuse):
    _traces(langfuse, 3)
    cred = sync_until_live(credential)
    watermark_before = cred.sync_cursor["watermark"]

    _traces(langfuse, 2, first=5)
    _chunk(credential)
    credential.refresh_from_db()

    assert _spans(credential) == 5
    assert credential.sync_cursor["watermark"] > watermark_before


def test_a_paged_live_poll_queues_the_next_chunk(credential, langfuse, continuations):
    _traces(langfuse, 3)
    sync_until_live(credential)
    continuations.clear()
    langfuse.page_size = 1
    _traces(langfuse, 2, first=5)

    result = _chunk(credential)
    credential.refresh_from_db()

    assert result["status"] == "live_continuing"
    assert credential.sync_status == ConnectorCredential.SyncStatus.LIVE
    assert credential.sync_cursor["next_cursor"]
    assert continuations == [str(credential.id)]


def test_an_interrupted_backfill_resumes_without_restarting_or_duplicating(langfuse, continuations):
    credential = make_connector(
        "langfuse",
        base_url=langfuse.host,
        api_secret="sk",
        lookback_days=None,
        backfill_from=datetime(2026, 1, 1, tzinfo=UTC),
        backfill_to=datetime(2026, 1, 3, tzinfo=UTC),
    )
    _traces(langfuse, 2, day=1)
    _traces(langfuse, 3, day=2)

    _chunk(credential)
    credential.refresh_from_db()
    assert credential.sync_status == ConnectorCredential.SyncStatus.BACKFILLING
    assert _spans(credential) == 3
    assert credential.sync_cursor["windows_remaining"] >= 1

    sync_until_live(credential)
    assert _spans(credential) == 5


def test_a_vendor_outage_backs_off_and_imports_nothing(credential, langfuse):
    _traces(langfuse, 3)
    langfuse.status = 500

    _chunk(credential)
    credential.refresh_from_db()

    assert credential.sync_status == ConnectorCredential.SyncStatus.ERROR
    assert credential.sync_retry_count == 1
    assert credential.next_poll_at is not None
    assert _spans(credential) == 0


def test_the_poller_queues_only_due_active_auto_syncing_connectors(credential, continuations):
    ConnectorCredential.objects.filter(id=credential.id).update(auto_sync_enabled=True)
    later = make_connector("langfuse", project=credential.project)
    ConnectorCredential.objects.filter(id=later.id).update(
        auto_sync_enabled=True, next_poll_at=timezone.now() + timedelta(hours=1)
    )
    inactive = make_connector("langfuse", project=credential.project)
    ConnectorCredential.objects.filter(id=inactive.id).update(
        auto_sync_enabled=True, is_active=False
    )
    make_connector("langfuse", project=credential.project)

    result = connector_sync.poll_connectors()

    assert result["enqueued"] == 1
    assert continuations == [str(credential.id)]


def test_a_manual_sync_runs_with_auto_sync_off(credential, langfuse):
    _traces(langfuse, 3)
    assert credential.auto_sync_enabled is False

    cred = sync_until_live(credential)

    assert cred.sync_status == ConnectorCredential.SyncStatus.LIVE
    assert _spans(credential) == 3


def test_an_import_queues_scoring_for_every_trace_it_lands(credential, langfuse, monkeypatch):
    queued = []
    monkeypatch.setattr(
        "overbae.tasks.trace_scoring.score_trace.delay", lambda **kw: queued.append(kw["trace_id"])
    )
    _traces(langfuse, 3)

    sync_until_live(credential)

    roots = Span.objects.filter(project=credential.project, parent_span_id__isnull=True)
    assert sorted(queued) == sorted(roots.values_list("trace_id", flat=True))


def test_a_sync_request_restarts_a_backfill_that_imported_nothing(credential):
    ConnectorCredential.objects.filter(pk=credential.pk).update(
        sync_status=ConnectorCredential.SyncStatus.LIVE,
        sync_cursor={"mode": "live", "watermark": None},
        total_traces_imported=0,
    )
    credential.refresh_from_db()
    enqueue_connector_sync(credential)
    credential.refresh_from_db()
    assert credential.sync_status == ConnectorCredential.SyncStatus.BACKFILLING
    assert credential.sync_cursor == {}


def test_a_sync_request_keeps_the_live_cursor_after_an_import(credential):
    cursor = {"mode": "live", "watermark": "2026-01-01T00:00:00Z"}
    ConnectorCredential.objects.filter(pk=credential.pk).update(
        sync_status=ConnectorCredential.SyncStatus.LIVE,
        sync_cursor=cursor,
        total_traces_imported=4,
    )
    credential.refresh_from_db()
    enqueue_connector_sync(credential)
    credential.refresh_from_db()
    assert credential.sync_status == ConnectorCredential.SyncStatus.LIVE
    assert credential.sync_cursor == cursor


def test_a_sync_request_restarts_the_backfill_when_the_boundaries_changed(credential):
    ConnectorCredential.objects.filter(pk=credential.pk).update(
        sync_status=ConnectorCredential.SyncStatus.LIVE,
        sync_cursor={"mode": "live", "watermark": "2026-01-01T00:00:00Z"},
        total_traces_imported=4,
        capability_mapping={"source": "observation_name", "names": ["analyze_email"]},
        imported_boundary_key=boundary_import_key(
            {"source": "observation_name", "names": ["run_invoice_agent"]}
        ),
    )
    credential.refresh_from_db()
    enqueue_connector_sync(credential)
    credential.refresh_from_db()
    assert credential.sync_status == ConnectorCredential.SyncStatus.BACKFILLING
    assert credential.sync_cursor == {}
