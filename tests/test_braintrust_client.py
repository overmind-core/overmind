"""Braintrust BTQL client: pagination, envelope ambiguity, errors, pacing."""

import httpx
import pytest

from overbae.services.connectors.braintrust import client as bt_client
from overbae.services.connectors.braintrust.client import (
    BraintrustAuthError,
    BraintrustClient,
    BraintrustError,
    BraintrustRegionError,
    BraintrustTimeoutError,
    QueryPage,
    build_span_query,
)


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    """Real spacing would add 5s per request to every test."""
    monkeypatch.setattr(bt_client.time, "sleep", lambda _s: None)


def _response(status=200, json_body=None, text="", headers=None):
    request = httpx.Request("POST", "https://api.braintrust.dev/btql")
    if json_body is not None:
        return httpx.Response(status, json=json_body, headers=headers or {}, request=request)
    return httpx.Response(status, text=text, headers=headers or {}, request=request)


def _stub(monkeypatch, responses):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(bt_client.httpx, "request", fake_request)
    return calls


def test_query_uses_unversioned_btql_path_and_never_strict_lint(monkeypatch):
    calls = _stub(monkeypatch, [_response(json_body={"data": []})])
    BraintrustClient("bt-st-key").query("SELECT 1")

    assert calls[0]["url"] == "https://api.braintrust.dev/btql"
    assert calls[0]["json"] == {"query": "SELECT 1", "fmt": "json"}
    assert "lint_mode" not in calls[0]["json"]


def test_query_accepts_the_data_envelope_and_logs_warnings(monkeypatch):
    _stub(
        monkeypatch,
        [_response(json_body={"data": [{"id": "a"}], "schema": {}, "warnings": ["slow"]})],
    )
    page = BraintrustClient("k").query("SELECT 1")

    assert [r["id"] for r in page.rows] == ["a"]
    assert page.warnings == ["slow"]


def test_query_accepts_the_rows_envelope(monkeypatch):
    _stub(monkeypatch, [_response(json_body={"rows": [{"id": "b"}], "cursor": "c1"})])
    page = BraintrustClient("k").query("SELECT 1")

    assert [r["id"] for r in page.rows] == ["b"]
    assert page.cursor == "c1"


@pytest.mark.parametrize("header", ["x-bt-cursor", "x-amz-meta-bt_cursor"])
def test_cursor_is_read_from_either_header(monkeypatch, header):
    _stub(monkeypatch, [_response(json_body={"data": [{"id": "a"}]}, headers={header: "tok"})])
    assert BraintrustClient("k").query("SELECT 1").cursor == "tok"


def test_fetch_span_rows_pages_until_the_cursor_stops(monkeypatch):
    pages = [
        _response(json_body={"data": [{"id": "a"}]}, headers={"x-bt-cursor": "c1"}),
        _response(json_body={"data": [{"id": "b"}]}, headers={"x-bt-cursor": "c2"}),
        _response(json_body={"data": []}, headers={"x-bt-cursor": ""}),
    ]
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(kwargs)
        return pages[min(len(calls) - 1, len(pages) - 1)]

    monkeypatch.setattr(bt_client.httpx, "request", fake_request)
    page = BraintrustClient("k").fetch_span_rows(["p1"], where="created >= '2026-01-01'")

    assert [r["id"] for r in page.rows] == ["a", "b"]
    assert page.done is True
    assert "OFFSET" not in calls[0]["json"]["query"]
    assert "OFFSET 'c1'" in calls[1]["json"]["query"]


def test_fetch_span_rows_rejects_a_repeated_cursor(monkeypatch):
    _stub(
        monkeypatch,
        [_response(json_body={"data": [{"id": "a"}]}, headers={"x-bt-cursor": "same"})] * 2,
    )
    with pytest.raises(BraintrustError, match="repeated BTQL cursor"):
        BraintrustClient("k").fetch_span_rows(["p1"], where="created >= '2026-01-01'")


