"""Langfuse REST client — v2 observations with v1 traces fallback.

Auth: Basic (public_key, secret_key). Default host: https://cloud.langfuse.com.
v2 observations is Cloud-only; self-hosted v3 returns 404 — probe caches
``api_version`` on the credential.

Cloud rate-limits deprecated reads (GET /traces, GET /observations) at 15/min
on Hobby and tells high-volume callers to use v2. Ingest stays on v2; v1 is
only for self-hosted 404s.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx

from overbae.services.connectors.records import LangFuseObservation
from overbae.services.connectors.windows import TimeWindow, plan_windows

ApiVersion = Literal["v1", "v2", "unknown"]

_DEFAULT_HOST = "https://cloud.langfuse.com"
_V2_FIELDS = "core,basic,time,io,metadata,model,usage,prompt,metrics,trace_context"
_V2_PAGE_SIZE = 100  # v2 allows up to 1000; stay conservative
_DEPRECATED_READ_PREFIXES = ("/api/public/traces", "/api/public/observations")
_DEPRECATED_MIN_INTERVAL = 4.5  # Hobby deprecated-read bucket is 15/min
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


class LangFuseClient:
    """Thin synchronous wrapper around the Langfuse public REST API."""

    def __init__(self, public_key: str, secret_key: str, base_url: str = ""):
        self.base_url = (base_url or _DEFAULT_HOST).rstrip("/")
        self._auth = (public_key, secret_key)
        self._api_version: ApiVersion = "unknown"

    @property
    def api_version(self) -> ApiVersion:
        return self._api_version

    def _get(self, path: str, **params) -> Any:
        # Drop None params so we don't send empty query keys.
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base_url}{path}"
        last_429 = ""
        last_retry_after = _DEPRECATED_MIN_INTERVAL
        for attempt in range(_429_ATTEMPTS):
            if _is_deprecated_read(path):
                _pace_deprecated_read()
            try:
                resp = httpx.get(url, auth=self._auth, params=clean, timeout=60)
            except httpx.RequestError as exc:
                raise LangFuseError(f"Network error reaching LangFuse: {exc}") from exc

            if resp.status_code == 429:
                last_429 = resp.text[:200]
                last_retry_after = _retry_after_seconds(resp)
                if attempt < _429_ATTEMPTS - 1:
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
        """Best-effort count of traces in *window* / lookback.

        Walks pages until exhausted. Used by the wizard preview — not a hot path.
        """
        n = 0
        for _ in self.iter_ingest_units(
            window_from=window.start if window else None,
            window_to=window.end if window else None,
            lookback_days=lookback_days,
            windows=[window] if window is not None else None,
        ):
            n += 1
        return n

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
            yield from observations
            # A repeated cursor would page forever inside a worker.
            if not cursor or not observations or cursor in seen:
                break
            seen.add(cursor)

    def _v2_observation_page(
        self,
        *,
        cursor: str | None = None,
        expand_metadata: str | None = None,
        **filters,
    ) -> tuple[list[LangFuseObservation], str | None]:
        data = self._get(
            "/api/public/v2/observations",
            limit=_V2_PAGE_SIZE,
            fields=_V2_FIELDS,
            expandMetadata=expand_metadata,
            cursor=cursor,
            **filters,
        )
        observations = [_observation_from_v2(raw) for raw in data.get("data") or []]
        next_cursor = (data.get("meta") or {}).get("cursor")
        return observations, next_cursor if next_cursor != cursor else None

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
    ) -> tuple[list[list[LangFuseObservation]], str | None]:
        """Complete touched traces because a cursor page can split one trace."""
        observations, next_cursor = self._v2_observation_page(
            cursor=cursor,
            expand_metadata=expand_metadata,
            fromStartTime=_iso(window.start) if window.start.year > 1 else None,
            toStartTime=_iso(window.end),
        )
        by_trace: dict[str, list[LangFuseObservation]] = {}
        for observation in observations:
            by_trace.setdefault(observation.trace_id or observation.id, []).append(observation)

        trees: list[list[LangFuseObservation]] = []
        for trace_id, page_group in by_trace.items():
            if not all(observation.trace_id for observation in page_group):
                trees.append(page_group)
                continue
            whole_trace = self.fetch_trace_observations(trace_id, expand_metadata=expand_metadata)
            if whole_trace:
                trees.append(whole_trace)
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
            return min(float(header), _429_WAIT_CAP)
        except ValueError:
            pass
    try:
        details = (json.loads(resp.text) or {}).get("details") or {}
        raw = details.get("retryAfter") or details.get("retry_after")
        if raw is not None:
            return min(float(raw), _429_WAIT_CAP)
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
