"""Braintrust adapter: bounded backfill walk, resume, and the _xact_id watermark."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from overbae.services.connectors.braintrust.adapter import BraintrustAdapter
from overbae.services.connectors.braintrust.client import (
    BraintrustClient,
    BraintrustError,
    BraintrustProject,
    BraintrustRegionError,
    QueryPage,
    SpanRowsPage,
)


def _cred(
    *,
    lookback_days=3,
    source_project_id="proj-1",
    backfill_from=None,
    backfill_to=None,
):
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        name="demo",
        api_key="bt-st-key",
        api_secret="",
        base_url="",
        connector_type="braintrust",
        capability_mapping={},
        project=SimpleNamespace(id="p", slug="p"),
        active_config=lambda: SimpleNamespace(
            lookback_days=lookback_days,
            source_project_id=source_project_id,
            backfill_from=backfill_from,
            backfill_to=backfill_to,
            version=1,
        ),
    )


def _adapter(monkeypatch, rows_by_call, **cred_kwargs):
    calls = []

    def fake_fetch(self, project_ids, *, where, **kwargs):
        calls.append({"project_ids": project_ids, "where": where, **kwargs})
        result = rows_by_call[min(len(calls) - 1, len(rows_by_call) - 1)]
        if isinstance(result, SpanRowsPage):
            return result
        return SpanRowsPage(rows=result, cursor=None, done=True)

    monkeypatch.setattr(BraintrustClient, "fetch_span_rows", fake_fetch)
    return BraintrustAdapter(_cred(**cred_kwargs)), calls


def _row(row_id, *, xact_id=1000, is_root=True, root="root-span"):
    return {
        "id": row_id,
        "span_id": f"s-{row_id}",
        "span_parents": [],
        "root_span_id": root,
        "is_root": is_root,
        "created": "2026-01-02T00:00:00+00:00",
        "_xact_id": xact_id,
        "span_attributes": {"name": "handler", "type": "task"},
        "metrics": {"start": 1767312000.0, "end": 1767312001.0},
    }


def test_capabilities_omit_exact_count_and_lead_with_observation_names():
    caps = BraintrustAdapter(_cred()).capabilities

    assert caps.exact_count is False
    assert caps.capability_sources[0] == "observation_name"
    assert "14 days" in caps.retention_note


def test_count_is_none_because_it_costs_a_shared_request():
    assert (
        BraintrustAdapter(_cred()).count(
            lookback_days=7,
            window_from=datetime(2026, 1, 1, tzinfo=UTC),
            window_to=datetime(2026, 1, 2, tzinfo=UTC),
        )
        is None
    )


def test_sample_units_uses_the_project_the_wizard_is_offering(monkeypatch):
    """Mid-wizard the sync config does not exist yet, so discovery has to be told
    which project was picked or it can only raise."""
    adapter, calls = _adapter(monkeypatch, [[_row("a")]])
    adapter.credential.active_config = lambda: SimpleNamespace(
        lookback_days=3, source_project_id="", version=1
    )
    units = adapter.sample_units(lookback_days=7, source_project_id="picked-in-browser")

    assert len(units) == 1
    assert calls[0]["project_ids"] == ["picked-in-browser"]


def test_sample_units_without_a_project_says_so(monkeypatch):
    adapter, _ = _adapter(monkeypatch, [[]])
    adapter.credential.active_config = lambda: SimpleNamespace(
        lookback_days=3, source_project_id="", version=1
    )
    with pytest.raises(BraintrustError, match="No Braintrust source project"):
        adapter.sample_units(lookback_days=7)


def test_saved_config_still_wins_when_no_override_is_passed(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [[_row("a")]])
    adapter.sample_units(lookback_days=7)

    assert calls[0]["project_ids"] == ["proj-1"]


def test_sample_units_honors_an_explicit_window(monkeypatch):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    adapter, calls = _adapter(monkeypatch, [[_row("a")]])

    adapter.sample_units(lookback_days=30, window_from=start, window_to=end)

    assert start.isoformat() in calls[0]["where"]
    assert end.isoformat() in calls[0]["where"]


def test_verify_reads_the_data_plane_not_just_the_project_list(monkeypatch):
    """Project names come from Braintrust's global control plane, so listing them
    succeeds against the wrong regional host. Only a BTQL read proves the base URL."""
    queries = []
    monkeypatch.setattr(
        BraintrustClient,
        "list_projects",
        lambda self: [BraintrustProject(id="proj-1", name="Demo")],
    )
    monkeypatch.setattr(
        BraintrustClient,
        "query",
        lambda self, sql: queries.append(sql) or QueryPage(rows=[]),
    )
    result = BraintrustAdapter(_cred()).verify()

    assert result.ok is True
    assert len(queries) == 1
    assert "project_logs('proj-1'" in queries[0]
    # A range filter is mandatory, and _pagination_key may not exist on this deployment.
    assert "created >= '" in queries[0]
    assert "_xact_id ASC" in queries[0]
    assert "LIMIT 1" in queries[0]


def test_verify_fails_when_the_base_url_is_the_wrong_data_plane(monkeypatch):
    monkeypatch.setattr(
        BraintrustClient,
        "list_projects",
        lambda self: [BraintrustProject(id="proj-1", name="Demo")],
    )

    def _misdirected(self, sql):
        raise BraintrustRegionError("Set the connector base URL to https://api-eu.braintrust.dev.")

    monkeypatch.setattr(BraintrustClient, "query", _misdirected)
    result = BraintrustAdapter(_cred()).verify()

    assert result.ok is False
    assert "api-eu.braintrust.dev" in result.detail


def test_verify_works_on_the_unsaved_stand_in_the_wizard_builds(monkeypatch):
    """ConnectorCredentialViewSet.perform_create verifies before saving, passing a
    stand-in with only the connection fields — so verify must not need a config."""
    tmp = SimpleNamespace(
        pk=None,
        api_key="bt-st-key",
        api_secret="",
        base_url="https://api-eu.braintrust.dev",
        api_version="unknown",
        connector_type="braintrust",
    )
    queries = []
    monkeypatch.setattr(
        BraintrustClient,
        "list_projects",
        lambda self: [BraintrustProject(id="proj-9", name="Demo")],
    )
    monkeypatch.setattr(
        BraintrustClient,
        "query",
        lambda self, sql: queries.append(sql) or QueryPage(rows=[]),
    )
    result = BraintrustAdapter(tmp).verify()

    assert result.ok is True
    assert "project_logs('proj-9'" in queries[0]


def test_verify_skips_the_probe_when_the_org_has_no_projects(monkeypatch):
    monkeypatch.setattr(BraintrustClient, "list_projects", lambda self: [])
    monkeypatch.setattr(
        BraintrustClient,
        "query",
        lambda self, sql: pytest.fail("no project id to probe with"),
    )
    result = BraintrustAdapter(_cred(source_project_id="")).verify()

    assert result.ok is True
    assert result.projects == []


def test_backfill_walks_bounded_windows_newest_first(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [[_row("a")]])
    page = adapter.fetch_page({})

    assert page.mode == "backfill"
    assert page.done is False
    assert page.next_state["windows_remaining"] == 2
    assert calls[0]["project_ids"] == ["proj-1"]
    assert "created >= '" in calls[0]["where"]
    assert "AND created < '" in calls[0]["where"]
    assert "BETWEEN" not in calls[0]["where"]


def test_backfill_honors_the_configured_window(monkeypatch):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 3, tzinfo=UTC)
    adapter, calls = _adapter(
        monkeypatch,
        [[_row("a")]],
        backfill_from=start,
        backfill_to=end,
    )

    page = adapter.fetch_page({})

    assert page.window_to == end
    assert start.isoformat() not in calls[0]["where"]
    assert end.isoformat() in calls[0]["where"]


def test_backfill_resumes_from_next_window_end(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [[_row("a")], [_row("b")]])
    first = adapter.fetch_page({})
    second = adapter.fetch_page(first.next_state)

    assert second.next_state["windows_remaining"] == 1
    assert second.window_to.isoformat() == first.next_state["next_window_end"]
    # The resumed window is strictly older than the one already walked.
    assert calls[1]["where"] < calls[0]["where"]


def test_empty_historical_window_does_not_complete_the_backfill(monkeypatch):
    adapter, _calls = _adapter(monkeypatch, [[]])
    page = adapter.fetch_page({})

    # Past retention Braintrust returns nothing rather than erroring, so an empty
    # page must not be read as "finished".
    assert page.units == []
    assert page.done is False
    assert page.next_state["mode"] == "backfill"


def test_backfill_flips_to_live_when_the_windows_run_out(monkeypatch):
    adapter, _calls = _adapter(monkeypatch, [[_row("a")]])
    state: dict = {}
    for _ in range(3):
        page = adapter.fetch_page(state)
        state = page.next_state

    assert page.done is True
    assert state["mode"] == "live"
    assert state["xact_id"] == 1000


def test_live_poll_uses_xact_id_without_a_created_floor_for_late_updates(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [[_row("a", xact_id=2000)]])
    page = adapter.fetch_page({"mode": "live", "xact_id": 1500})

    assert page.mode == "live"
    assert page.done is True
    assert "created" not in calls[0]["where"]
    assert "_xact_id >= '1500'" in calls[0]["where"]
    assert page.next_state["xact_id"] == 2000


def test_live_watermark_is_quoted_so_a_real_19_digit_xact_id_survives(monkeypatch):
    """A bare literal this large is rejected: "1000197680912101758 can't be
    represented as a JavaScript number"."""
    real = 1000197680912101758
    adapter, calls = _adapter(monkeypatch, [[_row("a", xact_id=real + 1)]])
    adapter.fetch_page({"mode": "live", "xact_id": real})

    assert f"_xact_id >= '{real}'" in calls[0]["where"]
    assert f"_xact_id >= {real} " not in calls[0]["where"]