def test_page_ceiling_returns_a_resume_cursor_instead_of_completing(monkeypatch):
    calls = []

    def fake_query(self, sql):
        calls.append(sql)
        number = len(calls)
        return QueryPage(rows=[{"id": str(number)}], cursor=f"cursor-{number}")

    monkeypatch.setattr(BraintrustClient, "query", fake_query)
    page = BraintrustClient("k").fetch_span_rows(["p1"], where="created >= '2026-01-01'")

    assert len(calls) == 200
    assert page.done is False
    assert page.cursor == "cursor-200"


def test_resume_cursor_continues_a_capped_fetch(monkeypatch):
    calls = []

    def fake_query(self, sql):
        calls.append(sql)
        if len(calls) == 1:
            return QueryPage(rows=[{"id": "a"}], cursor="resume")
        return QueryPage(rows=[], cursor=None)

    monkeypatch.setattr(BraintrustClient, "query", fake_query)
    first = BraintrustClient("k").fetch_span_rows(
        ["p1"], where="created >= '2026-01-01'", max_pages=1
    )
    second = BraintrustClient("k").fetch_span_rows(
        ["p1"],
        where="created >= '2026-01-01'",
        cursor=first.cursor,
    )

    assert first.done is False
    assert second.done is True
    assert "OFFSET 'resume'" in calls[1]


def test_429_honours_retry_after_then_succeeds(monkeypatch):
    slept = []
    monkeypatch.setattr(bt_client.time, "sleep", lambda s: slept.append(s))
    responses = [
        _response(429, text="Too many requests", headers={"Retry-After": "3"}),
        _response(json_body={"data": [{"id": "a"}]}),
    ]
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(kwargs)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(bt_client.httpx, "request", fake_request)
    page = BraintrustClient("k").query("SELECT 1")

    assert [r["id"] for r in page.rows] == ["a"]
    assert 3.0 in slept


def test_non_json_error_body_does_not_raise_a_decode_error(monkeypatch):
    _stub(monkeypatch, [_response(400, text="plain text failure")])
    with pytest.raises(BraintrustError, match="plain text failure"):
        BraintrustClient("k").query("SELECT 1")


def test_auth_failure_asks_for_rotation_and_is_not_retried(monkeypatch):
    calls = _stub(monkeypatch, [_response(401, text="bad key")])
    with pytest.raises(BraintrustAuthError, match="rotate") as caught:
        BraintrustClient("k").query("SELECT 1")

    assert len(calls) == 1
    assert caught.value.response.status_code == 401


def test_421_names_the_data_plane_to_switch_to_and_is_not_retried(monkeypatch):
    body = {
        "Code": "DataPlaneRedirectError",
        "Message": (
            'Your organization "Acme" is configured to use a different data plane. '
            "Please direct your requests to: https://api-eu.braintrust.dev"
        ),
    }
    calls = _stub(monkeypatch, [_response(421, json_body=body)])
    with pytest.raises(BraintrustRegionError) as caught:
        BraintrustClient("k").query("SELECT 1")

    assert "https://api-eu.braintrust.dev" in str(caught.value)
    assert "base URL" in str(caught.value)
    assert caught.value.response.status_code == 421
    # Retrying a misdirected request can only fail again.
    assert len(calls) == 1


def test_421_without_a_url_in_the_body_still_reports_the_wrong_host(monkeypatch):
    _stub(monkeypatch, [_response(421, text="misdirected")])
    with pytest.raises(BraintrustRegionError, match="https://api.braintrust.dev"):
        BraintrustClient("k").query("SELECT 1")


