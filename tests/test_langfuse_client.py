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


@pytest.mark.parametrize(
    "metrics,version",
    [
        ({"status": 404, "text": "The metrics v2 API is only available in a v4 write mode"}, "v1"),
        ({"status": 429, "text": "quota"}, "v2"),
        ({"status": 500, "text": "v2 not found upstream"}, "v2"),
    ],
)
def test_probe_reads_empty_v2_observations_through_their_metrics(api, slept, metrics, version):
    api.reply(json_body={"data": []})
    api.reply(**metrics)
    assert LangFuseClient("pk", "sk").probe_capabilities() == version
    assert len(api.calls) == 2


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


def test_long_trace_ids_split_the_batch_below_the_server_url_limit(api):
    rows = [
        {"id": f"o{n}", "traceId": f"{n:0100d}", "type": "SPAN", "isRootObservation": True}
        for n in range(150)
    ]
    api.reply(json_body={"data": rows, "meta": {}})
    api.reply(json_body={"data": rows[:109], "meta": {}})
    api.reply(json_body={"data": rows[109:], "meta": {}})
    groups, _ = LangFuseClient("pk", "sk").fetch_v2_trace_page(_WINDOW)
    assert len(groups) == 150
    assert len(api.calls) == 3
    assert all(len(call.url) < 16_000 for call in api.calls)


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


def test_v1_page_builds_trees_with_trace_fields_and_a_page_cursor(api):
    root = {"id": "root", "traceId": "t1", "type": "SPAN", "parentObservationId": None}
    child = {
        "id": "child",
        "traceId": "t1",
        "type": "GENERATION",
        "parentObservationId": "root",
        "usage": {"unit": "TOKENS", "input": 11, "output": 7, "total": 18},
        "calculatedTotalCost": 0.5,
    }
    api.reply(json_body={"data": [child], "meta": {"page": 1, "totalPages": 2}})
    api.reply(
        json_body={
            "name": "support",
            "tags": ["a"],
            "sessionId": "s",
            "userId": "u",
            "observations": [root, child],
        }
    )
    ((first, second),), cursor = LangFuseClient("pk", "sk").fetch_v1_trace_page(_WINDOW)
    assert cursor == "2"
    assert (first.id, first.is_root_observation, second.is_root_observation) == (
        "root",
        True,
        False,
    )
    assert (second.trace_name, second.tags, second.session_id, second.user_id) == (
        "support",
        ["a"],
        "s",
        "u",
    )
    assert second.usage_details == {"input": 11, "output": 7, "total": 18}
    assert second.total_cost == 0.5
    assert api.calls[0].path == "/api/public/observations"
    assert api.calls[0].params["limit"] == "100"
    assert api.calls[1].path == "/api/public/traces/t1"


def test_v1_page_skips_empty_traces_and_keeps_usage_without_a_trace(api):
    traced = {"id": "a", "traceId": "gone", "type": "SPAN"}
    untraced = {"id": "b", "traceId": None, "type": "GENERATION", "usage": {"input": 3}}
    api.reply(json_body={"data": [traced, untraced], "meta": {"page": 1, "totalPages": 1}})
    api.reply(json_body={"name": "n", "observations": []})
    ((only,),), cursor = LangFuseClient("pk", "sk").fetch_v1_trace_page(_WINDOW)
    assert cursor is None
    assert (only.id, only.usage_details) == ("b", {"input": 3})
