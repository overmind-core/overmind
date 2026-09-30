"""Langfuse adapter: backfill window walk."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from overbae.services.connectors.langfuse import adapter as adapter_module
from overbae.services.connectors.langfuse.adapter import LangfuseAdapter
from overbae.services.connectors.langfuse.client import LangFuseClient, LangFuseObservation

_START = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _cred(*, lookback_days=5, backfill_from=None, backfill_to=None, capability_mapping=None):
    return SimpleNamespace(
        name="demo",
        api_key="pk-lf-key",
        api_secret="sk-lf-secret",
        base_url="",
        api_version="v2",
        connector_type="langfuse",
        capability_mapping=capability_mapping or {},
        project=SimpleNamespace(id="p", slug="p"),
        active_config=lambda: SimpleNamespace(
            lookback_days=lookback_days,
            backfill_from=backfill_from,
            backfill_to=backfill_to,
            source_project_id="src",
            version=1,
        ),
    )


def _adapter(monkeypatch, *, tick=timedelta(seconds=2)):
    """Adapter whose clock advances *tick* on every now() call."""
    clock = {"t": _START}

    def fake_now():
        clock["t"] += tick
        return clock["t"]

    monkeypatch.setattr(adapter_module, "timezone", SimpleNamespace(now=fake_now))
    monkeypatch.setattr(LangFuseClient, "iter_ingest_units", lambda self, **kwargs: iter(()))
    monkeypatch.setattr(
        LangFuseClient, "fetch_v2_trace_page", lambda self, *args, **kwargs: ([], None)
    )
    return LangfuseAdapter(_cred())


def _walk(adapter, max_pages=10):
    state: dict = {}
    pages = []
    for _ in range(max_pages):
        page = adapter.fetch_page(state)
        pages.append(page)
        if page.done:
            break
        state = page.next_state
    return pages


def test_empty_v2_backfill_reads_the_whole_range_once(monkeypatch):
    pages = _walk(_adapter(monkeypatch))
    assert len(pages) == 1
    assert pages[0].done
    assert pages[0].window_to - pages[0].window_from == timedelta(days=5)


def test_backfill_pins_its_anchor_across_cursor_pages(monkeypatch):
    adapter = _adapter(monkeypatch)
    calls = []

    def fetch(self, window, *, cursor, expand_metadata):
        calls.append(window)
        return [], "next" if cursor is None else None

    monkeypatch.setattr(LangFuseClient, "fetch_v2_trace_page", fetch)
    pages = _walk(adapter)
    assert len(pages) == 2
    assert calls[0] == calls[1]
    assert pages[1].done


def test_backfill_uses_the_configured_bounds(monkeypatch):
    start = datetime(2026, 8, 1, 12, tzinfo=UTC)
    end = start + timedelta(days=1)
    adapter = LangfuseAdapter(_cred(lookback_days=30, backfill_from=start, backfill_to=end))
    monkeypatch.setattr(LangFuseClient, "iter_ingest_units", lambda self, **kwargs: iter(()))
    monkeypatch.setattr(
        LangFuseClient, "fetch_v2_trace_page", lambda self, *args, **kwargs: ([], None)
    )

    page = adapter.fetch_page({})

    assert page.window_from == start
    assert page.window_to == end


def test_count_and_sample_prefer_explicit_bounds(monkeypatch):
    adapter = LangfuseAdapter(_cred())
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = start + timedelta(days=3)
    count_windows = []
    sample_windows = []

    monkeypatch.setattr(
        LangFuseClient,
        "count",
        lambda self, *, window: count_windows.append(window) or 7,
    )
    monkeypatch.setattr(
        LangFuseClient,
        "iter_ingest_units",
        lambda self, *, windows: sample_windows.append(windows[0]) or iter(()),
    )

    assert adapter.count(lookback_days=30, window_from=start, window_to=end) == 7
    assert adapter.sample_units(lookback_days=30, window_from=start, window_to=end) == []
    assert count_windows == [adapter_module.TimeWindow(start=start, end=end)]
    assert sample_windows == [adapter_module.TimeWindow(start=start, end=end)]


def test_live_v2_pagination_pins_its_window_and_resumes(monkeypatch):
    adapter = LangfuseAdapter(_cred(capability_mapping={"source": "metadata", "key": "capability"}))
    clock = {"t": _START}
    calls = []

    def fake_now():
        clock["t"] += timedelta(minutes=1)
        return clock["t"]

    def fetch_page(self, window, *, cursor, expand_metadata):
        calls.append((window, cursor, expand_metadata))
        if cursor is None:
            return (
                [
                    [
                        LangFuseObservation(
                            id="one",
                            trace_id="t1",
                            parent_observation_id=None,
                            type="SPAN",
                            name="one",
                            start_time=_START.isoformat(),
                            end_time=None,
                        )
                    ]
                ],
                "cursor-1",
            )
        return (
            [
                [
                    LangFuseObservation(
                        id="two",
                        trace_id="t2",
                        parent_observation_id=None,
                        type="SPAN",
                        name="two",
                        start_time=_START.isoformat(),
                        end_time=None,
                    )
                ]
            ],
            None,
        )

    monkeypatch.setattr(adapter_module, "timezone", SimpleNamespace(now=fake_now))
    monkeypatch.setattr(LangFuseClient, "fetch_v2_trace_page", fetch_page)

    first = adapter.fetch_page({"mode": "live", "watermark": _START.isoformat()})
    second = adapter.fetch_page(first.next_state)

    assert first.done is False
    assert first.next_state["next_cursor"] == "cursor-1"
    assert second.done is True
    assert second.next_state == {"mode": "live", "watermark": first.window_to.isoformat()}
    assert calls == [
        (adapter_module.TimeWindow(_START - timedelta(minutes=20), first.window_to), None, None),
        (
            adapter_module.TimeWindow(_START - timedelta(minutes=20), first.window_to),
            "cursor-1",
            None,
        ),
    ]
