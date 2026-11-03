from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from factories import make_connector, make_project, sync_until_live
from fakes.vendors import LangfuseAPI, Step

from overbae.api.serializers import ConnectorCredentialSerializer
from overbae.models import ConnectorCredential, Span
from overbae.services.connectors.feedforward import save_sync_config
from overbae.services.connectors.progress import checkpoint_progress, sync_progress
from overbae.services.connectors.sync import (
    boundary_import_key,
    enqueue_connector_sync,
    reset_connector_import,
)
from overbae.services.mcp.resources import connector_resource_payload
from overbae.tasks import connector_sync

pytestmark = [pytest.mark.django_db, pytest.mark.covers("vendor-connectors")]


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


def test_generations_carry_a_cost_reported_or_priced(credential, langfuse):
    for second, cost in ((0, None), (1, 0.42)):
        start = datetime(2026, 1, 1, 0, 0, second, tzinfo=UTC)
        end = start + timedelta(seconds=1)
        step = Step(
            "answer", "generation", None, tokens=(1000, 500), start=start, end=end, cost=cost
        )
        langfuse.record(uuid.uuid4().hex, [step])

    sync_until_live(credential)

    costs = sorted(
        span.usage["genai.cost"] for span in Span.objects.filter(project=credential.project)
    )
    # gpt-4.1-mini: 1000 in x $0.40/M + 500 out x $1.60/M
    assert costs == [pytest.approx(0.0012), 0.42]


def test_live_polling_pulls_only_what_arrived_since_the_watermark(credential, langfuse):
    _traces(langfuse, 3)
    cred = sync_until_live(credential)
    watermark_before = cred.sync_cursor["watermark"]

    _traces(langfuse, 2, first=5)
    _chunk(credential)
    credential.refresh_from_db()

    assert _spans(credential) == 5
    assert credential.sync_cursor["watermark"] > watermark_before


def test_a_trace_that_appears_after_its_poll_window_is_still_imported(credential, langfuse):
    credential = sync_until_live(credential)
    watermark = timezone.now()
    ConnectorCredential.objects.filter(pk=credential.pk).update(
        sync_cursor={"mode": "live", "watermark": watermark.isoformat()}
    )
    start = watermark - timedelta(minutes=10)
    late = Step("late", "span", None, start=start, end=start + timedelta(seconds=1))
    langfuse.record(uuid.uuid4().hex, [late])
    _chunk(credential)
    assert Span.objects.filter(project=credential.project, name="late").count() == 1


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


def test_the_poller_leaves_a_running_chunk_alone_until_its_time_limit_has_passed(
    credential, langfuse, continuations, monkeypatch
):
    _traces(langfuse, 1)
    ConnectorCredential.objects.filter(id=credential.id).update(auto_sync_enabled=True)
    langfuse.status = 429
    still_running = timedelta(seconds=connector_sync.sync_connector_chunk.time_limit - 60)
    polled: list[int] = []

    def vendor_backoff(_seconds):
        if polled:
            return
        langfuse.status = 200
        with time_machine.travel(timezone.now() + still_running):
            polled.append(connector_sync.poll_connectors()["enqueued"])

    monkeypatch.setattr(time, "sleep", vendor_backoff)
    _chunk(credential)

    assert polled == [0]
    assert _spans(credential) == 1


def test_a_manual_sync_runs_with_auto_sync_off(credential, langfuse):
    _traces(langfuse, 3)
    assert credential.auto_sync_enabled is False

    cred = sync_until_live(credential)

    assert cred.sync_status == ConnectorCredential.SyncStatus.LIVE
    assert _spans(credential) == 3


def test_an_import_queues_scoring_for_every_span_it_lands(credential, langfuse):
    from overbae.models import LiveScoringWork

    _traces(langfuse, 3)
    sync_until_live(credential)
    landed = Span.objects.filter(project=credential.project)
    queued = LiveScoringWork.objects.filter(project=credential.project, kind="span")
    assert sorted(queued.values_list("key", flat=True)) == sorted(
        landed.values_list("span_id", flat=True)
    )


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


def test_split_trace_pages_and_retries_do_not_inflate_import_counts(credential, langfuse):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    root = Step("root", "span", None, start=start, end=start + timedelta(seconds=2))
    child = Step(
        "child",
        "span",
        root.id,
        start=start + timedelta(seconds=1),
        end=start + timedelta(seconds=2),
    )
    langfuse.record(uuid.uuid4().hex, [root, child])
    langfuse.page_size = 1
    cred = sync_until_live(credential)
    assert cred.total_traces_imported == 1
    assert cred.total_spans_imported == 2
    _chunk(credential)
    credential.refresh_from_db()
    assert credential.total_traces_imported == 1
    assert credential.total_spans_imported == 2


def test_all_history_clears_the_previous_lookback(credential):
    save_sync_config(credential, lookback_days=1)
    assert save_sync_config(credential).lookback_days == 1
    assert save_sync_config(credential, lookback_days=None).lookback_days is None


def test_manual_import_resumes_after_provider_throttling(credential, langfuse, continuations):
    langfuse.status = 429
    result = _chunk(credential)
    assert result["status"] == "error"
    assert continuations == [str(credential.id)]
    credential.refresh_from_db()
    assert credential.auto_sync_enabled is False


