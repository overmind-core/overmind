from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from factories import make_connector

from overbae.services.connectors.langfuse.adapter import LangfuseAdapter

pytestmark = pytest.mark.django_db

_START = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def api(scripted, slept):
    api = scripted("https://cloud.langfuse.com")
    api.reply(json_body={"data": [], "meta": {}})
    return api


@pytest.fixture
def clock():
    with time_machine.travel(_START, tick=False) as traveller:
        yield traveller


def _adapter(**config) -> LangfuseAdapter:
    return LangfuseAdapter(
        make_connector("langfuse", source_project_id="src", api_secret="sk", **config)
    )


def _walk(adapter, clock, max_pages=10):
    state: dict = {}
    pages = []
    for _ in range(max_pages):
        page = adapter.fetch_page(state)
        pages.append(page)
        if page.done:
            break
        state = page.next_state
        clock.shift(timedelta(seconds=2))
    return pages


def test_backfill_covers_every_window_while_the_clock_moves(api, clock):
    pages = _walk(_adapter(lookback_days=5), clock)

    assert len(pages) == 5
    assert pages[-1].done is True
    for earlier, later in zip(pages[:-1], pages[1:], strict=True):
        assert later.window_to == earlier.window_from


def test_backfill_pins_its_anchor_in_the_cursor(api, clock):
    pages = _walk(_adapter(lookback_days=5), clock)

    anchor = pages[0].next_state["backfill_anchor"]
    assert all(p.next_state.get("backfill_anchor") == anchor for p in pages[:-1])
    assert pages[0].window_to.isoformat() == anchor


def test_backfill_uses_the_configured_bounds(api):
    start = datetime(2026, 8, 1, 12, tzinfo=UTC)
    end = start + timedelta(days=1)
    page = _adapter(lookback_days=30, backfill_from=start, backfill_to=end).fetch_page({})

    assert page.window_from == start
    assert page.window_to == end


def test_count_and_discovery_prefer_explicit_bounds(api):
    api.steps.clear()
    api.reply(json_body={"data": [], "meta": {"totalItems": 7}})
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = start + timedelta(days=3)
    adapter = _adapter()

    adapter.count(lookback_days=30, window_from=start, window_to=end)
    adapter.sample_units(lookback_days=30, window_from=start, window_to=end)

    for call in api.calls:
        assert call.params["fromStartTime"].startswith("2026-08-01T00:00:00")
        assert call.params["toStartTime"].startswith("2026-08-04T00:00:00")


def test_live_pagination_pins_its_window_and_resumes(api, clock):
    def observation(oid, trace):
        return {
            "id": oid,
            "traceId": trace,
            "type": "SPAN",
            "name": oid,
            "startTime": _START.isoformat(),
            "isRootObservation": True,
        }

    api.steps.clear()
    api.reply(json_body={"data": [observation("one", "t1")], "meta": {"cursor": "cursor-1"}})
    api.reply(json_body={"data": [observation("one", "t1")], "meta": {}})
    api.reply(json_body={"data": [observation("two", "t2")], "meta": {}})
    api.reply(json_body={"data": [observation("two", "t2")], "meta": {}})
    adapter = _adapter(capability_mapping={"source": "metadata", "key": "capability"})

    clock.shift(timedelta(minutes=1))
    first = adapter.fetch_page({"mode": "live", "watermark": _START.isoformat()})
    clock.shift(timedelta(minutes=1))
    second = adapter.fetch_page(first.next_state)

    assert first.done is False
    assert first.next_state["next_cursor"] == "cursor-1"
    assert second.done is True
    assert second.next_state == {"mode": "live", "watermark": first.window_to.isoformat()}
    pages = [c.params for c in api.calls if "traceId" not in c.params]
    assert {(p["fromStartTime"], p["toStartTime"]) for p in pages} == {
        ("2026-08-19T12:00:00Z", "2026-08-19T12:01:00Z")
    }
    assert pages[1]["cursor"] == "cursor-1"
    assert all(c.params["expandMetadata"] == "capability" for c in api.calls)
