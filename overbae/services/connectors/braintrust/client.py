"""Braintrust read client — ``POST /btql`` plus project listing.

Auth is a single bearer credential: an API key (``sk-``) or a service token
(``bt-st-``), which is the only way to scope access to specific projects.

The read path is BTQL, not ``/v1/*/fetch``: fetch paginates newest-first over
the whole version history, so an updated row reappears at older ``_xact_id``
values and the GET form has no cursor at all.

Verified against braintrust.dev/docs (Aug 2026):
- The endpoint is ``/btql`` with no ``/v1`` prefix.
- ``fmt: "json"`` returns ``{"data": [...], "schema": {...}, "warnings": [...]}``.
- The page cursor is returned only in the ``x-bt-cursor`` response header
  (``x-amz-meta-bt_cursor`` on some deployments) and is sent back by appending
  ``OFFSET '<token>'``; numeric offsets are unsupported.
- ~20 BTQL requests/minute per org, shared with the customer's own UI, SDK and
  MCP usage, so this client paces itself well below that.
- Project metadata lives in Braintrust's global control plane, but log records
  live in a region-pinned data plane. ``/v1/project`` therefore answers on any
  host while ``/btql`` answers 421 unless the base URL matches the org's region.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.braintrust.dev"
# Braintrust allows ~20/min per org and counts the customer's own UI and SDK
# traffic against it, so leave most of the budget to them.
DEFAULT_REQUESTS_PER_MINUTE = 12
# LIMIT counts traces on the traces shape. Braintrust's own guidance is <= 100
# whenever a query projects document-stored fields such as metadata or error.
PAGE_LIMIT = 100
# LIMIT counts traces on the traces shape, so a page's row count is set by the
# customer's trace fan-out rather than by us. These bound that.
_MAX_ROWS_PER_PAGE = 2000
_MIN_PAGE_LIMIT = 5
_QUERY_TIMEOUT_SECONDS = 30
# Braintrust's own timeout is 30s; allow for connect plus response transfer.
_HTTP_TIMEOUT_SECONDS = 90
_MAX_RETRIES = 5
_PROJECT_PAGE_SIZE = 100
# A fetch handles 200 BTQL pages; the adapter checkpoints the next cursor to resume.
_MAX_PAGES_PER_FETCH = 200

_SPAN_COLUMNS = (
    "id",
    "span_id",
    "span_parents",
    "root_span_id",
    "is_root",
    "created",
    "_xact_id",
    "_pagination_key",
    "span_attributes",
    "metrics",
    "scores",
    "metadata",
    "input",
    "output",
    "tags",
    "error",
    "estimated_cost() AS estimated_cost",
)
# project_logs() ids and names reach BTQL as SQL string literals, so anything
# that could terminate one is rejected rather than escaped.
_SAFE_SOURCE_ID = re.compile(r"^[A-Za-z0-9 ._:@/-]{1,128}$")
_REDIRECT_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


class BraintrustError(Exception):
    pass


class BraintrustAuthError(BraintrustError):
    """Credential is rejected or lacks scope — it must be rotated, not retried."""

    def __init__(self, message: str, *, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code)


class BraintrustRegionError(BraintrustError):
    """Base URL points at the wrong data plane — retrying cannot help."""

    def __init__(self, message: str, *, status_code: int = 421):
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code)


class BraintrustTimeoutError(BraintrustError):
    """The query did not finish. Retrying it unchanged just spends the budget again."""


@dataclass(frozen=True)
class BraintrustProject:
    id: str
    name: str


@dataclass
class QueryPage:
    rows: list[dict[str, Any]]
    cursor: str | None = None
    warnings: list[Any] = field(default_factory=list)


@dataclass
class SpanRowsPage:
    rows: list[dict[str, Any]]
    cursor: str | None
    done: bool


def _quote(value: str) -> str:
    if not _SAFE_SOURCE_ID.match(value or ""):
        raise BraintrustError(f"Unsafe Braintrust project identifier: {value!r}")
    return f"'{value}'"


def build_span_query(
    project_ids: list[str],
    *,
    where: str,
    limit: int = PAGE_LIMIT,
    order_by: str = "_pagination_key ASC",
    cursor: str | None = None,
) -> str:
    """A traces-shape span query.

    ``shape => 'traces'`` returns every span of any trace containing a matching
    span, so a trace is never half-ingested and no refetch-by-trace-id pass is
    needed. *where* must carry a range filter on ``created``, ``_xact_id`` or
    ``_pagination_key``; without one Braintrust scans all project history.
    """
    if not project_ids:
        raise BraintrustError("At least one Braintrust project id is required.")
    sources = ", ".join(_quote(pid) for pid in project_ids)
    sql = (
        f"SELECT {', '.join(_SPAN_COLUMNS)}\n"
        f"FROM project_logs({sources}, shape => 'traces')\n"
        f"WHERE {where}\n"
        f"ORDER BY {order_by}\n"
        f"LIMIT {int(limit)}"
    )
    if cursor:
        sql += f"\nOFFSET {_quote_cursor(cursor)}"
    return sql


def _quote_cursor(cursor: str) -> str:
    # Cursors are opaque provider tokens; a quote in one would break the query.
    if "'" in cursor or "\\" in cursor:
        raise BraintrustError("Braintrust returned an unquotable cursor token.")
    return f"'{cursor}'"


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


class BraintrustClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "",
        *,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
    ):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._pacer = _Pacer(requests_per_minute)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.base_url}{path}"
        delay = 1.0
        for attempt in range(_MAX_RETRIES):
            self._pacer.wait()
            try:
                resp = httpx.request(
                    method, url, headers=self._headers, timeout=_HTTP_TIMEOUT_SECONDS, **kwargs
                )
            except httpx.TimeoutException as exc:
                raise BraintrustTimeoutError(
                    f"Braintrust did not answer within {_HTTP_TIMEOUT_SECONDS}s: {exc}"
                ) from exc
            except httpx.RequestError as exc:
                raise BraintrustError(f"Network error reaching Braintrust: {exc}") from exc

            # Their own query timeout is 30s; the same query would only hit it again.
            if resp.status_code == 504:
                raise BraintrustTimeoutError(
                    f"Braintrust timed out serving the query (504): {_error_detail(resp)}"
                )
            if resp.status_code in (401, 403):
                raise BraintrustAuthError(
                    "Braintrust rejected the credential — rotate the API key or service token "
                    f"({resp.status_code}): {_error_detail(resp)}",
                    status_code=resp.status_code,
                )
            if resp.status_code == 421:
                raise BraintrustRegionError(
                    _region_detail(resp, self.base_url), status_code=resp.status_code
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == _MAX_RETRIES - 1:
                    break
                time.sleep(_retry_after(resp, delay))
                delay = min(delay * 2, 60.0)
                continue
            if not resp.is_success:
                raise BraintrustError(
                    f"Braintrust API error {resp.status_code}: {_error_detail(resp)}"
                )
            return resp

        raise BraintrustError(
            f"Braintrust API error {resp.status_code} after {_MAX_RETRIES} attempts: "
            f"{_error_detail(resp)}"
        )

    def list_projects(self) -> list[BraintrustProject]:
        """Every visible project. There is no "has more" flag — stop on a short page."""
        projects: list[BraintrustProject] = []
        starting_after: str | None = None
        while True:
            params: dict[str, Any] = {"limit": _PROJECT_PAGE_SIZE}
            if starting_after:
                params["starting_after"] = starting_after
            objects = (self._request("GET", "/v1/project", params=params).json() or {}).get(
                "objects"
            ) or []
            projects.extend(
                BraintrustProject(id=str(o["id"]), name=str(o.get("name") or o["id"]))
                for o in objects
                if o.get("id")
            )
            if len(objects) < _PROJECT_PAGE_SIZE or not projects:
                return projects
            starting_after = projects[-1].id

    def query(self, sql: str) -> QueryPage:
        """Run one BTQL query.

        ``lint_mode`` is left at its default: ``strict`` turns advisory
        performance warnings into hard 400s.
        """
        resp = self._request("POST", "/btql", json={"query": sql, "fmt": "json"})
        try:
            body = resp.json()
        except ValueError as exc:
            raise BraintrustError(f"Braintrust returned a non-JSON BTQL response: {exc}") from exc
        if not isinstance(body, dict):
            raise BraintrustError("Braintrust returned an unexpected BTQL response shape.")

        # The API reference documents "data"; a KB article documents "rows".
        rows = body.get("data")
        if rows is None:
            rows = body.get("rows")
        warnings = body.get("warnings") or []
        if warnings:
            logger.info("braintrust btql warnings: %s", warnings)
        cursor = (
            resp.headers.get("x-bt-cursor")
            or resp.headers.get("x-amz-meta-bt_cursor")
            or body.get("cursor")
        )
        return QueryPage(
            rows=[r for r in (rows or []) if isinstance(r, dict)],
            cursor=cursor or None,
            warnings=list(warnings),
        )

    def fetch_span_rows(
        self,
        project_ids: list[str],
        *,
        where: str,
        limit: int = PAGE_LIMIT,
        order_by: str = "_pagination_key ASC",
        cursor: str | None = None,
        max_pages: int = _MAX_PAGES_PER_FETCH,
    ) -> SpanRowsPage:
        """Fetch a bounded set of BTQL pages, returning a cursor when more remain.

        *limit* counts traces, so a project whose traces are hundreds of spans
        wide returns far more rows — and document bytes — per page than it
        suggests. The trace budget therefore shrinks when a page overshoots the
        row cap or times out, and stays shrunk for the rest of the walk. Retrying
        a failed page is safe because its cursor was never consumed.
        """
        rows: list[dict[str, Any]] = []
        seen_cursors = {cursor} if cursor else set()
        page_limit = limit
        for _ in range(max_pages):
            page = None
            while page is None:
                try:
                    page = self.query(
                        build_span_query(
                            project_ids,
                            where=where,
                            limit=page_limit,
                            order_by=order_by,
                            cursor=cursor,
                        )
                    )
                except BraintrustTimeoutError:
                    if page_limit <= _MIN_PAGE_LIMIT:
                        raise
                    page_limit = max(_MIN_PAGE_LIMIT, page_limit // 2)
                    logger.info("braintrust page timed out; retrying at limit=%d", page_limit)
            rows.extend(page.rows)
            page_limit = _shrink_to_fit(page_limit, len(page.rows))
            cursor = page.cursor
            if not page.rows or not cursor:
                return SpanRowsPage(rows=rows, cursor=None, done=True)
            if cursor in seen_cursors:
                raise BraintrustError("Braintrust returned a repeated BTQL cursor.")
            seen_cursors.add(cursor)
        logger.warning(
            "braintrust btql fetch reached %d pages; continuing from its cursor", max_pages
        )
        return SpanRowsPage(rows=rows, cursor=cursor, done=False)


def _shrink_to_fit(page_limit: int, rows: int) -> int:
    """Re-budget the next page from the fan-out this one just revealed."""
    if rows <= _MAX_ROWS_PER_PAGE or page_limit <= _MIN_PAGE_LIMIT:
        return page_limit
    return max(_MIN_PAGE_LIMIT, page_limit * _MAX_ROWS_PER_PAGE // rows)


def _region_detail(resp: httpx.Response, base_url: str) -> str:
    """Braintrust names the correct host in the body, so quote it back as the fix."""
    detail = _error_detail(resp)
    match = _REDIRECT_URL_RE.search(detail)
    target = match.group(0).rstrip("/.,\"'") if match else ""
    if target and target.rstrip("/") != base_url:
        return (
            f"{base_url} is not this organization's Braintrust data plane (421). "
            f"Set the connector base URL to {target}."
        )
    return f"{base_url} is not this organization's Braintrust data plane (421): {detail}"


def _error_detail(resp: httpx.Response) -> str:
    """Braintrust has no error schema and may answer text/plain, so never assume JSON."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(body, dict):
        return str(body.get("Message") or body.get("message") or body)[:200]
    return str(body)[:200]


def _retry_after(resp: httpx.Response, fallback: float) -> float:
    raw = resp.headers.get("Retry-After")
    try:
        return min(max(float(raw), 0.0), _QUERY_TIMEOUT_SECONDS * 2)
    except (TypeError, ValueError):
        return fallback
