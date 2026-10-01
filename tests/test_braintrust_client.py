import httpx
import pytest

from overbae.services.connectors.braintrust.client import (
    BraintrustClient,
    BraintrustError,
    BraintrustRegionError,
    BraintrustTimeoutError,
    build_span_query,
)

_WHERE = "created >= '2026-01-01'"


@pytest.fixture
def api(scripted, slept):
    return scripted("https://api.braintrust.dev")


def _limits(api) -> list[int]:
    return [int(c.json["query"].rsplit("LIMIT ", 1)[1].split()[0]) for c in api.calls]


def test_query_uses_unversioned_btql_path_and_never_strict_lint(api):
    api.reply(json_body={"data": []})
    BraintrustClient("bt-st-key").query("SELECT 1")

    assert api.calls[0].url == "https://api.braintrust.dev/btql"
    assert api.calls[0].json == {"query": "SELECT 1", "fmt": "json"}


def test_query_accepts_the_data_envelope_and_logs_warnings(api):
    api.reply(json_body={"data": [{"id": "a"}], "schema": {}, "warnings": ["slow"]})
    page = BraintrustClient("k").query("SELECT 1")

    assert [r["id"] for r in page.rows] == ["a"]
    assert page.warnings == ["slow"]


def test_query_accepts_the_rows_envelope(api):
    api.reply(json_body={"rows": [{"id": "b"}], "cursor": "c1"})
    page = BraintrustClient("k").query("SELECT 1")

    assert [r["id"] for r in page.rows] == ["b"]
    assert page.cursor == "c1"


@pytest.mark.parametrize("header", ["x-bt-cursor", "x-amz-meta-bt_cursor"])
def test_cursor_is_read_from_either_header(api, header):
    api.reply(json_body={"data": [{"id": "a"}]}, headers={header: "tok"})
    assert BraintrustClient("k").query("SELECT 1").cursor == "tok"


def test_fetch_span_rows_pages_until_the_cursor_stops(api):
    api.reply(json_body={"data": [{"id": "a"}]}, headers={"x-bt-cursor": "c1"})
    api.reply(json_body={"data": [{"id": "b"}]}, headers={"x-bt-cursor": "c2"})
    api.reply(json_body={"data": []}, headers={"x-bt-cursor": ""})
    page = BraintrustClient("k").fetch_span_rows(["p1"], where=_WHERE)

    assert [r["id"] for r in page.rows] == ["a", "b"]
    assert page.done is True
    assert "OFFSET" not in api.calls[0].json["query"]
    assert "OFFSET 'c1'" in api.calls[1].json["query"]


def test_fetch_span_rows_rejects_a_repeated_cursor(api):
    api.reply(json_body={"data": [{"id": "a"}]}, headers={"x-bt-cursor": "same"})
    with pytest.raises(BraintrustError, match="repeated BTQL cursor"):
        BraintrustClient("k").fetch_span_rows(["p1"], where=_WHERE)


def test_page_ceiling_returns_a_resume_cursor_instead_of_completing(api):
    for number in range(1, 202):
        api.reply(json_body={"data": [{"id": str(number)}]}, headers={"x-bt-cursor": f"c-{number}"})
    page = BraintrustClient("k").fetch_span_rows(["p1"], where=_WHERE)

    assert len(api.calls) == 200
    assert page.done is False
    assert page.cursor == "c-200"


def test_resume_cursor_continues_a_capped_fetch(api):
    api.reply(json_body={"data": [{"id": "a"}]}, headers={"x-bt-cursor": "resume"})
    api.reply(json_body={"data": []})
    first = BraintrustClient("k").fetch_span_rows(["p1"], where=_WHERE, max_pages=1)
    second = BraintrustClient("k").fetch_span_rows(["p1"], where=_WHERE, cursor=first.cursor)

    assert first.done is False
    assert second.done is True
    assert "OFFSET 'resume'" in api.calls[1].json["query"]


def test_421_names_the_data_plane_to_switch_to_and_is_not_retried(api):
    api.reply(
        421,
        json_body={
            "Code": "DataPlaneRedirectError",
            "Message": (
                'Your organization "Acme" is configured to use a different data plane. '
                "Please direct your requests to: https://api-eu.braintrust.dev"
            ),
        },
    )
    with pytest.raises(BraintrustRegionError) as caught:
        BraintrustClient("k").query("SELECT 1")

    assert "https://api-eu.braintrust.dev" in str(caught.value)
    assert "base URL" in str(caught.value)
    assert caught.value.response.status_code == 421
    assert len(api.calls) == 1


def test_421_without_a_url_in_the_body_still_reports_the_wrong_host(api):
    api.reply(421, text="misdirected")
    with pytest.raises(BraintrustRegionError, match="https://api.braintrust.dev"):
        BraintrustClient("k").query("SELECT 1")


def test_span_query_shape_and_bounds():
    sql = build_span_query(["p1", "p2"], where=_WHERE, limit=100)

    assert "shape => 'traces'" in sql
    assert "project_logs('p1', 'p2'" in sql
    assert "LIMIT 100" in sql
    assert "SELECT *" not in sql
    assert "estimated_cost() AS estimated_cost" in sql


def test_span_query_rejects_an_unquotable_project_id():
    with pytest.raises(BraintrustError, match="Unsafe"):
        build_span_query(["p1' OR 1=1 --"], where=_WHERE)


def test_list_projects_stops_on_a_short_page(api):
    api.reply(json_body={"objects": [{"id": f"p{i}", "name": f"P{i}"} for i in range(100)]})
    api.reply(json_body={"objects": [{"id": "p100", "name": "P100"}]})
    projects = BraintrustClient("k").list_projects()

    assert len(projects) == 101
    assert api.calls[1].params["starting_after"] == "p99"


def test_a_timed_out_page_is_retried_on_a_smaller_trace_budget(api):
    api.fail(httpx.ReadTimeout("too slow")).reply(json_body={"data": [{"id": "a"}]})
    page = BraintrustClient("k").fetch_span_rows(["p1"], where=_WHERE)

    assert [r["id"] for r in page.rows] == ["a"]
    assert _limits(api) == [100, 50]


def test_a_page_that_keeps_timing_out_gives_up_at_the_floor(api):
    api.fail(httpx.ReadTimeout("too slow"))
    with pytest.raises(BraintrustTimeoutError):
        BraintrustClient("k").fetch_span_rows(["p1"], where=_WHERE)

    assert _limits(api) == [100, 50, 25, 12, 6, 5]


def test_an_oversized_page_rebudgets_the_next_one(api):
    api.reply(
        json_body={"data": [{"id": str(i)} for i in range(4000)]}, headers={"x-bt-cursor": "c1"}
    )
    api.reply(json_body={"data": []})
    BraintrustClient("k").fetch_span_rows(["p1"], where=_WHERE)

    assert _limits(api) == [100, 50]


def test_a_page_within_the_row_cap_keeps_the_full_budget(api):
    api.reply(
        json_body={"data": [{"id": str(i)} for i in range(300)]}, headers={"x-bt-cursor": "c1"}
    )
    api.reply(json_body={"data": []})
    BraintrustClient("k").fetch_span_rows(["p1"], where=_WHERE)

    assert _limits(api) == [100, 100]
