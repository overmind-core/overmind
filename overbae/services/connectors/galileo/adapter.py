"""Galileo implementation of ConnectorAdapter.

``traces/search`` returns trace metadata only; the span tree for each match is
a second call per trace id, so a page here costs one search request plus one
request per trace returned. The search page is kept small
(``client.DEFAULT_SEARCH_LIMIT``) so one celery chunk stays well inside the pacer.
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
from overbae.services.connectors.galileo.client import GalileoClient, GalileoError
from overbae.services.connectors.galileo.mapping import GALILEO, tree_to_records
from overbae.services.connectors.mapping import observations_to_span_dicts
from overbae.services.connectors.windows import plan_windows

GALILEO_CAPABILITIES = Capabilities(
    # Galileo documents the count as slightly inexact after deduplication.
    exact_count=False,
    needs_source_project=True,
    needs_secret=False,
)

_DEFAULT_LOOKBACK_DAYS = 30
# Async metrics can land after ingest completes, so every live poll re-checks
# a couple of days rather than trusting a single watermark.
_LIVE_LOOKBACK_DAYS = 2
_SAMPLE_TRACE_LIMIT = 100
_SAMPLE_MAX_PAGES = 4


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


class GalileoAdapter:
    source = GALILEO.source
    capabilities = GALILEO_CAPABILITIES
    conventions = GALILEO

    def __init__(self, credential):
        self.credential = credential
        self._client = GalileoClient(api_key=credential.api_key, base_url=credential.base_url)

    def verify(self) -> VerifyResult:
        try:
            streams = self.list_source_projects()
            self._probe_data_plane(streams)
        except GalileoError as exc:
            return VerifyResult(ok=False, detail=str(exc), capabilities=self.capabilities)
        return VerifyResult(ok=True, projects=streams, capabilities=self.capabilities)

    def _probe_data_plane(self, streams: list[SourceProject]) -> None:
        """One bounded search, because listing log streams proves nothing about
        whether this key can actually read one."""
        source_id = self._source_project_id() or (streams[0].id if streams else "")
        if not source_id:
            return
        self._client.search_traces(
            source_id,
            window_start=timezone.now() - timedelta(days=1),
            window_end=timezone.now(),
            limit=1,
        )

    def list_source_projects(self) -> list[SourceProject]:
        return [SourceProject(id=s.id, name=s.name) for s in self._client.list_log_streams()]

    def count(
        self,
        *,
        lookback_days: int | None,
        source_project_id: str = "",
        window_from: datetime | None = None,
        window_to: datetime | None = None,
    ) -> int | None:
        source_id = (source_project_id or self._source_project_id()).strip()
        if not source_id:
            return None
        window_end = window_to or timezone.now()
        window_start = window_from or (
            window_end - timedelta(days=int(lookback_days))
            if lookback_days
            else datetime.min.replace(tzinfo=UTC)
        )
        return self._client.count_traces(
            source_id,
            window_start=window_start,
            window_end=window_end,
        )

    def sample_units(
        self, *, lookback_days: int, limit: int = 200, source_project_id: str = ""
    ) -> list[Any]:
        window_end = timezone.now()
        window_start = window_end - timedelta(days=max(1, lookback_days))
        units, _, _ = self._collect(
            window_start=window_start,
            window_end=window_end,
            source_project_id=source_project_id,
            max_pages=_SAMPLE_MAX_PAGES,
        )
        capped = min(limit, _SAMPLE_TRACE_LIMIT)
        return [records for records, _, _ in units][:capped]

    def fetch_page(self, state: dict[str, Any]) -> Page:
        config = self.credential.active_config()
        lookback_days = (config.lookback_days if config else None) or _DEFAULT_LOOKBACK_DAYS
        mode = state.get("mode") or ("live" if state.get("watermark") else "backfill")

        if mode == "live":
            return self._live_page(state)

        # Windows are planned against an anchor pinned on the first page, not a
        # fresh now(): re-planning as the clock moves shifts every boundary, and
        # the resume filter then steps over a whole window.
        anchor = _parse_iso(state.get("backfill_anchor")) or timezone.now()
        windows = plan_windows(None, anchor, max_lookback=timedelta(days=int(lookback_days)))
        next_end = state.get("next_window_end")
        if next_end:
            windows = [w for w in windows if w.end.isoformat() <= next_end]
        if not windows:
            return Page(units=[], next_state=self._live_state(state), done=True, mode="backfill")

        window = windows[0]
        units, next_token, newest = self._collect(
            window_start=window.start,
            window_end=window.end,
            starting_token=int(state.get("next_token") or 0),
        )
        if next_token is not None:
            next_state = {
                "mode": "backfill",
                "watermark": newest or state.get("watermark"),
                "backfill_anchor": anchor.isoformat(),
                "next_window_end": window.end.isoformat(),
                "next_token": next_token,
                "windows_remaining": len(windows),
            }
            done = False
        else:
            remaining = windows[1:]
            if remaining:
                next_state = {
                    "mode": "backfill",
                    "watermark": newest or state.get("watermark"),
                    "backfill_anchor": anchor.isoformat(),
                    "next_window_end": remaining[0].end.isoformat(),
                    "windows_remaining": len(remaining),
                }
            else:
                next_state = self._live_state(state, newest)
            done = not remaining

        return Page(
            units=[_unit(records, trace_id, ts) for records, trace_id, ts in units],
            next_state=next_state,
            done=done,
            window_from=window.start,
            window_to=window.end,
            mode="backfill",
        )

    def _live_page(self, state: dict[str, Any]) -> Page:
        window_to = _parse_iso(state.get("live_window_to")) or timezone.now()
        window_from = _parse_iso(state.get("live_window_from")) or (
            window_to - timedelta(days=_LIVE_LOOKBACK_DAYS)
        )
        units, next_token, newest = self._collect(
            window_start=window_from,
            window_end=window_to,
            starting_token=int(state.get("live_next_token") or 0),
        )
        if next_token is None:
            next_state = self._live_state(state, newest)
        else:
            next_state = {
                "mode": "live",
                "watermark": newest or state.get("watermark"),
                "live_window_from": window_from.isoformat(),
                "live_window_to": window_to.isoformat(),
                "live_next_token": next_token,
            }
        return Page(
            units=[_unit(records, trace_id, ts) for records, trace_id, ts in units],
            next_state=next_state,
            done=next_token is None,
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
        """Empty during wizard verification: that runs before the credential is
        saved, on a stand-in carrying only the connection fields.
        """
        config = getattr(self.credential, "active_config", None)
        config = config() if callable(config) else None
        return (getattr(config, "source_project_id", "") or "") if config else ""

    def _collect(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        starting_token: int = 0,
        source_project_id: str = "",
        max_pages: int = 1,
    ) -> tuple[list[tuple[list[Any], str, str | None]], int | None, str | None]:
        """Up to *max_pages* search pages for one window, hydrated into records."""
        source_id = (source_project_id or self._source_project_id()).strip()
        if not source_id:
            raise GalileoError("No Galileo source project is configured.")

        units: list[tuple[list[Any], str, str | None]] = []
        newest: str | None = None
        token = starting_token
        for _ in range(max_pages):
            trees, next_token = self._client.iter_trace_trees(
                source_id,
                window_start=window_start,
                window_end=window_end,
                starting_token=token,
            )
            for tree in trees:
                records = tree_to_records(tree)
                if not records:
                    continue
                unit_newest = max((r.start_time or "" for r in records), default="") or None
                if unit_newest and (newest is None or unit_newest > newest):
                    newest = unit_newest
                units.append((records, str(tree.get("id") or ""), unit_newest))
            if next_token is None:
                return units, None, newest
            token = next_token
        return units, token, newest

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
            conventions=GALILEO,
            project=project,
            mapping=mapping,
        )


def _unit(records: list[Any], trace_id: str, newest: str | None) -> IngestUnit:
    return IngestUnit(records=records, external_trace_id=trace_id, newest_ts=newest)
