"""LangSmith adapter: whole-trace filter and resumable bounded windows."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from overbae.services.connectors.langsmith import adapter as langsmith_adapter
from overbae.services.connectors.langsmith.adapter import LangSmithAdapter
from overbae.services.connectors.langsmith.client import (
    LangSmithClient,
    LangSmithError,
    LangSmithProject,
    QueryPage,
)

_PROJECT = "11111111-1111-1111-1111-111111111111"


def _cred(*, lookback_days=3, source_project_id=_PROJECT, api_key="lsv2_sk_key"):
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        name="demo",
        api_key=api_key,
        api_secret="",
        base_url="",
        connector_type="langsmith",
        capability_mapping={},
        project=SimpleNamespace(id="p", slug="p"),
        active_config=lambda: SimpleNamespace(
            lookback_days=lookback_days,
            source_project_id=source_project_id,
            version=1,
        ),
    )


def _adapter(monkeypatch, pages):
    """pages: list of (rows, next_cursor) returned from iter_runs, in order."""
    calls = []

    def fake_iter(self, project_ids, **kwargs):
        calls.append({"project_ids": project_ids, **kwargs})
        return pages[min(len(calls) - 1, len(pages) - 1)]

    monkeypatch.setattr(LangSmithClient, "iter_runs", fake_iter)
    return LangSmithAdapter(_cred()), calls


def _run(run_id, *, is_root=True, trace="trace-1", parent_ids=()):
    return {
        "id": run_id,
        "trace_id": trace,
        "parent_run_ids": list(parent_ids),
        "is_root": is_root,
        "name": "handler",
        "run_type": "CHAIN",
        "status": "SUCCESS",
        "start_time": "2026-01-02T00:00:00Z",
        "end_time": "2026-01-02T00:00:01Z",
    }


def test_capabilities_omit_exact_count_and_lead_with_observation_names():
    caps = LangSmithAdapter(_cred()).capabilities

    assert caps.exact_count is False
    assert caps.capability_sources[0] == "observation_name"
    assert "14 days" in caps.retention_note
    assert caps.needs_secret is False


def test_count_is_none_even_with_explicit_window():
    assert (
        LangSmithAdapter(_cred()).count(
            lookback_days=7,
            window_from=datetime(2026, 1, 1, tzinfo=UTC),
            window_to=datetime(2026, 1, 2, tzinfo=UTC),
        )
        is None
    )


def test_sample_units_uses_the_project_the_wizard_is_offering(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_run("a")], None)])
    adapter.credential.active_config = lambda: SimpleNamespace(
        lookback_days=3, source_project_id="", version=1
    )
    units = adapter.sample_units(
        lookback_days=7, source_project_id="22222222-2222-2222-2222-222222222222"
    )

    assert len(units) == 1
    assert calls[0]["project_ids"] == ["22222222-2222-2222-2222-222222222222"]
    # Discovery stays in the cheap rate tier even when the wizard asked for 30.
    span = calls[0]["max_start_time"] - calls[0]["min_start_time"]
    assert span <= timedelta(days=6)


def test_sample_units_honours_explicit_window_bounds(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_run("a")], None)])
    window_from = datetime(2026, 1, 1, tzinfo=UTC)
    window_to = datetime(2026, 1, 11, tzinfo=UTC)

    adapter.sample_units(
        lookback_days=1,
        window_from=window_from,
        window_to=window_to,
    )

    assert calls[0]["min_start_time"] == window_from
    assert calls[0]["max_start_time"] == window_to


def test_sample_units_without_a_project_says_so(monkeypatch):
    adapter, _ = _adapter(monkeypatch, [([], None)])
    adapter.credential.active_config = lambda: SimpleNamespace(
        lookback_days=3, source_project_id="", version=1
    )
    with pytest.raises(LangSmithError, match="No LangSmith source project"):
        adapter.sample_units(lookback_days=7)


def test_saved_config_still_wins_when_no_override_is_passed(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_run("a")], None)])
    adapter.sample_units(lookback_days=7)

    assert calls[0]["project_ids"] == [_PROJECT]


def test_sample_units_resolves_a_project_name_to_its_id(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_run("a")], None)])
    monkeypatch.setattr(
        LangSmithClient,
        "list_projects",
        lambda self: [LangSmithProject(id=_PROJECT, name="Demo")],
    )
    adapter.credential.active_config = lambda: SimpleNamespace(
        lookback_days=3, source_project_id="", version=1
    )
    units = adapter.sample_units(lookback_days=7, source_project_id="Demo")

    assert len(units) == 1
    assert calls[0]["project_ids"] == [_PROJECT]


def test_sample_units_rejects_an_unknown_project_name(monkeypatch):
    adapter, _ = _adapter(monkeypatch, [([], None)])
    monkeypatch.setattr(LangSmithClient, "list_projects", lambda self: [])
    adapter.credential.active_config = lambda: SimpleNamespace(
        lookback_days=3, source_project_id="", version=1
    )
    with pytest.raises(LangSmithError, match="Unknown LangSmith project"):
        adapter.sample_units(lookback_days=7, source_project_id="Demo")


def test_verify_lists_every_project_then_probes_runs(monkeypatch):
    queries = []
    monkeypatch.setattr(
        LangSmithClient,
        "list_projects",
        lambda self: [
            LangSmithProject(id=_PROJECT, name="Demo"),
            LangSmithProject(id="22222222-2222-2222-2222-222222222222", name="Other"),
        ],
    )
    monkeypatch.setattr(
        LangSmithClient,
        "query_runs",
        lambda self, project_ids, **kwargs: (
            queries.append({"project_ids": project_ids, **kwargs}) or QueryPage(rows=[])
        ),
    )
    result = LangSmithAdapter(_cred()).verify()

    assert result.ok is True
    assert [p.id for p in result.projects] == [_PROJECT, "22222222-2222-2222-2222-222222222222"]
    assert queries[0]["page_size"] == 1
    assert "min_start_time" in queries[0]


def test_verify_works_on_the_unsaved_stand_in_the_wizard_builds(monkeypatch):
    tmp = SimpleNamespace(
        pk=None,
        api_key="lsv2_sk_key",
        api_secret="",
        base_url="https://eu.api.smith.langchain.com",
        api_version="unknown",
        connector_type="langsmith",
    )
    queries = []
    monkeypatch.setattr(
        LangSmithClient,
        "list_projects",
        lambda self: [LangSmithProject(id=_PROJECT, name="Demo")],
    )
    monkeypatch.setattr(
        LangSmithClient,
        "query_runs",
        lambda self, project_ids, **kwargs: queries.append(project_ids) or QueryPage(rows=[]),
    )
    result = LangSmithAdapter(tmp).verify()

    assert result.ok is True
    assert queries[0] == [_PROJECT]


def test_verify_skips_the_probe_when_the_org_has_no_projects(monkeypatch):
    monkeypatch.setattr(LangSmithClient, "list_projects", lambda self: [])
    monkeypatch.setattr(
        LangSmithClient,
        "query_runs",
        lambda self, *a, **k: pytest.fail("no project id to probe with"),
    )
    result = LangSmithAdapter(_cred(source_project_id="")).verify()

    assert result.ok is True
    assert result.projects == []


def test_verify_accepts_a_personal_access_token(monkeypatch):
    monkeypatch.setattr(LangSmithClient, "list_projects", lambda self: [])
    result = LangSmithAdapter(_cred(api_key="lsv2_pt_personal", source_project_id="")).verify()

    assert result.ok is True


def test_verify_accepts_a_legacy_key_without_the_sk_prefix(monkeypatch):
    monkeypatch.setattr(LangSmithClient, "list_projects", lambda self: [])
    result = LangSmithAdapter(_cred(api_key="legacy-no-prefix", source_project_id="")).verify()

    assert result.ok is True


def test_backfill_sends_a_whole_trace_filter_and_child_slack(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_run("a")], None)])
    page = adapter.fetch_page({})

    assert page.mode == "backfill"
    assert page.done is False
    assert "gte(start_time" in calls[0]["trace_filter"]
    assert 'lt(start_time, "' in calls[0]["trace_filter"]
    slack = calls[0]["max_start_time"] - page.window_to
    assert slack == timedelta(days=1)
    assert calls[0]["min_start_time"] == page.window_from


def test_backfill_honours_explicit_config_bounds(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_run("a")], None), ([_run("b")], None)])
    window_from = datetime(2026, 1, 1, tzinfo=UTC)
    window_to = datetime(2026, 1, 3, tzinfo=UTC)
    adapter.credential.active_config = lambda: SimpleNamespace(
        lookback_days=None,
        source_project_id=_PROJECT,
        backfill_from=window_from,
        backfill_to=window_to,
        version=1,
    )

    first = adapter.fetch_page({})
    second = adapter.fetch_page(first.next_state)

    assert first.window_to == window_to
    assert second.window_from == window_from
    assert calls[0]["trace_filter"].endswith('lt(start_time, "2026-01-03T00:00:00Z"))')
    assert calls[1]["min_start_time"] == window_from


def test_explicit_all_time_config_is_not_converted_to_legacy_lookback(monkeypatch):
    now = datetime(2026, 1, 3, tzinfo=UTC)
    monkeypatch.setattr(langsmith_adapter.timezone, "now", lambda: now)
    adapter, calls = _adapter(monkeypatch, [([_run("a")], None)])
    adapter.credential.active_config = lambda: SimpleNamespace(
        lookback_days=None,
        source_project_id=_PROJECT,
        backfill_from=None,
        backfill_to=None,
        version=1,
    )

    page = adapter.fetch_page({})

    assert page.done is True
    assert page.window_from == datetime.min.replace(tzinfo=UTC)
    assert page.window_to == now
    assert calls[0]["min_start_time"] == page.window_from
    assert 'gte(start_time, "0001-01-01T00:00:00Z")' in calls[0]["trace_filter"]


def test_legacy_config_without_range_uses_the_default_lookback(monkeypatch):
    adapter, _calls = _adapter(monkeypatch, [([_run("a")], None)])
    adapter.credential.active_config = lambda: SimpleNamespace(
        lookback_days=None,
        source_project_id=_PROJECT,
        version=1,
    )

    page = adapter.fetch_page({})

    assert page.next_state["windows_remaining"] == 29


def test_backfill_resumes_from_next_window_end(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_run("a")], None), ([_run("b")], None)])
    first = adapter.fetch_page({})
    second = adapter.fetch_page(first.next_state)

    assert second.next_state["windows_remaining"] == 1
    assert second.window_to.isoformat() == first.next_state["next_window_end"]
    assert calls[1]["min_start_time"] < calls[0]["min_start_time"]


def test_intra_window_cursor_keeps_the_same_bounds(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_run("a")], "cur-1"), ([_run("b")], None)])
    first = adapter.fetch_page({})

    assert first.done is False
    assert first.next_state["next_cursor"] == "cur-1"
    assert first.next_state["next_window_end"] == first.window_to.isoformat()

    second = adapter.fetch_page(first.next_state)

    assert calls[1]["cursor"] == "cur-1"
    assert calls[1]["min_start_time"] == calls[0]["min_start_time"]
    assert calls[1]["max_start_time"] == calls[0]["max_start_time"]
    assert calls[1]["trace_filter"] == calls[0]["trace_filter"]
    assert "next_cursor" not in second.next_state


def test_empty_historical_window_does_not_complete_the_backfill(monkeypatch):
    adapter, _calls = _adapter(monkeypatch, [([], None)])
    page = adapter.fetch_page({})

    assert page.units == []
    assert page.done is False
    assert page.next_state["mode"] == "backfill"


def test_backfill_flips_to_live_when_the_windows_run_out(monkeypatch):
    adapter, _calls = _adapter(monkeypatch, [([_run("a")], None)])
    state: dict = {}
    for _ in range(3):
        page = adapter.fetch_page(state)
        state = page.next_state

    assert page.done is True
    assert state["mode"] == "live"
    assert state["watermark"]


def test_live_poll_rescans_six_days_without_child_slack(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_run("a")], None)])
    page = adapter.fetch_page({"mode": "live"})

    assert page.mode == "live"
    assert page.done is True
    span = calls[0]["max_start_time"] - calls[0]["min_start_time"]
    assert span == timedelta(days=6)
    assert page.next_state["watermark"]


def test_live_cursor_continues_with_fixed_window_bounds(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [([_run("a")], "cur-1"), ([_run("b")], None)])

    first = adapter.fetch_page({"mode": "live"})
    second = adapter.fetch_page(first.next_state)

    assert first.done is False
    assert first.next_state["live_next_cursor"] == "cur-1"
    assert second.done is True
    assert calls[1]["cursor"] == "cur-1"
    assert calls[1]["min_start_time"] == calls[0]["min_start_time"]
    assert calls[1]["max_start_time"] == calls[0]["max_start_time"]
    assert calls[1]["trace_filter"] == calls[0]["trace_filter"]
    assert "live_next_cursor" not in second.next_state


def test_units_are_one_per_trace(monkeypatch):
    rows = [
        _run("a", trace="trace-1"),
        _run("b", is_root=False, trace="trace-1", parent_ids=["a"]),
        _run("c", trace="trace-2"),
    ]
    adapter, _calls = _adapter(monkeypatch, [(rows, None)])
    page = adapter.fetch_page({"mode": "live"})

    assert sorted(u.external_trace_id for u in page.units) == ["trace-1", "trace-2"]
    assert sorted(len(u.records) for u in page.units) == [1, 2]


def test_a_slice_does_not_promote_orphans(monkeypatch):
    child = _run("child", is_root=False, parent_ids=["missing-root"])
    adapter, _calls = _adapter(monkeypatch, [([child], "more")])
    page = adapter.fetch_page({})

    record = page.units[0].records[0]
    assert record.is_root_observation is False
    assert record.parent_observation_id == "missing-root"


def test_live_slice_keeps_a_cross_page_parent_id_without_fabricating_a_root(monkeypatch):
    child = _run("child", is_root=False, parent_ids=["root"])
    root = _run("root", is_root=True)
    adapter, _calls = _adapter(monkeypatch, [([child], "next"), ([root], None)])

    first = adapter.fetch_page({"mode": "live"})
    second = adapter.fetch_page(first.next_state)

    child_record = first.units[0].records[0]
    root_record = second.units[0].records[0]
    assert child_record.parent_observation_id == root_record.id
    assert child_record.is_root_observation is False
    assert root_record.is_root_observation is True


def test_missing_source_project_is_reported(monkeypatch):
    monkeypatch.setattr(LangSmithClient, "iter_runs", lambda *a, **k: ([], None))
    adapter = LangSmithAdapter(_cred(source_project_id=""))

    with pytest.raises(LangSmithError, match="source project"):
        adapter.fetch_page({"mode": "live"})


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
