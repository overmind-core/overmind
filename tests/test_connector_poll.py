"""Background connector polling via the adapter protocol.

Fakes the Langfuse HTTP client so no real provider is contacted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from overbae.models import ConnectorCredential, Project, Span
from overbae.services.connectors.base import Page
from overbae.services.connectors.langfuse.adapter import LangfuseAdapter
from overbae.services.connectors.langfuse.client import LangFuseError, LangFuseObservation
from overbae.services.connectors.sync import boundary_import_key, enqueue_connector_sync
from overbae.tasks import connector_sync

pytestmark = pytest.mark.django_db


class FakeLangfuseClient:
    """Serves observation trees newest-first from an in-memory trace list."""

    def __init__(self, traces: list[dict]):
        self.traces = traces
        self.fail = False
        self._api_version = "v1"

    def add(self, trace: dict) -> None:
        self.traces.append(trace)

    def probe_capabilities(self):
        return "v1"

    @property
    def api_version(self):
        return self._api_version

    def iter_ingest_units(
        self,
        *,
        window_from=None,
        window_to=None,
        lookback_days=None,
        windows=None,
    ):
        if self.fail:
            raise LangFuseError("LangFuse API error 500: boom")
        items = sorted(self.traces, key=lambda t: t["timestamp"], reverse=True)
        bounds = windows or []
        if not bounds and (window_from is not None or window_to is not None):
            from overbae.services.connectors.windows import TimeWindow

            bounds = [
                TimeWindow(
                    start=window_from or datetime(1970, 1, 1, tzinfo=UTC),
                    end=window_to or datetime.now(UTC),
                )
            ]
        for t in items:
            ts = t["timestamp"]
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if bounds and not any(
                (b.start is None or getattr(b.start, "year", 1) <= 1 or dt >= b.start)
                and (b.end is None or dt < b.end)
                for b in bounds
            ):
                continue
            yield [
                LangFuseObservation(
                    id=t["id"],
                    trace_id=t["id"],
                    parent_observation_id=None,
                    type="SPAN",
                    name=t.get("name"),
                    start_time=ts,
                    end_time=None,
                    latency=t.get("latency"),
                    is_root_observation=True,
                )
            ]


@pytest.fixture
def credential(db):
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    return ConnectorCredential.objects.create(
        project=project,
        name="LF",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key="pk",
        api_secret="sk",
        api_version="v1",
    )


@pytest.fixture
def fake_langfuse(monkeypatch):
    fake = FakeLangfuseClient([])

    def _init(self, credential):
        self.credential = credential
        self._client = fake

    monkeypatch.setattr(LangfuseAdapter, "__init__", _init)
    monkeypatch.setattr(
        "overbae.services.connectors.langfuse.adapter.plan_windows",
        lambda window_from, window_to, **kw: [
            __import__("overbae.services.connectors.windows", fromlist=["TimeWindow"]).TimeWindow(
                start=datetime(2026, 1, 1, tzinfo=UTC),
                end=window_to or datetime.now(UTC),
            )
        ],
    )
    monkeypatch.setattr(connector_sync.sync_connector_chunk, "apply_async", lambda *a, **k: None)
    return fake


def _make_traces(n: int, *, start_sec: int = 0) -> list[dict]:
    return [
        {
            "id": f"trace-{i}",
            "timestamp": f"2026-01-01T00:00:{start_sec + i:02d}.000Z",
            "name": f"t{i}",
            "latency": 0.1,
        }
        for i in range(n)
    ]


def _drive_to_live(cred_id, *, max_iters: int = 50) -> ConnectorCredential:
    for _ in range(max_iters):
        ConnectorCredential.objects.filter(id=cred_id).update(next_poll_at=None)
        connector_sync.sync_connector_chunk(str(cred_id))
        cred = ConnectorCredential.objects.get(id=cred_id)
        if cred.sync_status == ConnectorCredential.SyncStatus.LIVE:
            return cred
    raise AssertionError("backfill never reached LIVE")


def test_backfill_paginates_and_is_idempotent(credential, fake_langfuse):
    fake_langfuse.traces = _make_traces(5)

    cred = _drive_to_live(credential.id)

    assert Span.objects.filter(project=credential.project).count() == 5
    assert cred.sync_status == ConnectorCredential.SyncStatus.LIVE
    assert cred.backfill_imported == 5
    assert cred.sync_cursor.get("watermark") == "2026-01-01T00:00:04.000Z"

    ConnectorCredential.objects.filter(id=cred.id).update(next_poll_at=None)
    connector_sync.sync_connector_chunk(str(cred.id))
    assert Span.objects.filter(project=credential.project).count() == 5


def test_watermark_advances_and_live_pulls_only_new(credential, fake_langfuse):
    fake_langfuse.traces = _make_traces(3)

    cred = _drive_to_live(credential.id)
    assert Span.objects.filter(project=credential.project).count() == 3
    watermark_before = cred.sync_cursor["watermark"]

    fake_langfuse.add(
        {"id": "trace-new-a", "timestamp": "2026-01-01T00:00:05.000Z", "name": "na", "latency": 0}
    )
    fake_langfuse.add(
        {"id": "trace-new-b", "timestamp": "2026-01-01T00:00:06.000Z", "name": "nb", "latency": 0}
    )

    ConnectorCredential.objects.filter(id=cred.id).update(next_poll_at=None)
    connector_sync.sync_connector_chunk(str(cred.id))
    cred.refresh_from_db()

    assert Span.objects.filter(project=credential.project).count() == 5
    assert cred.sync_cursor["watermark"] > watermark_before


def test_live_page_continuation_enqueues_another_chunk(credential, fake_langfuse, monkeypatch):
    enqueued = []
    monkeypatch.setattr(
        connector_sync.sync_connector_chunk,
        "apply_async",
        lambda *args, **kwargs: enqueued.append((args, kwargs)),
    )
    monkeypatch.setattr(
        LangfuseAdapter,
        "fetch_page",
        lambda _self, _state: Page(
            units=[],
            next_state={"mode": "live", "live_next_token": 25},
            done=False,
            mode="live",
        ),
    )

    result = connector_sync.sync_connector_chunk(str(credential.id))
    credential.refresh_from_db()

    assert result["status"] == "live_continuing"
    assert credential.sync_status == ConnectorCredential.SyncStatus.LIVE
    assert credential.sync_cursor == {"mode": "live", "live_next_token": 25}
    assert enqueued


def test_backfill_resumes_from_checkpoint_after_interruption(
    credential, fake_langfuse, monkeypatch
):
    """A mid-backfill cursor resumes without restarting or duplicating."""
    from overbae.services.connectors.windows import TimeWindow

    fake_langfuse.traces = _make_traces(5)

    mid = datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC)
    end = datetime.now(UTC)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    windows = [TimeWindow(start=mid, end=end), TimeWindow(start=start, end=mid)]

    monkeypatch.setattr(
        "overbae.services.connectors.langfuse.adapter.plan_windows",
        lambda *a, **k: list(windows),
    )

    connector_sync.sync_connector_chunk(str(credential.id))
    cred = ConnectorCredential.objects.get(id=credential.id)
    assert cred.sync_status == ConnectorCredential.SyncStatus.BACKFILLING
    partial = Span.objects.filter(project=credential.project).count()
    assert 0 < partial < 5
    assert cred.sync_cursor.get("windows_remaining", 0) >= 1

    cred = _drive_to_live(credential.id)
    assert cred.sync_status == ConnectorCredential.SyncStatus.LIVE
    assert Span.objects.filter(project=credential.project).count() == 5


def test_http_error_triggers_backoff(credential, fake_langfuse):
    fake_langfuse.traces = _make_traces(3)
    fake_langfuse.fail = True

    connector_sync.sync_connector_chunk(str(credential.id))
    cred = ConnectorCredential.objects.get(id=credential.id)

    assert cred.sync_status == ConnectorCredential.SyncStatus.ERROR
    assert cred.sync_retry_count == 1
    assert cred.next_poll_at is not None
    assert Span.objects.filter(project=credential.project).count() == 0


def test_poll_connectors_enqueues_only_due_active(credential, monkeypatch):
    enqueued: list = []
    monkeypatch.setattr(
        connector_sync.sync_connector_chunk,
        "apply_async",
        lambda *a, **k: enqueued.append(k.get("args", list(a))[0]),
    )
    from datetime import timedelta

    from django.utils import timezone

    ConnectorCredential.objects.filter(id=credential.id).update(auto_sync_enabled=True)
    gated = ConnectorCredential.objects.create(
        project=credential.project,
        name="gated",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key="pk",
        api_secret="sk",
        auto_sync_enabled=True,
        next_poll_at=timezone.now() + timedelta(hours=1),
    )
    ConnectorCredential.objects.create(
        project=credential.project,
        name="off",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key="pk",
        api_secret="sk",
        auto_sync_enabled=True,
        is_active=False,
    )

    result = connector_sync.poll_connectors()

    assert result["enqueued"] == 1
    assert enqueued == [str(credential.id)]
    assert str(gated.id) not in enqueued


def test_poll_connectors_skips_auto_sync_disabled(credential, monkeypatch):
    enqueued: list = []
    monkeypatch.setattr(
        connector_sync.sync_connector_chunk,
        "apply_async",
        lambda *a, **k: enqueued.append(k.get("args", list(a))[0]),
    )

    enabled = ConnectorCredential.objects.create(
        project=credential.project,
        name="enabled",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key="pk",
        api_secret="sk",
        auto_sync_enabled=True,
    )

    result = connector_sync.poll_connectors()

    assert result["enqueued"] == 1
    assert enqueued == [str(enabled.id)]
    assert str(credential.id) not in enqueued


def test_manual_sync_works_when_auto_sync_disabled(credential, fake_langfuse):
    fake_langfuse.traces = _make_traces(3)
    assert credential.auto_sync_enabled is False

    cred = _drive_to_live(credential.id)

    assert cred.sync_status == ConnectorCredential.SyncStatus.LIVE
    assert Span.objects.filter(project=credential.project).count() == 3


def test_import_enqueues_trace_scoring(credential, fake_langfuse, monkeypatch):
    # bulk_create fires no signals and the beat sweep only looks back 2h, so
    # without this enqueue an imported trace is never scored.
    enqueued = []
    monkeypatch.setattr(
        "overbae.tasks.trace_scoring.score_trace.delay",
        lambda **kw: enqueued.append(kw["trace_id"]),
    )
    fake_langfuse.traces = _make_traces(3)

    _drive_to_live(credential.id)

    roots = Span.objects.filter(project=credential.project, parent_span_id__isnull=True)
    assert sorted(enqueued) == sorted(roots.values_list("trace_id", flat=True))


def test_enqueue_connector_sync_restarts_backfill_when_live_imported_nothing(
    credential, monkeypatch
):
    monkeypatch.setattr(connector_sync.sync_connector_chunk, "apply_async", lambda *a, **k: None)
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


def test_enqueue_connector_sync_keeps_live_cursor_after_import(credential, monkeypatch):
    monkeypatch.setattr(connector_sync.sync_connector_chunk, "apply_async", lambda *a, **k: None)
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


def test_enqueue_connector_sync_restarts_backfill_when_boundaries_changed(credential, monkeypatch):
    monkeypatch.setattr(connector_sync.sync_connector_chunk, "apply_async", lambda *a, **k: None)
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
