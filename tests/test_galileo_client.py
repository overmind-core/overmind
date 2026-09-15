"""Galileo REST client: auth header, composite id parsing, pagination, errors, pacing."""

import httpx
import pytest

from overbae.services.connectors.galileo import client as gl_client
from overbae.services.connectors.galileo.client import (
    GalileoAuthError,
    GalileoClient,
    GalileoError,
    GalileoTimeoutError,
    split_source_id,
)

_PROJECT = "11111111-1111-1111-1111-111111111111"
_STREAM = "22222222-2222-2222-2222-222222222222"
_SOURCE = f"{_PROJECT}:{_STREAM}"


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(gl_client.time, "sleep", lambda _s: None)


def _response(status=200, json_body=None, text="", headers=None):
    request = httpx.Request("POST", "https://api.galileo.ai/")
    if json_body is not None:
        return httpx.Response(status, json=json_body, headers=headers or {}, request=request)
    return httpx.Response(status, text=text, headers=headers or {}, request=request)


def _stub(monkeypatch, responses):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(gl_client.httpx, "request", fake_request)
    return calls


def test_split_source_id_parses_both_uuids():
    assert split_source_id(_SOURCE) == (_PROJECT, _STREAM)


def test_split_source_id_rejects_a_malformed_composite():
    with pytest.raises(GalileoError, match="Unsafe"):
        split_source_id("not-a-composite")
    with pytest.raises(GalileoError, match="Unsafe"):
        split_source_id(f"{_PROJECT}:not-a-uuid")


def test_client_sends_the_galileo_api_key_header(monkeypatch):
    calls = _stub(monkeypatch, [_response(json_body={"projects": []})])
    GalileoClient("gal-key").list_log_streams()

    assert calls[0]["headers"]["Galileo-API-Key"] == "gal-key"
    assert "Authorization" not in calls[0]["headers"]


def test_list_log_streams_flattens_project_and_stream_into_a_composite_id(monkeypatch):
    _stub(
        monkeypatch,
        [
            _response(
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
        ],
    )
    streams = GalileoClient("k").list_log_streams()

    assert streams[0].id == _SOURCE
    assert streams[0].name == "Demo / production"


def test_list_log_streams_pages_on_next_starting_token(monkeypatch):
    calls = _stub(
        monkeypatch,
        [
            _response(
                json_body={
                    "projects": [
                        {
                            "id": _PROJECT,
                            "name": "Demo",
                            "log_streams": [{"id": _STREAM, "name": "prod"}],
                        }
                    ],
                    "next_starting_token": 100,
                }
            ),
            _response(json_body={"projects": [], "next_starting_token": None}),
        ],
    )
    streams = GalileoClient("k").list_log_streams()

    assert len(streams) == 1
    assert calls[1]["params"]["starting_token"] == 100


def test_search_traces_sends_a_flat_body_with_column_id_date_filters(monkeypatch):
    calls = _stub(monkeypatch, [_response(json_body={"records": [], "next_starting_token": None})])
    from datetime import UTC, datetime

    GalileoClient("k").search_traces(
        _SOURCE,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 2, tzinfo=UTC),
        starting_token=5,
        limit=10,
    )

    body = calls[0]["json"]
    assert calls[0]["url"] == f"https://api.galileo.ai/v2/projects/{_PROJECT}/traces/search"
    assert body["log_stream_id"] == _STREAM
    assert body["starting_token"] == 5
    assert body["limit"] == 10
    assert "pagination" not in body
    assert body["filters"][0] == {
        "type": "date",
        "column_id": "created_at",
        "operator": "gte",
        "value": "2026-01-01T00:00:00+00:00",
    }


