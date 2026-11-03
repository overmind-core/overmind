from datetime import UTC, datetime, timedelta

import pytest

from overbae.services.connectors.langfuse.client import LangFuseClient, LangFuseError
from overbae.services.connectors.windows import TimeWindow, plan_windows

pytestmark = pytest.mark.covers("vendor-connectors")

_WINDOW = TimeWindow(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 2, tzinfo=UTC))


@pytest.fixture
def api(scripted, slept):
    return scripted("https://cloud.langfuse.com")


def test_plan_windows_newest_first():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 4, tzinfo=UTC)
    windows = plan_windows(start, end, chunk=timedelta(days=1))

    assert len(windows) == 3
    assert windows[0].end == end
    assert windows[0].start == datetime(2026, 1, 3, tzinfo=UTC)
    assert windows[-1].start == start
    for i in range(len(windows) - 1):
        assert windows[i].start == windows[i + 1].end


def test_plan_windows_empty_when_start_after_end():
    assert plan_windows(datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)) == []


def test_probe_uses_v2_when_endpoint_exists(api):
    api.reply(json_body={"data": []})
    assert LangFuseClient("pk", "sk").probe_capabilities() == "v2"
    assert "/v2/" in api.calls[0].path


def test_probe_falls_back_to_v1_on_v2_404_without_probing_v1(api):
    api.reply(404, text="not found")
    assert LangFuseClient("pk", "sk").probe_capabilities() == "v1"
    assert len(api.calls) == 1


def test_probe_raises_on_v2_auth_error(api):
    api.reply(401, text="unauthorized")
    with pytest.raises(LangFuseError, match="401"):
        LangFuseClient("pk", "sk").probe_capabilities()


def test_v2_trace_page_completes_trees_and_keeps_the_cursor(api):
    root = {
        "id": "root",
        "traceId": "trace-1",
        "type": "SPAN",
        "startTime": "2026-01-01T00:00:00Z",
        "isRootObservation": True,
    }
    child = {
        "id": "child",
        "traceId": "trace-1",
        "parentObservationId": "root",
        "type": "SPAN",
        "startTime": "2026-01-01T00:01:00Z",
    }
    api.reply(json_body={"data": [root], "meta": {"cursor": "next"}})
    api.reply(json_body={"data": [root, child], "meta": {}})
    traces, cursor = LangFuseClient("pk", "sk").fetch_v2_trace_page(
        _WINDOW, expand_metadata="capability"
    )

    assert [[observation.id for observation in trace] for trace in traces] == [["root", "child"]]
    assert cursor == "next"
    assert api.calls[0].params["expandMetadata"] == "capability"
    assert api.calls[1].params["traceId"] == "trace-1"
    assert api.calls[1].params["expandMetadata"] == "capability"


def test_v2_trace_page_stops_a_repeated_cursor(api):
    api.reply(json_body={"data": [], "meta": {"cursor": "stuck"}})
    with pytest.raises(LangFuseError, match="cursor"):
        LangFuseClient("pk", "sk").fetch_v2_trace_page(_WINDOW, cursor="stuck")


def test_a_rate_limit_that_never_lifts_raises_with_its_status(api):
    api.reply(429, text='{"message":"Rate limit exceeded"}', headers={"Retry-After": "0"})
    with pytest.raises(LangFuseError, match="429") as caught:
        LangFuseClient("pk", "sk").list_projects()

    assert caught.value.status_code == 429
    assert len(api.calls) > 1


def _obs(oid, parent=None, *, root=False, minute=0):
    return {
        "id": oid,
        "traceId": "t1",
        "parentObservationId": parent,
        "type": "SPAN",
        "name": oid,
        "startTime": f"2026-01-01T00:{minute:02d}:00Z",
        "isRootObservation": root,
    }


def _groups(api, *pages) -> list:
    api.reply(json_body={"data": []})
    for page in pages:
        api.reply(json_body=page)
    client = LangFuseClient("pk", "sk")
    assert client.probe_capabilities() == "v2"
    groups = list(client.iter_ingest_units(windows=[_WINDOW]))
    del api.calls[0]
    return groups


