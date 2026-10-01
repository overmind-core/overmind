from datetime import datetime, timedelta

import pytest
from factories import make_connector

from overbae.models import ConnectorCredential
from overbae.services.connectors.galileo.adapter import GalileoAdapter
from overbae.services.connectors.galileo.client import GalileoError

pytestmark = pytest.mark.django_db

_PROJECT = "11111111-1111-1111-1111-111111111111"
_STREAM = "22222222-2222-2222-2222-222222222222"
_SOURCE = f"{_PROJECT}:{_STREAM}"
_OFFERED = "44444444-4444-4444-4444-444444444444:55555555-5555-5555-5555-555555555555"
_STREAMS = {
    "projects": [
        {"id": _PROJECT, "name": "Demo", "log_streams": [{"id": _STREAM, "name": "prod"}]}
    ],
    "next_starting_token": None,
}


@pytest.fixture
def api(scripted, slept):
    return scripted("https://api.galileo.ai")


def _adapter(source=_SOURCE, **config) -> GalileoAdapter:
    return GalileoAdapter(make_connector("galileo", source_project_id=source, **config))


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


def _search(api, trees, token=None):
    api.reply(json_body={"records": [{"id": t["id"]} for t in trees], "next_starting_token": token})
    for tree in trees:
        api.reply(json_body=tree)


def _searches(api):
    return [c for c in api.calls if c.path.endswith("/traces/search")]


def _window(call) -> tuple[datetime, datetime]:
    start, end = (datetime.fromisoformat(f["value"]) for f in call.json["filters"][:2])
    return start, end


def test_count_is_none_without_a_selected_stream(api):
    assert _adapter(source="").count(lookback_days=7) is None
    assert api.calls == []


def test_count_uses_the_stream_selected_in_the_wizard(api):
    api.reply(json_body={"total_count": 12})
    count = _adapter(source="").count(lookback_days=7, source_project_id=_SOURCE)

    assert count == 12
    assert api.calls[0].path == f"/v2/projects/{_PROJECT}/traces/count"
    assert api.calls[0].json["log_stream_id"] == _STREAM


def test_sample_units_without_a_project_says_so(api):
    with pytest.raises(GalileoError, match="No Galileo source project"):
        _adapter(source="").sample_units(lookback_days=7)


def test_saved_config_still_wins_when_no_override_is_passed(api):
    _search(api, [_tree("t1")])
    _adapter().sample_units(lookback_days=7)

    assert _searches(api)[0].path == f"/v2/projects/{_PROJECT}/traces/search"


def test_verify_lists_streams_then_probes_one_search(api):
    api.reply(json_body=_STREAMS)
    api.reply(json_body={"records": [], "next_starting_token": None})
    result = _adapter().verify()

    assert result.ok is True
    assert [p.id for p in result.projects] == [_SOURCE]
    assert _searches(api)[0].json["limit"] == 1


def test_verify_works_on_the_unsaved_stand_in_the_wizard_builds(api):
    api.reply(json_body=_STREAMS)
    api.reply(json_body={"records": [], "next_starting_token": None})
    stand_in = ConnectorCredential(connector_type="galileo", api_key="gal-key")
    result = GalileoAdapter(stand_in).verify()

    assert result.ok is True
    assert _searches(api)[0].json["log_stream_id"] == _STREAM


def test_verify_skips_the_probe_when_the_org_has_no_streams(api):
    api.reply(json_body={"projects": [], "next_starting_token": None})
    result = _adapter(source="").verify()

    assert result.ok is True
    assert result.projects == []
    assert _searches(api) == []


def test_verify_reports_a_rejected_key(api):
    api.reply(401, text="bad key")
    result = _adapter().verify()

    assert result.ok is False
    assert "rejected" in result.detail


def test_backfill_walks_bounded_windows_newest_first(api):
    _search(api, [_tree("t1")])
    page = _adapter().fetch_page({})

    assert page.mode == "backfill"
    assert page.done is False
    assert page.next_state["windows_remaining"] == 2
    start, end = _window(_searches(api)[0])
    assert start < end


def test_backfill_resumes_from_next_window_end(api):
    _search(api, [_tree("t1")])
    _search(api, [_tree("t2")])
    adapter = _adapter()
    first = adapter.fetch_page({})
    second = adapter.fetch_page(first.next_state)

    assert second.next_state["windows_remaining"] == 1
    assert second.window_to.isoformat() == first.next_state["next_window_end"]
    assert _window(_searches(api)[1])[0] < _window(_searches(api)[0])[0]


def test_intra_window_starting_token_keeps_the_same_window(api):
    _search(api, [_tree("t1")], token=25)
    _search(api, [_tree("t2")])
    adapter = _adapter()
    first = adapter.fetch_page({})

    assert first.done is False
    assert first.next_state["next_token"] == 25
    assert first.next_state["next_window_end"] == first.window_to.isoformat()

    second = adapter.fetch_page(first.next_state)

    searches = _searches(api)
    assert searches[1].json["starting_token"] == 25
    assert _window(searches[1]) == _window(searches[0])
    assert "next_token" not in second.next_state


def test_empty_historical_window_does_not_complete_the_backfill(api):
    _search(api, [])
    page = _adapter().fetch_page({})

    assert page.units == []
    assert page.done is False
    assert page.next_state["mode"] == "backfill"


def test_live_poll_rescans_two_days_for_late_async_metrics(api):
    _search(api, [_tree("t1")])
    page = _adapter().fetch_page({"mode": "live"})

    assert page.done is True
    start, end = _window(_searches(api)[0])
    assert end - start == timedelta(days=2)
    assert page.next_state["watermark"]


def test_live_poll_resumes_a_paginated_window(api):
    _search(api, [_tree("t1")], token=25)
    _search(api, [_tree("t2")])
    adapter = _adapter()
    first = adapter.fetch_page({"mode": "live"})
    second = adapter.fetch_page(first.next_state)

    assert first.done is False
    assert first.next_state["live_next_token"] == 25
    assert second.done is True
    searches = _searches(api)
    assert searches[1].json["starting_token"] == 25
    assert _window(searches[1]) == _window(searches[0])
    assert "live_next_token" not in second.next_state


def test_units_are_one_per_trace(api):
    _search(api, [_tree("t1"), _tree("t2")])
    page = _adapter().fetch_page({"mode": "live"})

    assert sorted(u.external_trace_id for u in page.units) == ["t1", "t2"]


def test_discovery_reads_the_stream_the_wizard_offers(api):
    _search(api, [_tree("t1")])
    units = _adapter(source="").sample_units(lookback_days=7, source_project_id=_OFFERED)

    assert len(units) == 1
    assert (
        _searches(api)[0].path == "/v2/projects/44444444-4444-4444-4444-444444444444/traces/search"
    )
