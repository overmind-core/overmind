"""LangSmith read client — ``POST /api/v1/runs/query`` plus session listing.

Auth is a single ``x-api-key``. Workspace-scoped service keys, personal
access tokens, and legacy unprefixed keys are all valid for read.

Verified against docs.langchain.com/langsmith (Aug 2026):
- ``POST /api/v1/runs/query`` is the v1 body: ``session`` (project UUIDs),
  ``select``, ``limit``, ``start_time``. v2's ``project_ids`` is ignored here
  and the request 400s without ``session``.
- Pages via ``cursors.next`` (SDK) with a fallback to ``next_cursor``.
- ``GET /api/v1/sessions`` returns a top-level JSON array.
- Short windows (≤ 7 days) get 10 requests / 10s; longer windows get 3.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.smith.langchain.com"
# Short-window cap is 10 / 10s. 30/min is 5 / 10s, leaving room for retries
# and the customer's own SDK.
DEFAULT_REQUESTS_PER_MINUTE = 30
PAGE_SIZE = 100
_PROJECT_PAGE_SIZE = 100
_HTTP_TIMEOUT_SECONDS = 90
_MAX_RETRIES = 5
_MAX_RETRY_AFTER = 60.0
_MAX_PAGES_PER_SLICE = 50

_SELECT = (
    "id",
    "name",
    "run_type",
    "status",
    "start_time",
    "end_time",
    "error",
    "extra",
    "inputs",
    "outputs",
    "tags",
    "trace_id",
    "thread_id",
    "dotted_order",
    "parent_run_ids",
    "parent_run_id",
    "reference_example_id",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_cost",
    "first_token_time",
)


class LangSmithError(Exception):
    pass


class LangSmithAuthError(LangSmithError):
    """Credential is rejected or lacks scope — it must be rotated, not retried."""

    def __init__(self, message: str, *, status_code: int = 401):
        super().__init__(message)
        # connector_sync._apply_backoff reads exc.response.status_code, not a
        # bare status_code attribute.
        self.response = SimpleNamespace(status_code=status_code)


class LangSmithTimeoutError(LangSmithError):
    """The query did not finish. Retrying it unchanged just spends the budget again."""


@dataclass(frozen=True)
class LangSmithProject:
    id: str
    name: str


@dataclass
class QueryPage:
    rows: list[dict[str, Any]]
    cursor: str | None = None


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
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def trace_filter_for(window_start: datetime, window_end: datetime) -> str:
    """Roots whose start_time is in [*window_start*, *window_end*)."""
    return (
        f'and(gte(start_time, "{_rfc3339(window_start)}"), '
        f'lt(start_time, "{_rfc3339(window_end)}"))'
    )


def validate_project_id(project_id: str) -> str:
    try:
        return str(uuid.UUID(str(project_id)))
    except (TypeError, ValueError) as exc:
        raise LangSmithError(f"Unsafe LangSmith project identifier: {project_id!r}") from exc


class LangSmithClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "",
        *,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
    ):
        key = (api_key or "").strip()
        if key.lower().startswith("bearer "):
            key = key[7:].strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        self._headers = {
            "x-api-key": key,
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
                raise LangSmithTimeoutError(
                    f"LangSmith did not answer within {_HTTP_TIMEOUT_SECONDS}s: {exc}"
                ) from exc
            except httpx.RequestError as exc:
                raise LangSmithError(f"Network error reaching LangSmith: {exc}") from exc

            if resp.status_code == 504:
                raise LangSmithTimeoutError(
                    f"LangSmith timed out serving the query (504): {_error_detail(resp)}"
                )
            if resp.status_code in (401, 403):
                raise LangSmithAuthError(
                    "LangSmith rejected the API key. If this workspace is not US, set "
                    "the API URL (EU is eu.api.smith.langchain.com).",
                    status_code=resp.status_code,
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == _MAX_RETRIES - 1:
                    break
                time.sleep(_retry_after(resp, delay))
                delay = min(delay * 2, _MAX_RETRY_AFTER)
                continue
            if not resp.is_success:
                raise LangSmithError(
                    f"LangSmith API error {resp.status_code}: {_error_detail(resp)}"
                )
            return resp

        raise LangSmithError(
            f"LangSmith API error {resp.status_code} after {_MAX_RETRIES} attempts: "
            f"{_error_detail(resp)}"
        )

    def list_projects(self) -> list[LangSmithProject]:
        """Every tracing project. Experiments are excluded via ``reference_free``."""
        projects: list[LangSmithProject] = []
        offset = 0
        while True:
            resp = self._request(
                "GET",
                "/api/v1/sessions",
                params={
                    "limit": _PROJECT_PAGE_SIZE,
                    "offset": offset,
                    "reference_free": "true",
                },
            )
            try:
                body = resp.json()
            except ValueError as exc:
                raise LangSmithError(
                    f"LangSmith returned a non-JSON sessions response: {exc}"
                ) from exc
            rows = body if isinstance(body, list) else []
            projects.extend(
                LangSmithProject(id=str(o["id"]), name=str(o.get("name") or o["id"]))
                for o in rows
                if isinstance(o, dict) and o.get("id")
            )
            if len(rows) < _PROJECT_PAGE_SIZE:
                return projects
            offset += _PROJECT_PAGE_SIZE

    def query_runs(
        self,
        project_ids: list[str],
        *,
        min_start_time: datetime,
        max_start_time: datetime | None = None,
        trace_filter: str = "",
        cursor: str | None = None,
        page_size: int = PAGE_SIZE,
    ) -> QueryPage:
        if not project_ids:
            raise LangSmithError("At least one LangSmith project id is required.")
        _ = max_start_time  # v1 `end_time` is not a start-time upper bound; window is in trace_filter
        body: dict[str, Any] = {
            "session": [validate_project_id(pid) for pid in project_ids],
            "select": list(_SELECT),
            "limit": min(page_size, _PROJECT_PAGE_SIZE),
            "start_time": _rfc3339(min_start_time),
        }
        if trace_filter:
            body["trace_filter"] = trace_filter
        if cursor:
            body["cursor"] = cursor
        resp = self._request("POST", "/api/v1/runs/query", json=body)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise LangSmithError(f"LangSmith returned a non-JSON runs response: {exc}") from exc
        return QueryPage(rows=_runs_from(payload), cursor=_cursor_from(payload))

    def iter_runs(
        self,
        project_ids: list[str],
        *,
        min_start_time: datetime,
        max_start_time: datetime | None = None,
        trace_filter: str = "",
        cursor: str | None = None,
        page_size: int = PAGE_SIZE,
        max_pages: int = _MAX_PAGES_PER_SLICE,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Page a runs query. Returns (rows, next_cursor); next_cursor is None when done."""
        collected: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        page_cursor = cursor
        for _ in range(max_pages):
            page = self.query_runs(
                project_ids,
                min_start_time=min_start_time,
                max_start_time=max_start_time,
                trace_filter=trace_filter,
                cursor=page_cursor,
                page_size=page_size,
            )
            collected.extend(page.rows)
            if not page.rows or not page.cursor or page.cursor in seen_cursors:
                return collected, None
            seen_cursors.add(page.cursor)
            page_cursor = page.cursor
        return collected, page_cursor


def _runs_from(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get("runs")
        if rows is None:
            rows = payload.get("items")
    elif isinstance(payload, list):
        rows = payload
    else:
        raise LangSmithError("LangSmith returned an unexpected runs response shape.")
    return [r for r in (rows or []) if isinstance(r, dict)]


def _cursor_from(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    cursors = payload.get("cursors")
    if isinstance(cursors, dict):
        token = cursors.get("next") or cursors.get("next_cursor")
        if token:
            return str(token)
    token = payload.get("next_cursor")
    return str(token) if token else None


def _error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(body, dict):
        return str(body.get("detail") or body.get("title") or body.get("message") or body)[:200]
    return str(body)[:200]


def _retry_after(resp: httpx.Response, fallback: float) -> float:
    raw = resp.headers.get("Retry-After")
    try:
        return min(max(float(raw), 0.0), _MAX_RETRY_AFTER)
    except (TypeError, ValueError):
        return fallback
