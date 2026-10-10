from datetime import UTC, datetime

import pytest

from overbae.services.connectors.galileo.client import GalileoClient, GalileoError, split_source_id

_PROJECT = "11111111-1111-1111-1111-111111111111"
_STREAM = "22222222-2222-2222-2222-222222222222"
_SOURCE = f"{_PROJECT}:{_STREAM}"
_WINDOW = {
    "window_start": datetime(2026, 1, 1, tzinfo=UTC),
    "window_end": datetime(2026, 1, 2, tzinfo=UTC),
}


@pytest.fixture
def api(scripted, slept):
    return scripted("https://api.galileo.ai")


def test_split_source_id_parses_both_uuids():
    assert split_source_id(_SOURCE) == (_PROJECT, _STREAM)


def test_split_source_id_rejects_a_malformed_composite():
    with pytest.raises(GalileoError, match="Unsafe"):
        split_source_id("not-a-composite")
    with pytest.raises(GalileoError, match="Unsafe"):
        split_source_id(f"{_PROJECT}:not-a-uuid")


def test_list_log_streams_flattens_project_and_stream_into_a_composite_id(api):
    api.reply(
        json_body={
            "projects": [
                {
                    "id": _PROJECT,
                    "name": "Demo",
                    "log_streams": [{"id": _STREAM, "name": "production"}],
                }
            ],
            "next_starting_token": None,
        }
    )
    streams = GalileoClient("k").list_log_streams()

    assert streams[0].id == _SOURCE
    assert streams[0].name == "Demo / production"


def test_list_log_streams_pages_on_next_starting_token(api):
    api.reply(
        json_body={
            "projects": [
                {"id": _PROJECT, "name": "Demo", "log_streams": [{"id": _STREAM, "name": "prod"}]}
            ],
            "next_starting_token": 100,
        }
    )
    api.reply(json_body={"projects": [], "next_starting_token": None})
    streams = GalileoClient("k").list_log_streams()

    assert len(streams) == 1
    assert api.calls[1].params["starting_token"] == "100"


def test_search_traces_sends_a_flat_body_with_column_id_date_filters(api):
    api.reply(json_body={"records": [], "next_starting_token": None})
    GalileoClient("k").search_traces(_SOURCE, starting_token=5, limit=10, **_WINDOW)

    call = api.calls[0]
    assert call.url == f"https://api.galileo.ai/v2/projects/{_PROJECT}/traces/search"
    assert call.json["log_stream_id"] == _STREAM
    assert call.json["starting_token"] == 5
    assert call.json["limit"] == 10
    assert "pagination" not in call.json
    assert call.json["filters"][0] == {
        "type": "date",
        "column_id": "created_at",
        "operator": "gte",
        "value": "2026-01-01T00:00:00+00:00",
    }


def test_search_traces_reads_the_next_starting_token(api):
    api.reply(json_body={"records": [{"id": "t1"}], "next_starting_token": 25})
    page = GalileoClient("k").search_traces(_SOURCE, **_WINDOW)

    assert [r["id"] for r in page.rows] == ["t1"]
    assert page.next_starting_token == 25


def test_count_traces_scopes_the_request_to_the_selected_stream(api):
    api.reply(json_body={"total_count": 12})
    count = GalileoClient("k").count_traces(_SOURCE, **_WINDOW)

    assert count == 12
    assert api.calls[0].url == f"https://api.galileo.ai/v2/projects/{_PROJECT}/traces/count"
    assert api.calls[0].json["log_stream_id"] == _STREAM
    assert api.calls[0].json["filters"][0]["column_id"] == "created_at"


def test_get_trace_returns_none_for_a_stub_trace(api):
    api.reply(json_body={"type": "stub_trace", "id": "t1"})
    assert GalileoClient("k").get_trace(_SOURCE, "t1") is None


def test_get_trace_returns_the_body_for_a_real_trace(api):
    api.reply(json_body={"type": "trace", "id": "t1", "spans": []})
    tree = GalileoClient("k").get_trace(_SOURCE, "t1")

    assert tree == {"type": "trace", "id": "t1", "spans": []}
    assert api.calls[0].url == f"https://api.galileo.ai/v2/projects/{_PROJECT}/traces/t1"
    assert api.calls[0].method == "GET"


def test_iter_trace_trees_drops_a_stub_trace_rather_than_importing_it_half_formed(api):
    api.reply(json_body={"records": [{"id": "t1"}, {"id": "t2"}], "next_starting_token": None})
    api.reply(json_body={"type": "trace", "id": "t1"})
    api.reply(json_body={"type": "stub_trace", "id": "t2"})
    trees, next_token = GalileoClient("k").iter_trace_trees(_SOURCE, **_WINDOW)

    assert [t["id"] for t in trees] == ["t1"]
    assert next_token is None
    assert len(api.calls) == 3
