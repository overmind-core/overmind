from datetime import UTC, datetime

import pytest

from overbae.services.connectors.langsmith.client import (
    LangSmithClient,
    LangSmithError,
    validate_project_id,
)

_PROJECT = "11111111-1111-1111-1111-111111111111"
_MIN = datetime(2026, 1, 2, tzinfo=UTC)


@pytest.fixture
def api(scripted, slept):
    return scripted("https://api.smith.langchain.com")


def test_list_projects_does_not_send_content_type(api):
    api.reply(json_body=[])
    LangSmithClient("k").list_projects()

    assert "content-type" not in api.calls[0].headers


def test_query_sends_x_api_key_and_an_explicit_select_list(api):
    api.reply(json_body={"items": []})
    LangSmithClient("lsv2_sk_key").query_runs([_PROJECT], min_start_time=_MIN, max_start_time=_MIN)

    call = api.calls[0]
    assert call.headers["x-api-key"] == "lsv2_sk_key"
    assert "authorization" not in call.headers
    assert call.headers["content-type"] == "application/json"
    body = call.json
    assert body["session"] == [_PROJECT]
    assert "project_ids" not in body
    assert "parent_run_ids" in body["select"]
    assert "child_run_ids" not in body["select"]
    assert body["start_time"] == "2026-01-02T00:00:00Z"
    assert body["limit"] == 100
    assert "end_time" not in body
    assert "max_start_time" not in body
    assert "page_size" not in body
    assert call.url == "https://api.smith.langchain.com/api/v1/runs/query"


def test_query_keeps_the_zero_padded_year_for_an_unbounded_window(api):
    api.reply(json_body={"items": []})
    LangSmithClient("k").query_runs([_PROJECT], min_start_time=datetime.min.replace(tzinfo=UTC))

    assert api.calls[0].json["start_time"] == "0001-01-01T00:00:00Z"


def test_query_reads_the_v1_runs_envelope_and_cursors_next(api):
    api.reply(json_body={"runs": [{"id": "a"}], "cursors": {"next": "c1"}})
    page = LangSmithClient("k").query_runs([_PROJECT], min_start_time=_MIN)

    assert [r["id"] for r in page.rows] == ["a"]
    assert page.cursor == "c1"


def test_query_falls_back_to_the_items_envelope(api):
    api.reply(json_body={"items": [{"id": "b"}], "next_cursor": "c2"})
    page = LangSmithClient("k").query_runs([_PROJECT], min_start_time=_MIN)

    assert [r["id"] for r in page.rows] == ["b"]
    assert page.cursor == "c2"


def test_iter_runs_pages_until_the_cursor_stops(api):
    api.reply(json_body={"runs": [{"id": "a"}], "cursors": {"next": "c1"}})
    api.reply(json_body={"runs": [{"id": "b"}], "cursors": {"next": "c2"}})
    api.reply(json_body={"runs": [], "cursors": {}})
    rows, cursor = LangSmithClient("k").iter_runs([_PROJECT], min_start_time=_MIN)

    assert [r["id"] for r in rows] == ["a", "b"]
    assert cursor is None
    assert api.calls[0].json.get("cursor") is None
    assert api.calls[1].json["cursor"] == "c1"


def test_iter_runs_stops_on_a_repeated_cursor(api):
    api.reply(json_body={"runs": [{"id": "a"}], "cursors": {"next": "same"}})
    rows, cursor = LangSmithClient("k").iter_runs([_PROJECT], min_start_time=_MIN)

    assert [r["id"] for r in rows] == ["a", "a"]
    assert cursor is None


def test_iter_runs_returns_the_cursor_when_the_page_cap_is_hit(api):
    api.reply(json_body={"runs": [{"id": "a"}], "cursors": {"next": "c1"}})
    rows, cursor = LangSmithClient("k").iter_runs([_PROJECT], min_start_time=_MIN, max_pages=1)

    assert [r["id"] for r in rows] == ["a"]
    assert cursor == "c1"


def test_query_rejects_an_unparseable_project_id():
    with pytest.raises(LangSmithError, match="Unsafe"):
        validate_project_id("not a uuid")


def test_list_projects_reads_a_top_level_array_and_stops_on_a_short_page(api):
    api.reply(
        json_body=[
            {"id": f"{i:08x}-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": f"P{i}"} for i in range(100)
        ]
    )
    api.reply(json_body=[{"id": "00000064-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": "P100"}])
    projects = LangSmithClient("k").list_projects()

    assert len(projects) == 101
    assert api.calls[0].path == "/api/v1/sessions"
    assert api.calls[0].params["reference_free"] == "true"
    assert api.calls[0].params["limit"] == "100"
    assert api.calls[1].params["offset"] == "100"


def test_list_projects_does_not_parse_an_objects_envelope(api):
    api.reply(
        json_body={"objects": [{"id": "11111111-1111-1111-1111-111111111111", "name": "Hidden"}]}
    )
    assert LangSmithClient("k").list_projects() == []
