from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from django.utils import timezone
from factories import make_connector

from overbae.models import ConnectorCredential
from overbae.services.connectors.langsmith.adapter import LangSmithAdapter
from overbae.services.connectors.langsmith.client import LangSmithError

_PROJECT = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def api(scripted, slept):
    return scripted("https://api.smith.langchain.com")


def _adapter(source=_PROJECT, **config) -> LangSmithAdapter:
    return LangSmithAdapter(make_connector("langsmith", source_project_id=source, **config))


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


def _page(api, rows, cursor=None):
    api.reply(json_body={"runs": rows, "cursors": {"next": cursor} if cursor else {}})


def _capped_slice(api, rows):
    for number in range(1, 51):
        _page(api, rows, f"c{number}")


def _start(call) -> datetime:
    return datetime.fromisoformat(call.json["start_time"].replace("Z", "+00:00"))


pytestmark = pytest.mark.django_db


def test_discovery_stays_in_the_cheap_six_day_tier_even_when_the_wizard_asks_for_thirty(api):
    _page(api, [_run("a")])
    units = _adapter(source="").sample_units(lookback_days=30, source_project_id=_OTHER)

    assert len(units) == 1
    assert api.calls[0].json["session"] == [_OTHER]
    assert timezone.now() - _start(api.calls[0]) <= timedelta(days=6, minutes=1)


def test_sample_units_honours_explicit_window_bounds(api):
    _page(api, [_run("a")])
    _adapter().sample_units(
        lookback_days=1,
        window_from=datetime(2026, 1, 1, tzinfo=UTC),
        window_to=datetime(2026, 1, 11, tzinfo=UTC),
    )

    assert api.calls[0].json["start_time"] == "2026-01-01T00:00:00Z"
    assert 'lt(start_time, "2026-01-11T00:00:00Z")' in api.calls[0].json["trace_filter"]


def test_sample_units_without_a_project_says_so(api):
    with pytest.raises(LangSmithError, match="No LangSmith source project"):
        _adapter(source="").sample_units(lookback_days=7)
    assert api.calls == []


def test_saved_config_still_wins_when_no_override_is_passed(api):
    _page(api, [_run("a")])
    _adapter().sample_units(lookback_days=7)

    assert api.calls[0].json["session"] == [_PROJECT]


def test_sample_units_resolves_a_project_name_to_its_id(api):
    api.reply(json_body=[{"id": _PROJECT, "name": "Demo"}])
    _page(api, [_run("a")])
    units = _adapter(source="").sample_units(lookback_days=7, source_project_id="Demo")

    assert len(units) == 1
    assert api.calls[1].json["session"] == [_PROJECT]


def test_sample_units_rejects_an_unknown_project_name(api):
    api.reply(json_body=[])
    with pytest.raises(LangSmithError, match="Unknown LangSmith project"):
        _adapter(source="").sample_units(lookback_days=7, source_project_id="Demo")


def test_verify_probes_the_first_project_with_a_one_run_query(api):
    api.reply(json_body=[{"id": _PROJECT, "name": "Demo"}, {"id": _OTHER, "name": "Other"}])
    _page(api, [])
    result = _adapter().verify()

    assert result.ok is True
    assert [p.id for p in result.projects] == [_PROJECT, _OTHER]
    assert api.calls[1].json["limit"] == 1
    assert "start_time" in api.calls[1].json


def test_verify_works_on_the_unsaved_stand_in_the_wizard_builds(scripted, slept):
    eu = scripted("https://eu.api.smith.langchain.com")
    eu.reply(json_body=[{"id": _PROJECT, "name": "Demo"}])
    _page(eu, [])
    stand_in = ConnectorCredential(
        connector_type="langsmith",
        api_key="lsv2_sk_key",
        base_url="https://eu.api.smith.langchain.com",
    )
    result = LangSmithAdapter(stand_in).verify()

    assert result.ok is True
    assert eu.calls[1].json["session"] == [_PROJECT]


def test_verify_skips_the_probe_when_the_org_has_no_projects(api):
    api.reply(json_body=[])
    result = _adapter(source="").verify()

    assert result.ok is True
    assert result.projects == []
    assert len(api.calls) == 1


@pytest.mark.parametrize("key", ["lsv2_pt_personal", "legacy-no-prefix"])
def test_verify_accepts_every_langsmith_key_format(api, key):
    api.reply(json_body=[])
    credential = make_connector("langsmith", api_key=key)

    assert LangSmithAdapter(credential).verify().ok is True
    assert api.calls[0].headers["x-api-key"] == key


def test_backfill_asks_for_whole_traces_whose_root_starts_in_the_window(api):
    _page(api, [_run("a")])
    page = _adapter().fetch_page({})

    assert page.mode == "backfill"
    assert page.done is False
    body = api.calls[0].json
    assert "gte(start_time" in body["trace_filter"]
    assert 'lt(start_time, "' in body["trace_filter"]
    assert _start(api.calls[0]) == page.window_from.replace(microsecond=0)