def test_pacing_spaces_requests(monkeypatch):
    slept = []
    monkeypatch.setattr(bt_client.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(bt_client.time, "monotonic", lambda: 0.0)
    _stub(monkeypatch, [_response(json_body={"data": []})])

    client = BraintrustClient("k", requests_per_minute=12)
    client.query("SELECT 1")
    client.query("SELECT 1")

    assert slept and slept[-1] == pytest.approx(5.0)


def test_base_url_is_configurable_for_eu_and_self_hosted(monkeypatch):
    calls = _stub(monkeypatch, [_response(json_body={"data": []})])
    BraintrustClient("k", base_url="https://api-eu.braintrust.dev/").query("SELECT 1")

    assert calls[0]["url"] == "https://api-eu.braintrust.dev/btql"


def test_span_query_shape_and_bounds():
    sql = build_span_query(["p1", "p2"], where="created >= '2026-01-01'", limit=100)

    assert "shape => 'traces'" in sql
    assert "project_logs('p1', 'p2'" in sql
    assert "LIMIT 100" in sql
    assert "SELECT *" not in sql
    assert "estimated_cost() AS estimated_cost" in sql


def test_span_query_rejects_an_unquotable_project_id():
    with pytest.raises(BraintrustError, match="Unsafe"):
        build_span_query(["p1' OR 1=1 --"], where="created >= '2026-01-01'")


def test_list_projects_stops_on_a_short_page(monkeypatch):
    responses = [
        _response(json_body={"objects": [{"id": f"p{i}", "name": f"P{i}"} for i in range(100)]}),
        _response(json_body={"objects": [{"id": "p100", "name": "P100"}]}),
    ]
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(kwargs)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(bt_client.httpx, "request", fake_request)
    projects = BraintrustClient("k").list_projects()

    assert len(projects) == 101
    assert calls[1]["params"]["starting_after"] == "p99"


def _script(monkeypatch, steps):
    """Each step is a response to return or an exception to raise, in order."""
    queries = []

    def fake_request(method, url, **kwargs):
        queries.append(kwargs.get("json", {}).get("query", ""))
        step = steps[min(len(queries) - 1, len(steps) - 1)]
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(bt_client.httpx, "request", fake_request)
    return queries


def test_a_timed_out_page_is_retried_on_a_smaller_trace_budget(monkeypatch):
    queries = _script(
        monkeypatch,
        [httpx.ReadTimeout("too slow"), _response(json_body={"data": [{"id": "a"}]})],
    )
    page = BraintrustClient("k").fetch_span_rows(["p1"], where="created >= '2026-01-01'")

    assert [r["id"] for r in page.rows] == ["a"]
    assert "LIMIT 100" in queries[0]
    # The cursor was never consumed, so the same page is safe to ask for again.
    assert "LIMIT 50" in queries[1]


def test_a_page_that_keeps_timing_out_gives_up_at_the_floor(monkeypatch):
    queries = _script(monkeypatch, [httpx.ReadTimeout("too slow")])
    with pytest.raises(BraintrustTimeoutError):
        BraintrustClient("k").fetch_span_rows(["p1"], where="created >= '2026-01-01'")

    limits = [int(q.rsplit("LIMIT ", 1)[1].split()[0]) for q in queries]
    assert limits == [100, 50, 25, 12, 6, 5]


def test_an_oversized_page_rebudgets_the_next_one(monkeypatch):
    wide = _response(
        json_body={"data": [{"id": str(i)} for i in range(4000)]},
        headers={"x-bt-cursor": "c1"},
    )
    queries = _script(monkeypatch, [wide, _response(json_body={"data": []})])
    BraintrustClient("k").fetch_span_rows(["p1"], where="created >= '2026-01-01'")

    assert "LIMIT 100" in queries[0]
    # 100 traces returned 4000 rows against a 2000-row cap, so halve the traces.
    assert "LIMIT 50" in queries[1]


def test_a_page_within_the_row_cap_keeps_the_full_budget(monkeypatch):
    ok = _response(
        json_body={"data": [{"id": str(i)} for i in range(300)]},
        headers={"x-bt-cursor": "c1"},
    )
    queries = _script(monkeypatch, [ok, _response(json_body={"data": []})])
    BraintrustClient("k").fetch_span_rows(["p1"], where="created >= '2026-01-01'")

    assert all("LIMIT 100" in q for q in queries)


def test_504_is_a_timeout_rather_than_a_retryable_server_error(monkeypatch):
    queries = _script(monkeypatch, [_response(504, text="gateway timeout")])
    with pytest.raises(BraintrustTimeoutError):
        BraintrustClient("k").query("SELECT 1")

    # Re-running an identical 30s query only spends the rate-limit budget again.
    assert len(queries) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
