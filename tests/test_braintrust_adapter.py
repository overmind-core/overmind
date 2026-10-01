from datetime import UTC, datetime

import pytest
from factories import make_connector

from overbae.models import ConnectorCredential
from overbae.services.connectors.braintrust.adapter import BraintrustAdapter
from overbae.services.connectors.braintrust.client import BraintrustError

pytestmark = pytest.mark.django_db


@pytest.fixture
def api(scripted, slept):
    return scripted("https://api.braintrust.dev")


def _adapter(source="proj-1", **config) -> BraintrustAdapter:
    return BraintrustAdapter(make_connector("braintrust", source_project_id=source, **config))


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


def _rows(api, rows, cursor=""):
    api.reply(json_body={"data": rows}, headers={"x-bt-cursor": cursor})


def _queries(api) -> list[str]:
    return [c.json["query"] for c in api.calls if c.path == "/btql"]


def test_discovery_reads_an_explicit_window(api):
    _rows(api, [_row("a")])
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    _adapter().sample_units(lookback_days=30, window_from=start, window_to=end)

    assert "created >= '2026-01-01" in _queries(api)[0]
    assert "created < '2026-01-02" in _queries(api)[0]


def test_sample_units_without_a_project_says_so(api):
    with pytest.raises(BraintrustError, match="No Braintrust source project"):
        _adapter(source="").sample_units(lookback_days=7)
    assert api.calls == []


def test_saved_config_still_wins_when_no_override_is_passed(api):
    _rows(api, [_row("a")])
    _adapter().sample_units(lookback_days=7)

    assert "project_logs('proj-1'" in _queries(api)[0]


def test_verify_reads_the_data_plane_not_just_the_project_list(api):
    api.reply(json_body={"objects": [{"id": "proj-1", "name": "Demo"}]})
    _rows(api, [])
    result = _adapter().verify()

    assert result.ok is True
    [probe] = _queries(api)
    assert "project_logs('proj-1'" in probe
    assert "created >= '" in probe
    assert "_xact_id ASC" in probe
    assert "LIMIT 1" in probe


def test_verify_fails_when_the_base_url_is_the_wrong_data_plane(api):
    api.reply(json_body={"objects": [{"id": "proj-1", "name": "Demo"}]})
    api.reply(
        421,
        json_body={
            "Code": "DataPlaneRedirectError",
            "Message": "Please direct your requests to: https://api-eu.braintrust.dev",
        },
    )
    result = _adapter().verify()

    assert result.ok is False
    assert "api-eu.braintrust.dev" in result.detail


def test_verify_works_on_the_unsaved_stand_in_the_wizard_builds(scripted, slept):
    eu = scripted("https://api-eu.braintrust.dev")
    eu.reply(json_body={"objects": [{"id": "proj-9", "name": "Demo"}]})
    _rows(eu, [])
    stand_in = ConnectorCredential(
        connector_type="braintrust", api_key="bt-st-key", base_url="https://api-eu.braintrust.dev"
    )
    result = BraintrustAdapter(stand_in).verify()

    assert result.ok is True
    assert "project_logs('proj-9'" in _queries(eu)[0]


def test_verify_skips_the_probe_when_the_org_has_no_projects(api):
    api.reply(json_body={"objects": []})
    result = _adapter(source="").verify()

    assert result.ok is True
    assert result.projects == []
    assert _queries(api) == []


def test_backfill_walks_bounded_windows_newest_first(api):
    _rows(api, [_row("a")])
    page = _adapter().fetch_page({})

    assert page.mode == "backfill"
    assert page.done is False
    assert page.next_state["windows_remaining"] == 2
    query = _queries(api)[0]
    assert "project_logs('proj-1'" in query
    assert "created >= '" in query
    assert "AND created < '" in query
    assert "BETWEEN" not in query


def test_backfill_honors_the_configured_window(api):
    _rows(api, [_row("a")])
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC)
    page = _adapter(backfill_from=start, backfill_to=end).fetch_page({})

    assert page.window_to == end
    assert "created < '2026-01-03" in _queries(api)[0]
    assert "created >= '2026-01-01" not in _queries(api)[0]