def test_backfill_honours_explicit_config_bounds(api):
    _page(api, [_run("a")])
    adapter = _adapter(
        lookback_days=None,
        backfill_from=datetime(2026, 1, 1, tzinfo=UTC),
        backfill_to=datetime(2026, 1, 3, tzinfo=UTC),
    )
    first = adapter.fetch_page({})
    second = adapter.fetch_page(first.next_state)

    assert first.window_to == datetime(2026, 1, 3, tzinfo=UTC)
    assert second.window_from == datetime(2026, 1, 1, tzinfo=UTC)
    assert api.calls[0].json["trace_filter"].endswith('lt(start_time, "2026-01-03T00:00:00Z"))')
    assert api.calls[1].json["start_time"] == "2026-01-01T00:00:00Z"


def test_an_all_time_config_imports_from_the_beginning_in_one_window(api):
    _page(api, [_run("a")])
    now = datetime(2026, 1, 3, tzinfo=UTC)
    with time_machine.travel(now, tick=False):
        page = _adapter(lookback_days=None).fetch_page({})

    assert page.done is True
    assert page.window_from == datetime.min.replace(tzinfo=UTC)
    assert page.window_to == now
    assert 'gte(start_time, "0001-01-01T00:00:00Z")' in api.calls[0].json["trace_filter"]


def test_backfill_resumes_from_next_window_end(api):
    _page(api, [_run("a")])
    adapter = _adapter()
    first = adapter.fetch_page({})
    second = adapter.fetch_page(first.next_state)

    assert second.next_state["windows_remaining"] == 1
    assert second.window_to.isoformat() == first.next_state["next_window_end"]
    assert _start(api.calls[1]) < _start(api.calls[0])


def test_a_capped_slice_resumes_inside_the_same_window(api):
    _capped_slice(api, [_run("a")])
    _page(api, [_run("b")])
    adapter = _adapter()
    first = adapter.fetch_page({})

    assert first.done is False
    assert first.next_state["next_cursor"] == "c50"
    assert first.next_state["next_window_end"] == first.window_to.isoformat()

    second = adapter.fetch_page(first.next_state)

    resumed = api.calls[50].json
    assert resumed["cursor"] == "c50"
    assert resumed["start_time"] == api.calls[0].json["start_time"]
    assert resumed["trace_filter"] == api.calls[0].json["trace_filter"]
    assert "next_cursor" not in second.next_state


def test_empty_historical_window_does_not_complete_the_backfill(api):
    _page(api, [])
    page = _adapter().fetch_page({})

    assert page.units == []
    assert page.done is False
    assert page.next_state["mode"] == "backfill"


def test_live_poll_rescans_six_days(api):
    _page(api, [_run("a")])
    page = _adapter().fetch_page({"mode": "live"})

    assert page.mode == "live"
    assert page.done is True
    assert page.window_to - page.window_from == timedelta(days=6)
    assert _start(api.calls[0]) == page.window_from.replace(microsecond=0)
    assert page.next_state["watermark"]


def test_a_capped_live_slice_resumes_on_the_same_window(api):
    _capped_slice(api, [_run("a")])
    _page(api, [_run("b")])
    adapter = _adapter()
    first = adapter.fetch_page({"mode": "live"})
    second = adapter.fetch_page(first.next_state)

    assert first.done is False
    assert first.next_state["live_next_cursor"] == "c50"
    assert second.done is True
    resumed = api.calls[50].json
    assert resumed["cursor"] == "c50"
    assert resumed["start_time"] == api.calls[0].json["start_time"]
    assert resumed["trace_filter"] == api.calls[0].json["trace_filter"]
    assert "live_next_cursor" not in second.next_state


def test_units_are_one_per_trace(api):
    _page(
        api,
        [
            _run("a", trace="trace-1"),
            _run("b", is_root=False, trace="trace-1", parent_ids=["a"]),
            _run("c", trace="trace-2"),
        ],
    )
    page = _adapter().fetch_page({"mode": "live"})

    assert sorted(u.external_trace_id for u in page.units) == ["trace-1", "trace-2"]
    assert sorted(len(u.records) for u in page.units) == [1, 2]


def test_a_capped_slice_does_not_promote_orphans(api):
    _capped_slice(api, [_run("child", is_root=False, parent_ids=["missing-root"])])
    page = _adapter().fetch_page({})

    record = page.units[0].records[0]
    assert record.is_root_observation is False
    assert record.parent_observation_id == "missing-root"


def test_live_slice_keeps_a_cross_page_parent_id_without_fabricating_a_root(api):
    _capped_slice(api, [_run("child", is_root=False, parent_ids=["root"])])
    _page(api, [_run("root", is_root=True)])
    adapter = _adapter()
    first = adapter.fetch_page({"mode": "live"})
    second = adapter.fetch_page(first.next_state)

    child_record = first.units[0].records[0]
    root_record = second.units[0].records[0]
    assert child_record.parent_observation_id == root_record.id
    assert child_record.is_root_observation is False
    assert root_record.is_root_observation is True