def test_source_count_and_eta_include_waits_and_survive_page_resume(
    credential, langfuse, time_machine
):
    clock = time_machine
    clock.move_to("2026-01-03T00:00:00Z", tick=False)
    _traces(langfuse, 6)
    langfuse.page_size = 2
    _chunk(credential)
    credential.refresh_from_db()
    first = sync_progress(credential)
    assert first.total_estimate == 6
    assert first.observations_total == 6
    assert first.imported == 2
    clock.shift(60)
    _chunk(credential)
    credential.refresh_from_db()
    progress = sync_progress(credential)
    assert progress.imported == 4
    assert progress.estimated_seconds_remaining == 30
    assert len([call for call in langfuse.requests if "/v2/metrics" in call.url]) == 1
    # Status polling never calls the provider and doesn't count lease expiry as a retry.
    assert progress.retry_at is None
    clock.shift(301)
    assert sync_progress(credential).estimated_seconds_remaining is None
    assert sync_progress(credential).state == "waiting"
    _chunk(credential)
    credential.refresh_from_db()
    progress = sync_progress(credential)
    assert progress.state == "complete"
    assert progress.imported == 6
    assert progress.estimated_seconds_remaining == 0


def test_source_count_persists_when_a_data_page_is_throttled(credential, langfuse):
    _traces(langfuse, 3)
    langfuse.page_size = 1
    _chunk(credential)
    credential.refresh_from_db()
    total = sync_progress(credential).total_estimate
    langfuse.status = 429
    _chunk(credential)
    credential.refresh_from_db()
    progress = sync_progress(credential)
    assert progress.total_estimate == total == 3
    assert progress.imported == 1
    assert progress.state == "waiting"
    assert progress.estimated_seconds_remaining is None
    assert progress.retry_at is not None


def test_unavailable_count_does_not_block_import_and_is_retried(credential, langfuse, time_machine):
    time_machine.move_to("2026-01-03T00:00:00Z", tick=False)
    _traces(langfuse, 4)
    langfuse.page_size = 1
    langfuse.metrics_status = 429
    _chunk(credential)
    credential.refresh_from_db()
    assert sync_progress(credential).total_estimate is None
    assert sync_progress(credential).imported == 1
    time_machine.shift(301)
    langfuse.metrics_status = 200
    _chunk(credential)
    credential.refresh_from_db()
    progress = ConnectorCredentialSerializer(credential).data["sync_progress"]
    assert progress["total_estimate"] == 4
    assert progress["imported"] == 2
    assert progress["estimated_seconds_remaining"] == 301
    resource = connector_resource_payload(
        credential.project, credential, "overmind://connectors/test"
    )
    assert resource["sync"]["progress"] == progress


def test_empty_backfill_reports_completion_instead_of_estimating(credential, langfuse):
    _chunk(credential)
    credential.refresh_from_db()
    progress = sync_progress(credential)
    assert credential.sync_status == "live"
    assert progress.state == "complete"
    assert progress.imported == 0
    assert progress.estimated_seconds_remaining == 0


def test_metrics_quota_wait_does_not_block_trace_pages_or_retry_early(
    credential, langfuse, time_machine
):
    time_machine.move_to("2026-01-03T00:00:00Z", tick=False)
    _traces(langfuse, 4)
    langfuse.page_size = 1
    langfuse.metrics_status = 429
    langfuse.metrics_retry_after = 3600
    _chunk(credential)
    time_machine.shift(301)
    _chunk(credential)
    assert len([call for call in langfuse.requests if "/v2/metrics" in call.url]) == 1
    assert _spans(credential) == 2


def test_totals_and_progress_count_the_target_project(credential, langfuse):
    target = make_project()
    save_sync_config(credential, target_project=target)
    _traces(langfuse, 3)
    credential = sync_until_live(credential)
    assert Span.objects.filter(project=target).count() == 3
    assert (credential.total_spans_imported, credential.total_traces_imported) == (3, 3)
    assert sync_progress(credential).imported == 3


def test_a_failed_live_poll_after_a_finished_backfill_is_not_complete(credential, langfuse):
    _traces(langfuse, 1)
    credential = sync_until_live(credential)
    assert sync_progress(credential).state == "complete"
    retry = timezone.now() + timedelta(minutes=5)
    ConnectorCredential.objects.filter(pk=credential.pk).update(
        sync_error="Langfuse rate limited", next_poll_at=retry
    )
    credential.refresh_from_db()
    progress = sync_progress(credential)
    assert progress.state == "waiting"
    assert progress.retry_at == retry
    assert progress.estimated_seconds_remaining is None


def test_a_middle_chunk_does_not_count_the_whole_import(credential, langfuse):
    _traces(langfuse, 3)
    langfuse.page_size = 1
    _chunk(credential)
    with CaptureQueriesContext(connection) as queries:
        _chunk(credential)
    credential.refresh_from_db()
    assert credential.total_traces_imported == 2
    assert not [q["sql"] for q in queries if "COUNT(" in q["sql"] and "resource_attrs" in q["sql"]]


def test_a_reset_reaches_spans_in_a_former_target_project(credential, langfuse):
    first, second = make_project(), make_project()
    save_sync_config(credential, target_project=first)
    _traces(langfuse, 2)
    sync_until_live(credential)
    assert Span.objects.filter(project=first).count() == 2
    save_sync_config(credential, target_project=second)
    assert reset_connector_import(credential) == 2
    assert not Span.objects.filter(project=first).exists()


def test_a_chunk_without_new_traces_keeps_the_import_active(credential, langfuse):
    _traces(langfuse, 3)
    langfuse.page_size = 1
    _chunk(credential)
    credential.refresh_from_db()
    with time_machine.travel(timezone.now() + timedelta(minutes=10), tick=False):
        checkpoint_progress(credential, credential.sync_cursor["progress"], gained=0, done=False)
        assert sync_progress(credential).state != "waiting"
