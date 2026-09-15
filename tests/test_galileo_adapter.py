"""Galileo adapter: capabilities, wizard source override, backfill/live windowing."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from overbae.services.connectors.galileo.adapter import GalileoAdapter
from overbae.services.connectors.galileo.client import GalileoClient, GalileoError, GalileoLogStream

_PROJECT = "11111111-1111-1111-1111-111111111111"
_STREAM = "22222222-2222-2222-2222-222222222222"
_SOURCE = f"{_PROJECT}:{_STREAM}"


def _cred(*, lookback_days=3, source_project_id=_SOURCE, api_key="gal-key"):
    return SimpleNamespace(
        id="33333333-3333-3333-3333-333333333333",
        name="demo",
        api_key=api_key,
        api_secret="",
        base_url="",
        connector_type="galileo",
        capability_mapping={},
        project=SimpleNamespace(id="p", slug="p"),
        active_config=lambda: SimpleNamespace(
            lookback_days=lookback_days,
            source_project_id=source_project_id,
            version=1,
        ),
    )


def _tree(trace_id, *, created_at="2026-01-02T00:00:00Z"):
    return {
        "id": trace_id,
        "type": "trace",
        "name": "handler",
        "created_at": created_at,
        "updated_at": created_at,
        "metrics": {"duration_ns": 1_000_000_000},
        "spans": [],
    }


def _adapter(monkeypatch, pages):
    """pages: list of (trees, next_starting_token) returned from iter_trace_trees, in order."""
    calls = []

    def fake_iter(self, source_id, **kwargs):
        calls.append({"source_id": source_id, **kwargs})
        return pages[min(len(calls) - 1, len(pages) - 1)]

    monkeypatch.setattr(GalileoClient, "iter_trace_trees", fake_iter)
    return GalileoAdapter(_cred()), calls


def test_capabilities_omit_exact_count_and_a_secret():
    caps = GalileoAdapter(_cred()).capabilities

    assert caps.exact_count is False
    assert caps.needs_source_project is True
    assert caps.needs_secret is False


def test_count_is_none_without_a_selected_stream():
    assert GalileoAdapter(_cred(source_project_id="")).count(lookback_days=7) is None


def test_count_uses_the_stream_selected_in_the_wizard(monkeypatch):
    calls = []
    monkeypatch.setattr(
        GalileoClient,
        "count_traces",
        lambda self, source_id, **kwargs: calls.append((source_id, kwargs)) or 12,
    )

    count = GalileoAdapter(_cred(source_project_id="")).count(
        lookback_days=7,
        source_project_id=_SOURCE,
    )

    assert count == 12
    assert calls[0][0] == _SOURCE
    assert calls[0][1]["window_start"] < calls[0][1]["window_end"]


def test_sample_units_uses_the_stream_the_wizard_is_offering(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_tree("t1")], None)])
    adapter.credential.active_config = lambda: SimpleNamespace(
        lookback_days=3, source_project_id="", version=1
    )
    units = adapter.sample_units(
        lookback_days=7,
        source_project_id="44444444-4444-4444-4444-444444444444:55555555-5555-5555-5555-555555555555",
    )

    assert len(units) == 1
    assert (
        calls[0]["source_id"]
        == "44444444-4444-4444-4444-444444444444:55555555-5555-5555-5555-555555555555"
    )


def test_sample_units_without_a_project_says_so(monkeypatch):
    adapter, _ = _adapter(monkeypatch, [([], None)])
    adapter.credential.active_config = lambda: SimpleNamespace(
        lookback_days=3, source_project_id="", version=1
    )
    with pytest.raises(GalileoError, match="No Galileo source project"):
        adapter.sample_units(lookback_days=7)


def test_saved_config_still_wins_when_no_override_is_passed(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_tree("t1")], None)])
    adapter.sample_units(lookback_days=7)

    assert calls[0]["source_id"] == _SOURCE


def test_verify_lists_streams_then_probes_a_search(monkeypatch):
    monkeypatch.setattr(
        GalileoClient,
        "list_log_streams",
        lambda self: [GalileoLogStream(id=_SOURCE, name="Demo / prod")],
    )
    queries = []
    monkeypatch.setattr(
        GalileoClient,
        "search_traces",
        lambda self, source_id, **kwargs: (
            queries.append({"source_id": source_id, **kwargs})
            or SimpleNamespace(rows=[], next_starting_token=None)
        ),
    )
    result = GalileoAdapter(_cred()).verify()

    assert result.ok is True
    assert [p.id for p in result.projects] == [_SOURCE]
    assert queries[0]["source_id"] == _SOURCE
    assert queries[0]["limit"] == 1


def test_verify_works_on_the_unsaved_stand_in_the_wizard_builds(monkeypatch):
    tmp = SimpleNamespace(
        pk=None,
        api_key="gal-key",
        api_secret="",
        base_url="",
        api_version="unknown",
        connector_type="galileo",
    )
    monkeypatch.setattr(
        GalileoClient,
        "list_log_streams",
        lambda self: [GalileoLogStream(id=_SOURCE, name="Demo / prod")],
    )
    queries = []
    monkeypatch.setattr(
        GalileoClient,
        "search_traces",
        lambda self, source_id, **kwargs: (
            queries.append(source_id) or SimpleNamespace(rows=[], next_starting_token=None)
        ),
    )
    result = GalileoAdapter(tmp).verify()

    assert result.ok is True
    assert queries[0] == _SOURCE


def test_verify_skips_the_probe_when_the_org_has_no_streams(monkeypatch):
    monkeypatch.setattr(GalileoClient, "list_log_streams", lambda self: [])
    monkeypatch.setattr(
        GalileoClient,
        "search_traces",
        lambda self, *a, **k: pytest.fail("no source id to probe with"),
    )
    result = GalileoAdapter(_cred(source_project_id="")).verify()

    assert result.ok is True
    assert result.projects == []


def test_verify_surfaces_a_client_error(monkeypatch):
    def _boom(self):
        raise GalileoError("Galileo rejected the API key (401)")

    monkeypatch.setattr(GalileoClient, "list_log_streams", _boom)
    result = GalileoAdapter(_cred()).verify()

    assert result.ok is False
    assert "rejected" in result.detail


def test_backfill_walks_bounded_windows_newest_first(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_tree("t1")], None)])
    page = adapter.fetch_page({})

    assert page.mode == "backfill"
    assert page.done is False
    assert page.next_state["windows_remaining"] == 2
    assert calls[0]["source_id"] == _SOURCE
    assert calls[0]["window_start"] < calls[0]["window_end"]


def test_backfill_resumes_from_next_window_end(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_tree("t1")], None), ([_tree("t2")], None)])
    first = adapter.fetch_page({})
    second = adapter.fetch_page(first.next_state)

    assert second.next_state["windows_remaining"] == 1
    assert second.window_to.isoformat() == first.next_state["next_window_end"]
    assert calls[1]["window_start"] < calls[0]["window_start"]


def test_intra_window_starting_token_keeps_the_same_window(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_tree("t1")], 25), ([_tree("t2")], None)])
    first = adapter.fetch_page({})

    assert first.done is False
    assert first.next_state["next_token"] == 25
    assert first.next_state["next_window_end"] == first.window_to.isoformat()

    second = adapter.fetch_page(first.next_state)

    assert calls[1]["starting_token"] == 25
    assert calls[1]["window_start"] == calls[0]["window_start"]
    assert calls[1]["window_end"] == calls[0]["window_end"]
    assert "next_token" not in second.next_state


def test_empty_historical_window_does_not_complete_the_backfill(monkeypatch):
    adapter, _calls = _adapter(monkeypatch, [([], None)])
    page = adapter.fetch_page({})

    assert page.units == []
    assert page.done is False
    assert page.next_state["mode"] == "backfill"


def test_backfill_flips_to_live_when_the_windows_run_out(monkeypatch):
    adapter, _calls = _adapter(monkeypatch, [([_tree("t1")], None)])
    state: dict = {}
    for _ in range(3):
        page = adapter.fetch_page(state)
        state = page.next_state

    assert page.done is True
    assert state["mode"] == "live"
    assert state["watermark"]


def test_live_poll_rescans_two_days(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_tree("t1")], None)])
    page = adapter.fetch_page({"mode": "live"})

    assert page.mode == "live"
    assert page.done is True
    span = calls[0]["window_end"] - calls[0]["window_start"]
    assert span == timedelta(days=2)
    assert page.next_state["watermark"]


def test_live_poll_resumes_a_paginated_window(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_tree("t1")], 25), ([_tree("t2")], None)])

    first = adapter.fetch_page({"mode": "live"})
    second = adapter.fetch_page(first.next_state)

    assert first.done is False
    assert first.next_state["live_next_token"] == 25
    assert second.done is True
    assert calls[1]["starting_token"] == 25
    assert calls[1]["window_start"] == calls[0]["window_start"]
    assert calls[1]["window_end"] == calls[0]["window_end"]
    assert "live_next_token" not in second.next_state


def test_units_are_one_per_trace(monkeypatch):
    adapter, _calls = _adapter(monkeypatch, [([_tree("t1"), _tree("t2")], None)])
    page = adapter.fetch_page({"mode": "live"})

    assert sorted(u.external_trace_id for u in page.units) == ["t1", "t2"]


def test_missing_source_project_is_reported(monkeypatch):
    monkeypatch.setattr(GalileoClient, "iter_trace_trees", lambda *a, **k: ([], None))
    adapter = GalileoAdapter(_cred(source_project_id=""))

    with pytest.raises(GalileoError, match="source project"):
        adapter.fetch_page({"mode": "live"})


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
