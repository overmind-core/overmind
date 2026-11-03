"""Langfuse REST reads with bounded pages and provider-directed retries."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any, Literal
from urllib.parse import quote

import httpx

from overbae.services.connectors.records import LangFuseObservation
from overbae.services.connectors.windows import TimeWindow, plan_windows

ApiVersion = Literal["v1", "v2", "unknown"]

_DEFAULT_HOST = "https://cloud.langfuse.com"
_V2_FIELDS = "core,basic,time,io,metadata,model,usage,prompt,metrics,trace_context"
_V2_PAGE_SIZE = 1000
_TRACE_FILTER_BATCH_SIZE = 150
_TRACE_FILTER_MAX_URL_CHARS = 12_000
_DEPRECATED_READ_PREFIXES = ("/api/public/traces", "/api/public/observations")
_DEPRECATED_MIN_INTERVAL = 24.0  # Hobby deprecated-read bucket is 150/hour
_429_ATTEMPTS = 4
_429_WAIT_CAP = 60.0

_deprecated_gate = threading.Lock()
_deprecated_next = 0.0


class LangFuseError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass(frozen=True)
class LangFuseProject:
    id: str
    name: str


class LangFuseClient:
    """Thin synchronous wrapper around the Langfuse public REST API."""

    def __init__(
        self,
        public_key: str,
        secret_key: str,
        base_url: str = "",
        *,
        retry_attempts: int = _429_ATTEMPTS,
    ):
        self.base_url = (base_url or _DEFAULT_HOST).rstrip("/")
        self._auth = (public_key, secret_key)
        self._api_version: ApiVersion = "unknown"
        self.retry_attempts = retry_attempts

    @property
    def api_version(self) -> ApiVersion:
        return self._api_version

    def _get(self, path: str, **params) -> Any:
        # Drop None params so we don't send empty query keys.
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base_url}{path}"
        last_429 = ""
        last_retry_after = _DEPRECATED_MIN_INTERVAL
        for attempt in range(self.retry_attempts):
            if _is_deprecated_read(path):
                _pace_deprecated_read()
            try:
                resp = httpx.get(url, auth=self._auth, params=clean, timeout=60)
            except httpx.RequestError as exc:
                raise LangFuseError(f"Network error reaching LangFuse: {exc}") from exc

            if resp.status_code == 429:
                last_429 = resp.text[:200]
                last_retry_after = _retry_after_seconds(resp)
                if attempt < self.retry_attempts - 1 and last_retry_after <= _429_WAIT_CAP:
                    time.sleep(last_retry_after)
                    continue
                break
            if resp.status_code == 401:
                raise LangFuseError(
                    "Invalid LangFuse credentials (401 Unauthorized).", status_code=401
                )
            if resp.status_code == 403:
                raise LangFuseError(
                    "LangFuse credentials do not have permission (403 Forbidden).",
                    status_code=403,
                )
            if not resp.is_success:
                raise LangFuseError(
                    f"LangFuse API error {resp.status_code}: {resp.text[:200]}",
                    status_code=resp.status_code,
                )
            return resp.json()
        raise LangFuseError(
            f"LangFuse API error 429: {last_429}",
            status_code=429,
            retry_after=last_retry_after,
        )

    def list_projects(self) -> list[LangFuseProject]:
        return [
            LangFuseProject(id=str(p["id"]), name=str(p.get("name") or p["id"]))
            for p in self._get("/api/public/projects").get("data") or []
        ]

    def probe_capabilities(self) -> ApiVersion:
        """Detect v2 observations support; cache on the client instance."""
        try:
            self._get(
                "/api/public/v2/observations",
                limit=1,
                fields="core",
                fromStartTime=_iso(datetime.now(UTC) - timedelta(days=1)),
                toStartTime=_iso(datetime.now(UTC)),
            )
            self._api_version = "v2"
        except LangFuseError as exc:
            msg = str(exc).lower()
            # 404 / "not found" / beta gate → fall back to v1 traces.
            if "404" in msg or "not found" in msg or "beta" in msg or "v2" in msg:
                self._api_version = "v1"
            else:
                # Auth / network errors should surface, not silently downgrade.
                raise
        return self._api_version

    def count(
        self,
        *,
        window: TimeWindow | None = None,
        lookback_days: int | None = None,
    ) -> int:
        """Estimate trace volume from semantic roots in one aggregate request.

        Roots are not guaranteed to be one-to-one with trace IDs. Never scan
        the full dataset merely to populate a preview or progress indicator.
        """
        end = window.end if window else datetime.now(UTC)
        start = (
            window.start
            if window
            else (
                end - timedelta(days=lookback_days)
                if lookback_days
                else datetime(1970, 1, 1, tzinfo=UTC)
            )
        )
        return self.volume(TimeWindow(start=start, end=end))["roots"]

    def volume(self, window: TimeWindow) -> dict[str, int]:
        """Count observations and semantic roots without consuming read pages."""
        query = {
            "view": "observations",
            "metrics": [{"measure": "count", "aggregation": "count"}],
            "dimensions": [{"field": "isRootObservation"}],
            "filters": [],
            "fromTimestamp": _iso(max(window.start, datetime(1970, 1, 1, tzinfo=UTC))),
            "toTimestamp": _iso(window.end),
        }
        data = self._get("/api/public/v2/metrics", query=json.dumps(query))
        rows = data.get("data")
        if not isinstance(rows, list):
            raise LangFuseError("Langfuse did not return a valid volume count.")
        roots = observations = 0
        for row in rows:
            value = row.get("count_count")
            if not isinstance(value, (int, float)) or value < 0:
                raise LangFuseError("Langfuse did not return a valid volume count.")
            observations += int(value)
            if row.get("isRootObservation") is True:
                roots += int(value)
        return {"roots": roots, "observations": observations}

    def iter_ingest_units(
        self,
        *,
        window_from: datetime | None = None,
        window_to: datetime | None = None,
        lookback_days: int | None = None,
        windows: list[TimeWindow] | None = None,
    ) -> Iterator[list[LangFuseObservation]]:
        """Yield observation trees (one list per trace), newest windows first.

        Pass *windows* to skip re-planning (used by the chunk task which already
        selected the next TimeWindow).
        """
        if self._api_version == "unknown":
            self.probe_capabilities()

        if windows is None:
            max_lookback = timedelta(days=lookback_days) if lookback_days else None
            windows = plan_windows(window_from, window_to, max_lookback=max_lookback)

        for window in windows:
            if self._api_version == "v2":
                yield from self._iter_v2_traces(window)
            else:
                yield from self._iter_v1_traces(window)

    def _iter_v2_observations(self, **filters) -> Iterator[LangFuseObservation]:
        """Page the v2 observations endpoint, following the meta cursor."""
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            observations, cursor = self._v2_observation_page(cursor=cursor, **filters)
            if cursor and cursor in seen:
                raise LangFuseError(
                    "Langfuse repeated a pagination cursor; the page was not imported."
                )
            yield from observations
            if not cursor:
                break
            seen.add(cursor)

    def _v2_observation_page(
        self,
        *,
        cursor: str | None = None,
        expand_metadata: str | None = None,
        limit: int = _V2_PAGE_SIZE,
        **filters,
    ) -> tuple[list[LangFuseObservation], str | None]:
        data = self._get(
            "/api/public/v2/observations",
            limit=limit,
            fields=_V2_FIELDS,
            expandMetadata=expand_metadata,
            cursor=cursor,
            **filters,
        )
        rows = data.get("data")
        if not isinstance(rows, list):
            raise LangFuseError("Langfuse did not return a valid observations page.")
        next_cursor = (data.get("meta") or {}).get("cursor")
        if next_cursor and (next_cursor == cursor or not rows):
            raise LangFuseError(
                "Langfuse returned a stalled pagination cursor; the page was not imported."
            )
        return [_observation_from_v2(raw) for raw in rows], next_cursor

    def fetch_trace_observations(
        self, trace_id: str, *, expand_metadata: str | None = None
    ) -> list[LangFuseObservation]:
        """Every observation of one trace, regardless of when each started."""
        return list(self._iter_v2_observations(traceId=trace_id, expand_metadata=expand_metadata))

    def fetch_v2_trace_page(
        self,
        window: TimeWindow,
        *,
        cursor: str | None = None,
        expand_metadata: str | None = None,
        limit: int = _V2_PAGE_SIZE,
    ) -> tuple[list[list[LangFuseObservation]], str | None]:
        """Complete touched traces because a cursor page can split one trace."""
        observations, next_cursor = self._v2_observation_page(
            cursor=cursor,
            limit=limit,
            expand_metadata=expand_metadata,
            fromStartTime=_iso(window.start) if window.start.year > 1 else None,
            toStartTime=_iso(window.end),
        )
        by_trace: dict[str, list[LangFuseObservation]] = {}
        for observation in observations:
            by_trace.setdefault(observation.trace_id or observation.id, []).append(observation)

        # Complete the selected trees without a request per trace. No time filter:
        # parents and siblings can fall outside the discovery page's window.
        trace_ids = [tid for tid, group in by_trace.items() if all(o.trace_id for o in group)]
        complete: dict[str, list[LangFuseObservation]] = {}
        for batch in _trace_batches(trace_ids):
            if len(batch) == 1:
                observations = self.fetch_trace_observations(
                    batch[0], expand_metadata=expand_metadata
                )
            else:
                observations = self._iter_v2_observations(
                    filter=json.dumps(
                        [
                            {
                                "type": "stringOptions",
                                "column": "traceId",
                                "operator": "any of",
                                "value": batch,
                            }
                        ]
                    ),
                    expand_metadata=expand_metadata,
                )
            for observation in observations:
                if observation.trace_id not in batch:
                    raise LangFuseError(
                        "Langfuse returned observations outside the requested trace batch."
                    )
                complete.setdefault(observation.trace_id, []).append(observation)
            if any(tid not in complete for tid in batch):
                raise LangFuseError(
                    "Langfuse did not return every requested trace; the page was not imported."
                )
        trees = [complete.get(tid, group) for tid, group in by_trace.items()]
        return trees, next_cursor

    def _iter_v2_traces(
        self,
        window: TimeWindow,
    ) -> Iterator[list[LangFuseObservation]]:
        """Page v2 observations, group by traceId, yield complete trees."""
        yield from self._group_observations(
            self._iter_v2_observations(
                fromStartTime=_iso(window.start) if window.start.year > 1 else None,
                toStartTime=_iso(window.end),
            )
        )

    def _group_observations(
        self, observations: Iterator[LangFuseObservation]
    ) -> Iterator[list[LangFuseObservation]]:
        by_trace: dict[str, list[LangFuseObservation]] = {}
        for obs in observations:
            by_trace.setdefault(obs.trace_id or obs.id, []).append(obs)

        def _trace_ts(obs_list: list[LangFuseObservation]) -> str:
            return max((o.start_time or "") for o in obs_list)

        for tid in sorted(by_trace, key=lambda t: _trace_ts(by_trace[t]), reverse=True):
            group = by_trace[tid]
            # A trace straddling the window arrives in pieces, and the missing
            # parents would make its children look like roots. Span ids are
            # stable and the writer never revisits them, so a half tree would
            # stay wrong forever — refetch the whole trace instead.
            if _is_partial_tree(group):
                group = self.fetch_trace_observations(tid) or group
            yield group


def _trace_batches(trace_ids: list[str]) -> Iterator[list[str]]:
    batch: list[str] = []
    size = 0
    for trace_id in trace_ids:
        length = len(quote(json.dumps(trace_id), safe="")) + 4
        if batch and (
            len(batch) == _TRACE_FILTER_BATCH_SIZE or size + length > _TRACE_FILTER_MAX_URL_CHARS
        ):
            yield batch
            batch, size = [], 0
        batch.append(trace_id)
        size += length
    if batch:
        yield batch


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def _is_deprecated_read(path: str) -> bool:
    return path.startswith(_DEPRECATED_READ_PREFIXES)


def _pace_deprecated_read() -> None:
    global _deprecated_next
    with _deprecated_gate:
        now = time.monotonic()
        wait = _deprecated_next - now
        if wait > 0:
            time.sleep(wait)
        _deprecated_next = time.monotonic() + _DEPRECATED_MIN_INTERVAL


def _retry_after_seconds(resp: httpx.Response) -> float:
    header = resp.headers.get("Retry-After")
    if header:
        try:
            seconds = float(header)
            if isfinite(seconds) and seconds >= 0:
                return seconds
        except ValueError:
            pass
    try:
        details = (json.loads(resp.text) or {}).get("details") or {}
        raw = details.get(
            "retryAfterSeconds", details.get("retryAfter", details.get("retry_after"))
        )
        if raw is not None:
            seconds = float(raw)
            if isfinite(seconds) and seconds >= 0:
                return seconds
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return _DEPRECATED_MIN_INTERVAL


def _is_partial_tree(observations: list[LangFuseObservation]) -> bool:
    """True when this group cannot be the whole trace: a parent is missing, or
    nothing in it is a root."""
    ids = {o.id for o in observations}
    if any(o.parent_observation_id and o.parent_observation_id not in ids for o in observations):
        return True
    return not any(o.is_root_observation or o.parent_observation_id is None for o in observations)


def _observation_from_v2(raw: dict[str, Any]) -> LangFuseObservation:
    return LangFuseObservation(
        id=raw["id"],
        trace_id=raw.get("traceId"),
        parent_observation_id=raw.get("parentObservationId"),
        type=(raw.get("type") or "SPAN").upper(),
        name=raw.get("name"),
        start_time=raw.get("startTime"),
        end_time=raw.get("endTime"),
        user_id=raw.get("userId"),
        session_id=raw.get("sessionId"),
        environment=raw.get("environment"),
        level=raw.get("level"),
        status_message=raw.get("statusMessage"),
        version=raw.get("version"),
        input=raw.get("input"),
        output=raw.get("output"),
        metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
        # `model` resolves to the real name; providedModelName is often null.
        model=raw.get("model") or raw.get("providedModelName"),
        usage_details=raw.get("usageDetails") if isinstance(raw.get("usageDetails"), dict) else {},
        cost_details=raw.get("costDetails") if isinstance(raw.get("costDetails"), dict) else {},
        total_cost=raw.get("totalCost"),
        latency=raw.get("latency"),
        tags=raw.get("tags") or raw.get("traceTags") or [],
        release=raw.get("release"),
        trace_name=raw.get("traceName"),
        is_root_observation=raw.get("isRootObservation"),
        raw=raw,
    )
