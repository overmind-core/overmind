"""LangSmith REST client: pagination, envelope shape, errors, pacing."""

from datetime import UTC, datetime

import httpx
import pytest

from overbae.services.connectors.langsmith import client as ls_client
from overbae.services.connectors.langsmith.client import (
    _SELECT,
    LangSmithAuthError,
    LangSmithClient,
    LangSmithError,
    LangSmithTimeoutError,
    validate_project_id,
)

_PROJECT = "11111111-1111-1111-1111-111111111111"
_MIN = datetime(2026, 1, 2, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(ls_client.time, "sleep", lambda _s: None)


def _response(
    status=200, json_body=None, text="", headers=None, url="https://api.smith.langchain.com/"
):
    request = httpx.Request("POST", url)
    if json_body is not None:
        return httpx.Response(status, json=json_body, headers=headers or {}, request=request)
    return httpx.Response(status, text=text, headers=headers or {}, request=request)


def _stub(monkeypatch, responses):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(ls_client.httpx, "request", fake_request)
    return calls


def test_client_strips_whitespace_and_a_bearer_prefix(monkeypatch):
    calls = _stub(monkeypatch, [_response(json_body={"items": []})])
    LangSmithClient("  Bearer lsv2_sk_key \n").query_runs([_PROJECT], min_start_time=_MIN)

    assert calls[0]["headers"]["x-api-key"] == "lsv2_sk_key"


def test_list_projects_does_not_send_content_type(monkeypatch):
    calls = _stub(monkeypatch, [_response(json_body=[])])
    LangSmithClient("k").list_projects()

    assert "Content-Type" not in calls[0]["headers"]


def test_query_sends_x_api_key_and_an_explicit_select_list(monkeypatch):
    calls = _stub(monkeypatch, [_response(json_body={"items": []})])
    LangSmithClient("lsv2_sk_key").query_runs([_PROJECT], min_start_time=_MIN, max_start_time=_MIN)

    headers = calls[0]["headers"]
    assert headers["x-api-key"] == "lsv2_sk_key"
    assert "Authorization" not in headers
    assert headers["Content-Type"] == "application/json"
    body = calls[0]["json"]
    assert body["session"] == [_PROJECT]
    assert "project_ids" not in body
    assert body["select"] == list(_SELECT)
    assert "parent_run_ids" in body["select"]
    assert "child_run_ids" not in body["select"]
    assert body["start_time"] == "2026-01-02T00:00:00Z"
    assert body["limit"] == 100
    assert "end_time" not in body
    assert "max_start_time" not in body
    assert "page_size" not in body
    assert calls[0]["url"] == "https://api.smith.langchain.com/api/v1/runs/query"


def test_query_keeps_the_zero_padded_year_for_an_unbounded_window(monkeypatch):
    calls = _stub(monkeypatch, [_response(json_body={"items": []})])
    LangSmithClient("k").query_runs(
        [_PROJECT],
        min_start_time=datetime.min.replace(tzinfo=UTC),
    )

    assert calls[0]["json"]["start_time"] == "0001-01-01T00:00:00Z"


def test_query_reads_the_v1_runs_envelope_and_cursors_next(monkeypatch):
    _stub(
        monkeypatch,
        [_response(json_body={"runs": [{"id": "a"}], "cursors": {"next": "c1"}})],
    )
    page = LangSmithClient("k").query_runs([_PROJECT], min_start_time=_MIN)

    assert [r["id"] for r in page.rows] == ["a"]
    assert page.cursor == "c1"


def test_query_falls_back_to_the_items_envelope(monkeypatch):
    _stub(monkeypatch, [_response(json_body={"items": [{"id": "b"}], "next_cursor": "c2"})])
    page = LangSmithClient("k").query_runs([_PROJECT], min_start_time=_MIN)

    assert [r["id"] for r in page.rows] == ["b"]
    assert page.cursor == "c2"


def test_iter_runs_pages_until_the_cursor_stops(monkeypatch):
    pages = [
        _response(json_body={"runs": [{"id": "a"}], "cursors": {"next": "c1"}}),
        _response(json_body={"runs": [{"id": "b"}], "cursors": {"next": "c2"}}),
        _response(json_body={"runs": [], "cursors": {}}),
    ]
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(kwargs)
        return pages[min(len(calls) - 1, len(pages) - 1)]

    monkeypatch.setattr(ls_client.httpx, "request", fake_request)
    rows, cursor = LangSmithClient("k").iter_runs([_PROJECT], min_start_time=_MIN)

    assert [r["id"] for r in rows] == ["a", "b"]
    assert cursor is None
    assert calls[0]["json"].get("cursor") is None
    assert calls[1]["json"]["cursor"] == "c1"


def test_iter_runs_stops_on_a_repeated_cursor(monkeypatch):
    _stub(
        monkeypatch, [_response(json_body={"runs": [{"id": "a"}], "cursors": {"next": "same"}})] * 2
    )
    rows, cursor = LangSmithClient("k").iter_runs([_PROJECT], min_start_time=_MIN)

    assert [r["id"] for r in rows] == ["a", "a"]
    assert cursor is None


def test_iter_runs_returns_the_cursor_when_the_page_cap_is_hit(monkeypatch):
    _stub(monkeypatch, [_response(json_body={"runs": [{"id": "a"}], "cursors": {"next": "c1"}})])
    rows, cursor = LangSmithClient("k").iter_runs([_PROJECT], min_start_time=_MIN, max_pages=1)

    assert [r["id"] for r in rows] == ["a"]
    assert cursor == "c1"


def test_429_honours_retry_after_then_succeeds(monkeypatch):
    slept = []
    monkeypatch.setattr(ls_client.time, "sleep", lambda s: slept.append(s))
    responses = [
        _response(429, text="Too many requests", headers={"Retry-After": "3"}),
        _response(json_body={"items": [{"id": "a"}]}),
    ]
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(kwargs)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(ls_client.httpx, "request", fake_request)
    page = LangSmithClient("k").query_runs([_PROJECT], min_start_time=_MIN)

    assert [r["id"] for r in page.rows] == ["a"]
    assert 3.0 in slept


def test_non_json_error_body_does_not_raise_a_decode_error(monkeypatch):
    _stub(monkeypatch, [_response(400, text="plain text failure")])
    with pytest.raises(LangSmithError, match="plain text failure"):
        LangSmithClient("k").query_runs([_PROJECT], min_start_time=_MIN)


def test_auth_failure_asks_for_rotation_and_is_not_retried(monkeypatch):
    calls = _stub(monkeypatch, [_response(401, text="bad key")])
    with pytest.raises(LangSmithAuthError, match="rejected the API key") as caught:
        LangSmithClient("k").query_runs([_PROJECT], min_start_time=_MIN)

    assert len(calls) == 1
    assert caught.value.response.status_code == 401


def test_504_is_a_timeout_rather_than_a_retryable_server_error(monkeypatch):
    calls = _stub(monkeypatch, [_response(504, text="gateway timeout")])
    with pytest.raises(LangSmithTimeoutError):
        LangSmithClient("k").query_runs([_PROJECT], min_start_time=_MIN)

    assert len(calls) == 1


def test_pacing_spaces_requests(monkeypatch):
    slept = []
    monkeypatch.setattr(ls_client.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ls_client.time, "monotonic", lambda: 0.0)
    _stub(monkeypatch, [_response(json_body={"items": []})])

    client = LangSmithClient("k", requests_per_minute=12)
    client.query_runs([_PROJECT], min_start_time=_MIN)
    client.query_runs([_PROJECT], min_start_time=_MIN)

    assert slept and slept[-1] == pytest.approx(5.0)


def test_base_url_is_configurable_for_eu_and_self_hosted(monkeypatch):
    calls = _stub(monkeypatch, [_response(json_body={"items": []})])
    LangSmithClient("k", base_url="https://eu.api.smith.langchain.com/").query_runs(
        [_PROJECT], min_start_time=_MIN
    )

    assert calls[0]["url"] == "https://eu.api.smith.langchain.com/api/v1/runs/query"


def test_query_rejects_an_unparseable_project_id():
    with pytest.raises(LangSmithError, match="Unsafe"):
        validate_project_id("not a uuid")


def test_list_projects_reads_a_top_level_array_and_stops_on_a_short_page(monkeypatch):
    responses = [
        _response(
            json_body=[
                {"id": f"{i:08x}-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": f"P{i}"} for i in range(100)
            ]
        ),
        _response(json_body=[{"id": "00000064-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": "P100"}]),
    ]
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"url": url, **kwargs})
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(ls_client.httpx, "request", fake_request)
    projects = LangSmithClient("k").list_projects()

    assert len(projects) == 101
    assert calls[0]["url"].endswith("/api/v1/sessions")
    assert calls[0]["params"]["reference_free"] == "true"
    assert calls[0]["params"]["limit"] == 100
    assert calls[1]["params"]["offset"] == 100


def test_list_projects_does_not_parse_an_objects_envelope(monkeypatch):
    """The sessions API is a top-level array, not Braintrust's {objects: [...]}."""
    _stub(
        monkeypatch,
        [
            _response(
                json_body={
                    "objects": [{"id": "11111111-1111-1111-1111-111111111111", "name": "Hidden"}]
                }
            )
        ],
    )
    assert LangSmithClient("k").list_projects() == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