def test_live_watermark_never_moves_backwards(monkeypatch):
    adapter, _calls = _adapter(monkeypatch, [[_row("a", xact_id=900)]])
    page = adapter.fetch_page({"mode": "live", "xact_id": 5000})

    assert page.next_state["xact_id"] == 5000


def test_live_poll_omits_the_xact_id_predicate_on_the_first_pass(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [[_row("a")]])
    adapter.fetch_page({"mode": "live"})

    assert "_xact_id" not in calls[0]["where"]


def test_live_page_continues_from_the_btql_cursor(monkeypatch):
    adapter, calls = _adapter(
        monkeypatch,
        [
            SpanRowsPage(rows=[_row("a", xact_id=2000)], cursor="resume", done=False),
            SpanRowsPage(rows=[_row("b", xact_id=2001)], cursor=None, done=True),
        ],
    )

    first = adapter.fetch_page({"mode": "live", "xact_id": 1500})
    second = adapter.fetch_page(first.next_state)

    assert first.done is False
    assert first.next_state["btql_cursor"] == "resume"
    assert calls[1]["cursor"] == "resume"
    assert second.done is True
    assert second.next_state["xact_id"] == 2001


def test_ordering_falls_back_to_xact_id_when_pagination_key_is_missing(monkeypatch):
    attempts = []

    def fake_fetch(self, project_ids, *, where, order_by="_pagination_key ASC", **kwargs):
        attempts.append(order_by)
        if "_pagination_key" in order_by:
            from overbae.services.connectors.braintrust.client import BraintrustError

            raise BraintrustError("Unknown column _pagination_key")
        return SpanRowsPage(rows=[_row("a")], cursor=None, done=True)

    monkeypatch.setattr(BraintrustClient, "fetch_span_rows", fake_fetch)
    page = BraintrustAdapter(_cred()).fetch_page({"mode": "live"})

    assert attempts == ["_pagination_key ASC", "_xact_id ASC"]
    assert len(page.units) == 1


def test_units_are_one_per_trace(monkeypatch):
    rows = [
        _row("a", root="trace-1"),
        _row("b", root="trace-1", is_root=False),
        _row("c", root="trace-2"),
    ]
    adapter, _calls = _adapter(monkeypatch, [rows])
    page = adapter.fetch_page({"mode": "live"})

    assert sorted(u.external_trace_id for u in page.units) == ["trace-1", "trace-2"]
    assert sorted(len(u.records) for u in page.units) == [1, 2]


def test_missing_source_project_is_reported(monkeypatch):
    monkeypatch.setattr(
        BraintrustClient,
        "fetch_span_rows",
        lambda *a, **k: SpanRowsPage(rows=[], cursor=None, done=True),
    )
    adapter = BraintrustAdapter(_cred(source_project_id=""))

    from overbae.services.connectors.braintrust.client import BraintrustError

    with pytest.raises(BraintrustError, match="source project"):
        adapter.fetch_page({"mode": "live"})


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