def test_backfill_resumes_from_next_window_end(api):
    _rows(api, [_row("a")])
    adapter = _adapter()
    first = adapter.fetch_page({})
    second = adapter.fetch_page(first.next_state)

    assert second.next_state["windows_remaining"] == 1
    assert second.window_to.isoformat() == first.next_state["next_window_end"]
    older, newer = _queries(api)[1], _queries(api)[0]
    assert older.split("created >= '")[1] < newer.split("created >= '")[1]


def test_empty_historical_window_does_not_complete_the_backfill(api):
    _rows(api, [])
    page = _adapter().fetch_page({})

    assert page.units == []
    assert page.done is False
    assert page.next_state["mode"] == "backfill"


def test_the_live_watermark_starts_from_the_newest_backfilled_write(api):
    _rows(api, [_row("a")])
    adapter, state = _adapter(), {}
    for _ in range(3):
        page = adapter.fetch_page(state)
        state = page.next_state

    assert page.done is True
    assert state["mode"] == "live"
    assert state["xact_id"] == 1000


def test_live_poll_uses_xact_id_without_a_created_floor_for_late_updates(api):
    _rows(api, [_row("a", xact_id=2000)])
    page = _adapter().fetch_page({"mode": "live", "xact_id": 1500})

    assert page.done is True
    assert "created" not in _queries(api)[0].split("WHERE", 1)[-1].split("ORDER")[0]
    assert "_xact_id >= '1500'" in _queries(api)[0]
    assert page.next_state["xact_id"] == 2000


def test_live_watermark_is_quoted_so_a_real_19_digit_xact_id_survives(api):
    real = 1000197680912101758
    _rows(api, [_row("a", xact_id=real + 1)])
    _adapter().fetch_page({"mode": "live", "xact_id": real})

    assert f"_xact_id >= '{real}'" in _queries(api)[0]


def test_live_watermark_never_moves_backwards(api):
    _rows(api, [_row("a", xact_id=900)])
    page = _adapter().fetch_page({"mode": "live", "xact_id": 5000})

    assert page.next_state["xact_id"] == 5000


def test_live_poll_omits_the_xact_id_predicate_on_the_first_pass(api):
    _rows(api, [_row("a")])
    _adapter().fetch_page({"mode": "live"})

    assert "_xact_id >=" not in _queries(api)[0]


def test_a_capped_live_page_continues_from_the_btql_cursor(api):
    for number in range(1, 201):
        _rows(api, [_row(f"a{number}", xact_id=2000)], cursor=f"c{number}")
    _rows(api, [_row("b", xact_id=2001)])
    adapter = _adapter()
    first = adapter.fetch_page({"mode": "live", "xact_id": 1500})
    second = adapter.fetch_page(first.next_state)

    assert first.done is False
    assert first.next_state["btql_cursor"] == "c200"
    assert "OFFSET 'c200'" in _queries(api)[200]
    assert second.done is True
    assert second.next_state["xact_id"] == 2001


def test_ordering_falls_back_to_xact_id_when_pagination_key_is_missing(api):
    api.reply(400, text="Unknown column _pagination_key")
    _rows(api, [_row("a")])
    page = _adapter().fetch_page({"mode": "live"})

    assert "_pagination_key ASC" in _queries(api)[0]
    assert "_xact_id ASC" in _queries(api)[1]
    assert len(page.units) == 1


def test_units_are_one_per_trace(api):
    _rows(
        api,
        [
            _row("a", root="trace-1"),
            _row("b", root="trace-1", is_root=False),
            _row("c", root="trace-2"),
        ],
    )
    page = _adapter().fetch_page({"mode": "live"})

    assert sorted(u.external_trace_id for u in page.units) == ["trace-1", "trace-2"]
    assert sorted(len(u.records) for u in page.units) == [1, 2]
