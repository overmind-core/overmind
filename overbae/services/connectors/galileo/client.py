"""Galileo read client — trace search plus per-trace span tree fetch and log-stream listing.

Auth is a single ``Galileo-API-Key`` header; the org is implicit in the key.
Default host ``https://api.galileo.ai``; self-hosted is a custom ``base_url``.

``traces/search`` returns flat trace metadata with no spans; the tree comes from
a second call per trace id, ``GET /v2/projects/{project_id}/traces/{trace_id}``.
Traces live under a project's log stream (not the project itself), and the
wizard has one source-project picker, so a source id here is the composite
``"{project_id}:{log_stream_id}"``.

Verified against api.galileo.ai/public/v2/openapi.json (Aug 2026):
- Auth header name is ``Galileo-API-Key`` (the OAuth2/basic paths in the spec
  are for the console, not machine keys).
- ``traces/search`` and ``traces/count`` request bodies are flat
  (``log_stream_id``, ``filters``, ``starting_token``, ``limit``) — not nested
  under a ``pagination`` object, despite some doc examples showing that shape.
- ``LogRecordsDateFilter`` keys its column as ``column_id``, not ``name``.
- A trace whose root span never arrived comes back as ``type: "stub_trace"``
  with no metadata beyond its child spans; treated as unavailable rather than
  imported half-formed.
- No published rate limit; paced like LangSmith's conservative default.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.galileo.ai"
DEFAULT_REQUESTS_PER_MINUTE = 30
_HTTP_TIMEOUT_SECONDS = 90
_MAX_RETRIES = 5
_MAX_RETRY_AFTER = 60.0
_PROJECT_PAGE_SIZE = 100
DEFAULT_SEARCH_LIMIT = 25


class GalileoError(Exception):
    pass


class GalileoAuthError(GalileoError):
    """Credential is rejected or lacks scope — it must be rotated, not retried."""

    def __init__(self, message: str, *, status_code: int = 401):
        super().__init__(message)
        # connector_sync._apply_backoff reads exc.response.status_code, not a
        # bare status_code attribute.
        self.response = SimpleNamespace(status_code=status_code)


class GalileoTimeoutError(GalileoError):
    """The query did not finish. Retrying it unchanged just spends the budget again."""


@dataclass(frozen=True)
class GalileoLogStream:
    id: str  # "{project_id}:{log_stream_id}"
    name: str  # "{project name} / {log stream name}"


@dataclass
class QueryPage:
    rows: list[dict[str, Any]]
    next_starting_token: int | None = None


class _Pacer:
    """Client-side request spacing."""

    def __init__(self, requests_per_minute: int):
        self._interval = 60.0 / max(1, requests_per_minute)
        self._last = 0.0

    def wait(self) -> None:
        gap = self._interval - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def split_source_id(source_id: str) -> tuple[str, str]:
    """``"{project_id}:{log_stream_id}"`` -> validated ``(project_id, log_stream_id)``."""
    project_id, sep, log_stream_id = (source_id or "").partition(":")
    if not sep:
        raise GalileoError(f"Unsafe Galileo source project identifier: {source_id!r}")
    try:
        return str(uuid.UUID(project_id)), str(uuid.UUID(log_stream_id))
    except (TypeError, ValueError) as exc:
        raise GalileoError(f"Unsafe Galileo source project identifier: {source_id!r}") from exc


def _date_filter(column_id: str, operator: str, value: datetime) -> dict[str, Any]:
    return {
        "type": "date",
        "column_id": column_id,
        "operator": operator,
        "value": _rfc3339(value),
    }


def _window_filters(window_start: datetime, window_end: datetime) -> list[dict[str, Any]]:
    return [
        _date_filter("created_at", "gte", window_start),
        _date_filter("created_at", "lt", window_end),
    ]


class GalileoClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "",
        *,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
    ):
        self.base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        self._headers = {
            "Galileo-API-Key": (api_key or "").strip(),
            "Accept": "application/json",
        }
        self._pacer = _Pacer(requests_per_minute)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = dict(self._headers)
        if "json" in kwargs:
            headers["Content-Type"] = "application/json"
        delay = 1.0
        for attempt in range(_MAX_RETRIES):
            self._pacer.wait()
            try:
                resp = httpx.request(
                    method, url, headers=headers, timeout=_HTTP_TIMEOUT_SECONDS, **kwargs
                )
            except httpx.TimeoutException as exc:
                raise GalileoTimeoutError(
                    f"Galileo did not answer within {_HTTP_TIMEOUT_SECONDS}s: {exc}"
                ) from exc
            except httpx.RequestError as exc:
                raise GalileoError(f"Network error reaching Galileo: {exc}") from exc

            if resp.status_code == 504:
                raise GalileoTimeoutError(
                    f"Galileo timed out serving the query (504): {_error_detail(resp)}"
                )
            if resp.status_code in (401, 403):
                raise GalileoAuthError(
                    f"Galileo rejected the API key ({resp.status_code}): {_error_detail(resp)}",
                    status_code=resp.status_code,
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == _MAX_RETRIES - 1:
                    break
                time.sleep(_retry_after(resp, delay))
                delay = min(delay * 2, _MAX_RETRY_AFTER)
                continue
            if not resp.is_success:
                raise GalileoError(f"Galileo API error {resp.status_code}: {_error_detail(resp)}")
            return resp

        raise GalileoError(
            f"Galileo API error {resp.status_code} after {_MAX_RETRIES} attempts: "
            f"{_error_detail(resp)}"
        )

    def list_log_streams(self) -> list[GalileoLogStream]:
        """Every log stream across every project. Experiments are never listed."""
        streams: list[GalileoLogStream] = []
        starting_token = 0
        while True:
            body = self._request(
                "POST",
                "/v2/projects/paginated",
                params={
                    "include_logstreams": "true",
                    "limit": _PROJECT_PAGE_SIZE,
                    "starting_token": starting_token,
                },
                json={},
            ).json()
            projects = body.get("projects") if isinstance(body, dict) else None
            projects = projects or []
            for project in projects:
                project_id = project.get("id")
                project_name = project.get("name") or project_id
                if not project_id:
                    continue
                for log_stream in project.get("log_streams") or []:
                    stream_id = log_stream.get("id")
                    if not stream_id:
                        continue
                    streams.append(
                        GalileoLogStream(
                            id=f"{project_id}:{stream_id}",
                            name=f"{project_name} / {log_stream.get('name') or stream_id}",
                        )
                    )
            next_token = body.get("next_starting_token") if isinstance(body, dict) else None
            if next_token is None or not projects:
                return streams
            starting_token = next_token

    def search_traces(
        self,
        source_id: str,
        *,
        window_start: datetime,
        window_end: datetime,
        starting_token: int = 0,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> QueryPage:
        project_id, log_stream_id = split_source_id(source_id)
        body = {
            "log_stream_id": log_stream_id,
            "filters": _window_filters(window_start, window_end),
            "starting_token": starting_token,
            "limit": limit,
        }
        resp = self._request("POST", f"/v2/projects/{project_id}/traces/search", json=body)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise GalileoError(f"Galileo returned a non-JSON traces response: {exc}") from exc
        records = payload.get("records") if isinstance(payload, dict) else None
        return QueryPage(
            rows=[r for r in (records or []) if isinstance(r, dict)],
            next_starting_token=payload.get("next_starting_token")
            if isinstance(payload, dict)
            else None,
        )

    def count_traces(
        self,
        source_id: str,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> int:
        project_id, log_stream_id = split_source_id(source_id)
        payload = self._request(
            "POST",
            f"/v2/projects/{project_id}/traces/count",
            json={
                "log_stream_id": log_stream_id,
                "filters": _window_filters(window_start, window_end),
            },
        ).json()
        if not isinstance(payload, dict) or not isinstance(payload.get("total_count"), int):
            raise GalileoError("Galileo returned an unexpected trace-count response.")
        return payload["total_count"]

    def get_trace(self, source_id: str, trace_id: str) -> dict[str, Any] | None:
        """The full span tree for one trace, or ``None`` for a synthesized stub."""
        project_id, _log_stream_id = split_source_id(source_id)
        resp = self._request("GET", f"/v2/projects/{project_id}/traces/{trace_id}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise GalileoError(f"Galileo returned a non-JSON trace response: {exc}") from exc
        if not isinstance(body, dict) or body.get("type") == "stub_trace":
            return None
        return body

    def iter_trace_trees(
        self,
        source_id: str,
        *,
        window_start: datetime,
        window_end: datetime,
        starting_token: int = 0,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """One search page, hydrated into full span trees. Returns (trees, next_starting_token)."""
        page = self.search_traces(
            source_id,
            window_start=window_start,
            window_end=window_end,
            starting_token=starting_token,
            limit=limit,
        )
        trees: list[dict[str, Any]] = []
        for record in page.rows:
            trace_id = record.get("id")
            if not trace_id:
                continue
            tree = self.get_trace(source_id, trace_id)
            if tree is not None:
                trees.append(tree)
        return trees, page.next_starting_token


def _error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(body, dict):
        return str(body.get("detail") or body.get("message") or body)[:200]
    return str(body)[:200]


def _retry_after(resp: httpx.Response, fallback: float) -> float:
    raw = resp.headers.get("Retry-After")
    try:
        return min(max(float(raw), 0.0), _MAX_RETRY_AFTER)
    except (TypeError, ValueError):
        return fallback
