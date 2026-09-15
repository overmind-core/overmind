"""LangSmith implementation of ConnectorAdapter.

Live has no write-time watermark, so every poll rescans the last 6 days.
Six, not seven: the cheap rate tier is a span of 7 days or less, and
``max_start_time=now`` on a 7-day floor can tip over it. Completion lag and
the rate-tier boundary are not the same number.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from django.utils import timezone

from overbae.services.connectors.base import (
    Capabilities,
    IngestUnit,
    Page,
    SourceProject,
    VerifyResult,
)
from overbae.services.connectors.langsmith.client import (
    LangSmithClient,
    LangSmithError,
    trace_filter_for,
    validate_project_id,
)
from overbae.services.connectors.langsmith.mapping import (
    LANGSMITH,
    group_runs_by_trace,
    runs_to_records,
)
from overbae.services.connectors.mapping import observations_to_span_dicts
from overbae.services.connectors.windows import plan_windows

LANGSMITH_CAPABILITIES = Capabilities(
    exact_count=False,
    capability_sources=("observation_name", "metadata", "tag", "trace_name"),
    needs_source_project=True,
    retention_note=(
        "LangSmith keeps base-tier traces 14 days and extended-tier 400. Data past "
        "the project's window is gone, so a longer range imports nothing."
    ),
    needs_secret=False,
)

_DEFAULT_LOOKBACK_DAYS = 30
_LIVE_LOOKBACK_DAYS = 6
_SAMPLE_TRACE_LIMIT = 100


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _backfill_bounds(config: Any) -> tuple[datetime | None, datetime | None, timedelta | None]:
    if config and (hasattr(config, "backfill_from") or hasattr(config, "backfill_to")):
        return (
            _parse_iso(getattr(config, "backfill_from", None)),
            _parse_iso(getattr(config, "backfill_to", None)),
            None,
        )
    try:
        lookback_days = int(getattr(config, "lookback_days", None) or _DEFAULT_LOOKBACK_DAYS)
    except (TypeError, ValueError):
        lookback_days = _DEFAULT_LOOKBACK_DAYS
    return None, None, timedelta(days=lookback_days)


class LangSmithAdapter:
    source = LANGSMITH.source
    capabilities = LANGSMITH_CAPABILITIES
    conventions = LANGSMITH

    def __init__(self, credential):
        self.credential = credential
        self._client = LangSmithClient(
            api_key=credential.api_key,
            base_url=credential.base_url,
        )

    def verify(self) -> VerifyResult:
        try:
            projects = self.list_source_projects()
            self._probe_data_plane(projects)
        except LangSmithError as exc:
            return VerifyResult(ok=False, detail=str(exc), capabilities=self.capabilities)
        return VerifyResult(ok=True, projects=projects, capabilities=self.capabilities)

    def _probe_data_plane(self, projects: list[SourceProject]) -> None:
        project_id = self._source_project_id() or (projects[0].id if projects else "")
        if not project_id:
            return
        self._client.query_runs(
            [self._canonical_project_id(project_id)],
            min_start_time=timezone.now() - timedelta(days=1),
            page_size=1,
        )

    def list_source_projects(self) -> list[SourceProject]:
        return [SourceProject(id=p.id, name=p.name) for p in self._client.list_projects()]

    def count(
        self,
        *,
        lookback_days: int | None,
        source_project_id: str = "",  # noqa: ARG002
        window_from: Any = None,
        window_to: Any = None,
    ) -> int | None:
        _ = lookback_days, window_from, window_to
        return None

    def sample_units(
        self,
        *,
        lookback_days: int,
        limit: int = 200,
        source_project_id: str = "",
        window_from: Any = None,
        window_to: Any = None,
    ) -> list[Any]:
        window_end = _parse_iso(window_to) or timezone.now()
        window_start = _parse_iso(window_from)
        if window_start is None:
            days = min(max(1, lookback_days), _LIVE_LOOKBACK_DAYS)
            window_start = window_end - timedelta(days=days)
        units, _, _ = self._collect(
            window_start=window_start,
            window_end=window_end,
            child_slack=False,
            source_project_id=source_project_id,
        )
        return [records for records, _, _ in units][: min(limit, _SAMPLE_TRACE_LIMIT)]

    def fetch_page(self, state: dict[str, Any]) -> Page:
        config = self.credential.active_config()
        mode = state.get("mode") or ("live" if state.get("watermark") else "backfill")

        if mode == "live":
            return self._live_page(state)

        config_from, config_to, max_lookback = _backfill_bounds(config)
        anchor = _parse_iso(state.get("backfill_anchor")) or config_to or timezone.now()
        windows = plan_windows(config_from, anchor, max_lookback=max_lookback)
        next_end = state.get("next_window_end")
        if next_end:
            windows = [w for w in windows if w.end.isoformat() <= next_end]
        if not windows:
            return Page(
                units=[],
                next_state=self._live_state(state),
                done=True,
                mode="backfill",
            )

        window = windows[0]
        units, next_cursor, newest = self._collect(
            window_start=window.start,
            window_end=window.end,
            child_slack=True,
            cursor=state.get("next_cursor") or "",
        )
        if next_cursor:
            next_state = {
                "mode": "backfill",
                "watermark": newest or state.get("watermark"),
                "backfill_anchor": anchor.isoformat(),
                "next_window_end": window.end.isoformat(),
                "next_cursor": next_cursor,
                "windows_remaining": len(windows),
            }
            remaining = True
        else:
            rest = windows[1:]
            remaining = bool(rest)
            if rest:
                next_state = {
                    "mode": "backfill",
                    "watermark": newest or state.get("watermark"),
                    "backfill_anchor": anchor.isoformat(),
                    "next_window_end": rest[0].end.isoformat(),
                    "windows_remaining": len(rest),
                }
            else:
                next_state = self._live_state(state, newest)

        return Page(
            units=[_unit(records, trace_id, newest_ts) for records, trace_id, newest_ts in units],
            next_state=next_state,
            done=not remaining,
            window_from=window.start,
            window_to=window.end,
            mode="backfill",
        )

    def _live_page(self, state: dict[str, Any]) -> Page:
        window_to = _parse_iso(state.get("live_window_to")) or timezone.now()
        window_from = _parse_iso(state.get("live_window_from")) or (
            window_to - timedelta(days=_LIVE_LOOKBACK_DAYS)
        )
        units, next_cursor, newest = self._collect(
            window_start=window_from,
            window_end=window_to,
            child_slack=False,
            cursor=str(state.get("live_next_cursor") or ""),
        )
        if next_cursor:
            next_state = {
                "mode": "live",
                "watermark": newest or state.get("watermark"),
                "live_window_from": window_from.isoformat(),
                "live_window_to": window_to.isoformat(),
                "live_next_cursor": next_cursor,
            }
        else:
            next_state = self._live_state(state, newest)
        return Page(
            units=[_unit(records, trace_id, newest_ts) for records, trace_id, newest_ts in units],
            next_state=next_state,
            done=next_cursor is None,
            window_from=window_from,
            window_to=window_to,
            mode="live",
        )

    def _live_state(self, state: dict[str, Any], newest: str | None = None) -> dict[str, Any]:
        return {
            "mode": "live",
            "watermark": newest or state.get("watermark") or timezone.now().isoformat(),
        }

    def _source_project_id(self) -> str:
        config = getattr(self.credential, "active_config", None)
        config = config() if callable(config) else None
        return (getattr(config, "source_project_id", "") or "") if config else ""

    def _canonical_project_id(self, raw: str) -> str:
        """Chat presents projects by name; the query API only accepts a UUID."""
        try:
            return validate_project_id(raw)
        except LangSmithError:
            pass
        projects = self._client.list_projects()
        exact = [p for p in projects if p.name == raw]
        if len(exact) != 1:
            lowered = raw.lower()
            exact = [p for p in projects if p.name.lower() == lowered]
        if len(exact) == 1:
            return validate_project_id(exact[0].id)
        raise LangSmithError(
            f"Unknown LangSmith project {raw!r}. Use a tracing-project id or name."
        )

    def _project_ids(self, override: str = "") -> list[str]:
        source_project_id = (override or self._source_project_id()).strip()
        if not source_project_id:
            raise LangSmithError("No LangSmith source project is configured.")
        return [self._canonical_project_id(source_project_id)]

    def _collect(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        child_slack: bool,
        cursor: str = "",
        source_project_id: str = "",
    ) -> tuple[list[tuple[list[Any], str, str | None]], str | None, str | None]:
        max_start = window_end + timedelta(days=1) if child_slack else window_end
        rows, next_cursor = self._client.iter_runs(
            self._project_ids(source_project_id),
            min_start_time=window_start,
            max_start_time=max_start,
            trace_filter=trace_filter_for(window_start, window_end),
            cursor=cursor or None,
        )
        complete = next_cursor is None
        credential_id = str(getattr(self.credential, "id", "") or "")

        units: list[tuple[list[Any], str, str | None]] = []
        newest: str | None = None
        for trace_id, trace_runs in group_runs_by_trace(rows).items():
            records = runs_to_records(
                trace_runs, credential_id=credential_id, promote_orphans=complete
            )
            if not records:
                continue
            unit_newest = max((r.start_time or "" for r in records), default="") or None
            if unit_newest and (newest is None or unit_newest > newest):
                newest = unit_newest
            units.append((records, trace_id, unit_newest))
        return units, next_cursor, newest

    def to_span_dicts(
        self,
        unit: IngestUnit,
        *,
        credential,
        project=None,
        mapping: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return observations_to_span_dicts(
            unit.records,
            credential=credential,
            conventions=LANGSMITH,
            project=project,
            mapping=mapping,
        )


def _unit(records: list[Any], trace_id: str, newest: str | None) -> IngestUnit:
    return IngestUnit(records=records, external_trace_id=trace_id, newest_ts=newest)