def test_an_orphaned_child_triggers_a_whole_trace_refetch(api):
    (group,) = _groups(
        api,
        {"data": [_obs("child", parent="root", minute=5)], "meta": {}},
        {"data": [_obs("root", root=True), _obs("child", parent="root")], "meta": {}},
    )

    assert sorted(o.id for o in group) == ["child", "root"]
    assert [c.params.get("traceId") for c in api.calls] == [None, "t1"]


def test_a_group_without_its_root_is_refetched(api):
    (group,) = _groups(
        api,
        {"data": [_obs("b", parent="a"), _obs("a", parent="root")], "meta": {}},
        {
            "data": [_obs("root", root=True), _obs("a", parent="root"), _obs("b", parent="a")],
            "meta": {},
        },
    )

    assert len(group) == 3
    assert api.calls[-1].params["traceId"] == "t1"


def test_a_complete_trace_is_not_refetched(api):
    (group,) = _groups(
        api, {"data": [_obs("root", root=True), _obs("child", parent="root")], "meta": {}}
    )

    assert len(group) == 2
    assert all("traceId" not in c.params for c in api.calls)


def test_window_paging_stops_when_the_cursor_repeats(api):
    with pytest.raises(LangFuseError, match="cursor"):
        _groups(api, {"data": [_obs("root", root=True)], "meta": {"cursor": "stuck"}})

    assert len(api.calls) <= 3


def test_batched_trace_completion_keeps_children_across_provider_pages(api):
    first = {"id": "a", "traceId": "t1", "type": "SPAN", "isRootObservation": True}
    second = {"id": "b", "traceId": "t2", "type": "SPAN", "isRootObservation": True}
    child = {"id": "c", "traceId": "t1", "type": "SPAN", "parentObservationId": "a"}
    api.reply(json_body={"data": [first, second], "meta": {"cursor": "discovery"}})
    api.reply(json_body={"data": [first, second], "meta": {"cursor": "children"}})
    api.reply(json_body={"data": [child], "meta": {}})
    groups, cursor = LangFuseClient("pk", "sk").fetch_v2_trace_page(_WINDOW)
    assert [[obs.id for obs in group] for group in groups] == [["a", "c"], ["b"]]
    assert cursor == "discovery"
    assert len(api.calls) == 3


@pytest.mark.parametrize("returned", [[], [{"id": "unexpected", "traceId": "other"}]])
def test_incomplete_or_unfiltered_batch_responses_are_not_imported(api, returned):
    api.reply(json_body={"data": [{"id": "root", "traceId": "wanted"}], "meta": {}})
    api.reply(json_body={"data": returned, "meta": {}})
    with pytest.raises(LangFuseError):
        LangFuseClient("pk", "sk").fetch_v2_trace_page(_WINDOW)


def test_count_uses_one_aggregate_request_not_observation_pagination(api):
    api.reply(
        json_body={
            "data": [
                {"isRootObservation": True, "count_count": 70000},
                {"isRootObservation": False, "count_count": 212},
            ]
        }
    )
    client = LangFuseClient("pk", "sk")
    assert client.volume(_WINDOW) == {"roots": 70000, "observations": 70212}
    assert len(api.calls) == 1
    assert api.calls[0].path == "/api/public/v2/metrics"


def test_missing_count_is_not_reported_as_zero(api):
    api.reply(json_body={"data": [{"isRootObservation": True}]})
    with pytest.raises(LangFuseError, match="valid volume count"):
        LangFuseClient("pk", "sk").volume(_WINDOW)


@pytest.mark.parametrize(
    "headers,body",
    [
        ({"Retry-After": "3600"}, {}),
        ({}, {"details": {"retryAfterSeconds": 3600}}),
    ],
)
def test_long_rate_limit_wait_is_deferred_without_shortening_it(api, headers, body):
    api.reply(429, headers=headers, json_body=body)
    with pytest.raises(LangFuseError) as caught:
        LangFuseClient("pk", "sk").list_projects()
    assert caught.value.retry_after == 3600
    assert len(api.calls) == 1