def test_search_traces_reads_the_next_starting_token(monkeypatch):
    _stub(
        monkeypatch,
        [_response(json_body={"records": [{"id": "t1"}], "next_starting_token": 25})],
    )
    from datetime import UTC, datetime

    page = GalileoClient("k").search_traces(
        _SOURCE,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert [r["id"] for r in page.rows] == ["t1"]
    assert page.next_starting_token == 25


def test_count_traces_scopes_the_request_to_the_selected_stream(monkeypatch):
    calls = _stub(monkeypatch, [_response(json_body={"total_count": 12})])
    from datetime import UTC, datetime

    count = GalileoClient("k").count_traces(
        _SOURCE,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert count == 12
    assert calls[0]["url"] == f"https://api.galileo.ai/v2/projects/{_PROJECT}/traces/count"
    assert calls[0]["json"]["log_stream_id"] == _STREAM
    assert calls[0]["json"]["filters"][0]["column_id"] == "created_at"


def test_get_trace_returns_none_for_a_stub_trace(monkeypatch):
    _stub(monkeypatch, [_response(json_body={"type": "stub_trace", "id": "t1"})])
    assert GalileoClient("k").get_trace(_SOURCE, "t1") is None


def test_get_trace_returns_the_body_for_a_real_trace(monkeypatch):
    calls = _stub(monkeypatch, [_response(json_body={"type": "trace", "id": "t1", "spans": []})])
    tree = GalileoClient("k").get_trace(_SOURCE, "t1")

    assert tree == {"type": "trace", "id": "t1", "spans": []}
    assert calls[0]["url"] == f"https://api.galileo.ai/v2/projects/{_PROJECT}/traces/t1"
    assert calls[0]["method"] == "GET"


def test_iter_trace_trees_hydrates_each_matched_trace(monkeypatch):
    responses = [
        _response(json_body={"records": [{"id": "t1"}, {"id": "t2"}], "next_starting_token": None}),
        _response(json_body={"type": "trace", "id": "t1"}),
        _response(json_body={"type": "stub_trace", "id": "t2"}),
    ]
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url})
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(gl_client.httpx, "request", fake_request)
    from datetime import UTC, datetime

    trees, next_token = GalileoClient("k").iter_trace_trees(
        _SOURCE,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 2, tzinfo=UTC),
    )

    # t2 is a stub trace and is dropped rather than imported half-formed.
    assert [t["id"] for t in trees] == ["t1"]
    assert next_token is None
    assert len(calls) == 3


def test_401_is_an_auth_error_and_is_not_retried(monkeypatch):
    calls = _stub(monkeypatch, [_response(401, text="bad key")])
    with pytest.raises(GalileoAuthError, match="rejected") as caught:
        GalileoClient("k").list_log_streams()

    assert len(calls) == 1
    assert caught.value.response.status_code == 401


def test_504_is_a_timeout_rather_than_a_retryable_server_error(monkeypatch):
    calls = _stub(monkeypatch, [_response(504, text="gateway timeout")])
    with pytest.raises(GalileoTimeoutError):
        GalileoClient("k").list_log_streams()

    assert len(calls) == 1


def test_429_honours_retry_after_then_succeeds(monkeypatch):
    slept = []
    monkeypatch.setattr(gl_client.time, "sleep", lambda s: slept.append(s))
    responses = [
        _response(429, text="Too many requests", headers={"Retry-After": "3"}),
        _response(json_body={"projects": [], "next_starting_token": None}),
    ]
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(kwargs)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(gl_client.httpx, "request", fake_request)
    streams = GalileoClient("k").list_log_streams()

    assert streams == []
    assert 3.0 in slept


def test_non_json_error_body_does_not_raise_a_decode_error(monkeypatch):
    _stub(monkeypatch, [_response(400, text="plain text failure")])
    with pytest.raises(GalileoError, match="plain text failure"):
        GalileoClient("k").list_log_streams()


def test_pacing_spaces_requests(monkeypatch):
    slept = []
    monkeypatch.setattr(gl_client.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(gl_client.time, "monotonic", lambda: 0.0)
    _stub(monkeypatch, [_response(json_body={"projects": [], "next_starting_token": None})])

    client = GalileoClient("k", requests_per_minute=30)
    client.list_log_streams()
    client.list_log_streams()

    assert slept and slept[-1] == pytest.approx(2.0)


def test_base_url_is_configurable_for_self_hosted(monkeypatch):
    calls = _stub(monkeypatch, [_response(json_body={"projects": [], "next_starting_token": None})])
    GalileoClient("k", base_url="https://galileo.internal.example.com/").list_log_streams()

    assert calls[0]["url"] == "https://galileo.internal.example.com/v2/projects/paginated"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
