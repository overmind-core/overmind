"""Unit checks for Langfuse window planner, probe, and v1 orderBy pin."""

from datetime import UTC, datetime, timedelta

import pytest

from overbae.services.connectors.langfuse.client import LangFuseClient, LangFuseError
from overbae.services.connectors.windows import TimeWindow, plan_windows


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
    assert (
        plan_windows(
            datetime(2026, 2, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        == []
    )


def test_probe_uses_v2_when_endpoint_exists(monkeypatch):
    def fake_get(self, path, **params):
        assert "/v2/" in path
        return {"data": []}

    monkeypatch.setattr(LangFuseClient, "_get", fake_get)
    assert LangFuseClient("pk", "sk").probe_capabilities() == "v2"


def test_probe_falls_back_to_v1_on_v2_404(monkeypatch):
    def fake_get(self, path, **params):
        if "/v2/" in path:
            raise LangFuseError("LangFuse API error 404: not found")
        raise AssertionError("v1 must not be probed during capability check")

    monkeypatch.setattr(LangFuseClient, "_get", fake_get)
    assert LangFuseClient("pk", "sk").probe_capabilities() == "v1"


def test_probe_raises_on_v2_auth_error(monkeypatch):
    def fake_get(self, path, **params):
        raise LangFuseError("Invalid LangFuse credentials (401 Unauthorized).", status_code=401)

    monkeypatch.setattr(LangFuseClient, "_get", fake_get)
    with pytest.raises(LangFuseError, match="401"):
        LangFuseClient("pk", "sk").probe_capabilities()


def test_v2_trace_page_completes_trees_and_keeps_the_cursor(monkeypatch):
    calls = []

    def fake_get(self, path, **params):
        calls.append(params)
        if params.get("traceId"):
            return {
                "data": [
                    {
                        "id": "root",
                        "traceId": "trace-1",
                        "type": "SPAN",
                        "startTime": "2026-01-01T00:00:00Z",
                        "isRootObservation": True,
                    },
                    {
                        "id": "child",
                        "traceId": "trace-1",
                        "parentObservationId": "root",
                        "type": "SPAN",
                        "startTime": "2026-01-01T00:01:00Z",
                    },
                ],
                "meta": {},
            }
        return {
            "data": [
                {
                    "id": "root",
                    "traceId": "trace-1",
                    "type": "SPAN",
                    "startTime": "2026-01-01T00:00:00Z",
                    "isRootObservation": True,
                }
            ],
            "meta": {"cursor": "next"},
        }

    monkeypatch.setattr(LangFuseClient, "_get", fake_get)
    client = LangFuseClient("pk", "sk")
    traces, cursor = client.fetch_v2_trace_page(
        TimeWindow(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 2, tzinfo=UTC),
        ),
        expand_metadata="capability",
    )

    assert [[observation.id for observation in trace] for trace in traces] == [["root", "child"]]
    assert cursor == "next"
    assert calls[0]["expandMetadata"] == "capability"
    assert calls[1]["traceId"] == "trace-1"
    assert calls[1]["expandMetadata"] == "capability"


def test_v2_trace_page_stops_a_repeated_cursor(monkeypatch):
    monkeypatch.setattr(
        LangFuseClient,
        "_get",
        lambda self, path, **params: {"data": [], "meta": {"cursor": params.get("cursor")}},
    )
    client = LangFuseClient("pk", "sk")

    _, cursor = client.fetch_v2_trace_page(
        TimeWindow(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 2, tzinfo=UTC),
        ),
        cursor="stuck",
    )

    assert cursor is None


class _Resp:
    def __init__(self, status_code, *, text="", headers=None, json_body=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.is_success = 200 <= status_code < 300
        self._json_body = json_body or {}

    def json(self):
        return self._json_body


def test_get_retries_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_httpx_get(url, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return _Resp(
                429,
                text='{"message":"Rate limit exceeded for GET /api/public/traces"}',
                headers={"Retry-After": "0"},
            )
        return _Resp(200, json_body={"data": [{"id": "t1"}]})

    monkeypatch.setattr("overbae.services.connectors.langfuse.client.httpx.get", fake_httpx_get)
    monkeypatch.setattr("overbae.services.connectors.langfuse.client.time.sleep", lambda s: None)
    data = LangFuseClient("pk", "sk")._get("/api/public/v2/observations", limit=1)
    assert data["data"][0]["id"] == "t1"
    assert calls["n"] == 3


def test_get_raises_after_429_retries(monkeypatch):
    monkeypatch.setattr(
        "overbae.services.connectors.langfuse.client.httpx.get",
        lambda *a, **k: _Resp(
            429,
            text='{"message":"Rate limit exceeded for GET /api/public/traces"}',
            headers={"Retry-After": "0"},
        ),
    )
    monkeypatch.setattr("overbae.services.connectors.langfuse.client.time.sleep", lambda s: None)
    with pytest.raises(LangFuseError, match="429") as exc_info:
        LangFuseClient("pk", "sk")._get("/api/public/v2/observations")
    assert exc_info.value.status_code == 429


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
