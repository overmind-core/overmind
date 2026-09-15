"""Langfuse implementation of ConnectorAdapter.

Preserves the existing ``sync_cursor`` key shape
(``mode``, ``watermark``, ``backfill_anchor``, ``next_window_end``,
``windows_remaining``) so in-flight backfills resume across deploy.
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
from overbae.services.connectors.langfuse.client import LangFuseClient, LangFuseError
from overbae.services.connectors.langfuse.mapping import LANGFUSE
from overbae.services.connectors.mapping import observations_to_span_dicts
from overbae.services.connectors.windows import TimeWindow, plan_windows

LANGFUSE_CAPABILITIES = Capabilities(
    exact_count=True,
    capability_sources=("observation_name", "trace_name", "tag", "metadata"),
    needs_source_project=True,
)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


class LangfuseAdapter:
    source = LANGFUSE.source
    capabilities = LANGFUSE_CAPABILITIES
    conventions = LANGFUSE

    def __init__(self, credential):
        self.credential = credential
        self._client = LangFuseClient(
            public_key=credential.api_key,
            secret_key=credential.api_secret,
            base_url=credential.base_url,
        )
        if credential.api_version == "v2":
            self._client._api_version = "v2"  # noqa: SLF001
        else:
            # v1 is the self-hosted fallback; Cloud credentials stored as v1
            # still 429 GET /traces (15/min) — re-probe so v2 wins.
            version = self._client.probe_capabilities()
            if getattr(credential, "pk", None) is not None:
                type(credential).objects.filter(pk=credential.pk).update(
                    api_version=version, verified_at=timezone.now()
                )
            credential.api_version = version

    def verify(self) -> VerifyResult:
        try:
            api_version = self._client.probe_capabilities()
            projects = self.list_source_projects()
        except LangFuseError as exc:
            return VerifyResult(ok=False, detail=str(exc), capabilities=self.capabilities)
        return VerifyResult(
            ok=True,
            api_version=api_version,
            projects=projects,
            capabilities=self.capabilities,
        )

    def list_source_projects(self) -> list[SourceProject]:
        return [SourceProject(id=p.id, name=p.name) for p in self._client.list_projects()]

    def count(
        self,
        *,
        lookback_days: int | None,
        source_project_id: str = "",  # noqa: ARG002
        window_from: datetime | None = None,
        window_to: datetime | None = None,
    ) -> int | None:
        end = window_to or timezone.now()
        start = window_from or (
            end - timedelta(days=int(lookback_days))
            if lookback_days
            else datetime.min.replace(tzinfo=UTC)
        )
        return self._client.count(window=TimeWindow(start=start, end=end))

    def sample_units(
        self,
        *,
        lookback_days: int,
        limit: int = 200,
        source_project_id: str = "",  # noqa: ARG002 — a Langfuse key already scopes one project
        window_from: datetime | None = None,
        window_to: datetime | None = None,
    ) -> list[Any]:
        end = window_to or timezone.now()
        window = TimeWindow(
            start=window_from or end - timedelta(days=lookback_days),
            end=end,
        )
        units: list[Any] = []
        for obs_list in self._client.iter_ingest_units(windows=[window]):
            units.append(obs_list)
            if len(units) >= limit:
                break
        return units

    def fetch_page(self, state: dict[str, Any]) -> Page:
        """Fetch one time window. Cursor keys match the pre-adapter shape."""
        config = self.credential.active_config()
        lookback_days = config.lookback_days if config else None
        watermark = state.get("watermark")
        mode = state.get("mode") or ("live" if watermark else "backfill")

        if mode == "live":
            return self._live_page(state)

        # Windows are planned against an anchor pinned on the first page, not a
        # fresh now(): re-planning as the clock moves shifts every boundary, and
        # the resume filter then steps over a whole window.
        configured_from = getattr(config, "backfill_from", None) if config else None
        configured_to = getattr(config, "backfill_to", None) if config else None
        anchor = _parse_iso(state.get("backfill_anchor")) or configured_to or timezone.now()
        all_windows = plan_windows(
            configured_from,
            anchor,
            max_lookback=timedelta(days=lookback_days) if lookback_days else None,
        )
        next_end = state.get("next_window_end")
        if next_end:
            all_windows = [w for w in all_windows if w.end.isoformat() <= next_end]
        if not all_windows:
            return Page(
                units=[],
                next_state={"mode": "live", "watermark": watermark},
                done=True,
                mode="backfill",
            )

        window = all_windows[0]
        units, newest = self._collect_window(window.start, window.end)
        self._persist_api_version()
        remaining = all_windows[1:]
        if not remaining:
            next_state = {"mode": "live", "watermark": newest or watermark}
            done = True
        else:
            next_state = {
                "mode": "backfill",
                "watermark": newest or watermark,
                "backfill_anchor": anchor.isoformat(),
                "next_window_end": remaining[0].end.isoformat(),
                "windows_remaining": len(remaining),
            }
            done = False

        return Page(
            units=units,
            next_state=next_state,
            done=done,
            window_from=window.start if getattr(window.start, "year", 9999) > 1 else None,
            window_to=window.end,
            mode="backfill",
        )

    def _live_page(self, state: dict[str, Any]) -> Page:
        cursor = state.get("next_cursor")
        if cursor:
            window_from = _parse_iso(state.get("live_window_from"))
            window_to = _parse_iso(state.get("live_window_to"))
        else:
            window_from = _parse_iso(state.get("watermark")) or (
                timezone.now() - timedelta(hours=1)
            )
            window_to = timezone.now()

        if window_from is None or window_to is None:
            window_from = timezone.now() - timedelta(hours=1)
            window_to = timezone.now()
            cursor = None

        if self._client.api_version == "v2":
            units, next_cursor = self._collect_v2_page(
                window_from,
                window_to,
                cursor=cursor,
            )
            if next_cursor:
                return Page(
                    units=units,
                    next_state={
                        "mode": "live",
                        "watermark": state.get("watermark"),
                        "next_cursor": next_cursor,
                        "live_window_from": window_from.isoformat(),
                        "live_window_to": window_to.isoformat(),
                    },
                    done=False,
                    window_from=window_from,
                    window_to=window_to,
                    mode="live",
                )
        else:
            units, _ = self._collect_window(window_from, window_to)

        self._persist_api_version()
        return Page(
            units=units,
            next_state={"mode": "live", "watermark": window_to.isoformat()},
            done=True,
            window_from=window_from,
            window_to=window_to,
            mode="live",
        )

    def _collect_window(
        self, window_from: datetime, window_to: datetime
    ) -> tuple[list[IngestUnit], str | None]:
        single = TimeWindow(start=window_from, end=window_to)
        return self._units_from_observation_groups(self._client.iter_ingest_units(windows=[single]))

    def _collect_v2_page(
        self,
        window_from: datetime,
        window_to: datetime,
        *,
        cursor: str | None,
    ) -> tuple[list[IngestUnit], str | None]:
        groups, next_cursor = self._client.fetch_v2_trace_page(
            TimeWindow(start=window_from, end=window_to),
            cursor=cursor,
            expand_metadata=self._expanded_metadata_key(),
        )
        units, _ = self._units_from_observation_groups(iter(groups))
        return units, next_cursor

    def _expanded_metadata_key(self) -> str | None:
        mapping = getattr(self.credential, "capability_mapping", None) or {}
        key = mapping.get("key") if mapping.get("source") == "metadata" else None
        return key if isinstance(key, str) and key else None

    def _units_from_observation_groups(
        self, observation_groups
    ) -> tuple[list[IngestUnit], str | None]:
        units: list[IngestUnit] = []
        newest: str | None = None
        for obs_list in observation_groups:
            unit_newest = None
            for obs in obs_list:
                ts = obs.start_time
                if ts and (unit_newest is None or ts > unit_newest):
                    unit_newest = ts
                if ts and (newest is None or ts > newest):
                    newest = ts
            tid = ""
            if obs_list:
                tid = obs_list[0].trace_id or obs_list[0].id
            units.append(IngestUnit(records=obs_list, external_trace_id=tid, newest_ts=unit_newest))
        return units, newest

    def _persist_api_version(self) -> None:
        version = getattr(self._client, "api_version", "unknown")
        if version in ("unknown", self.credential.api_version):
            return
        if getattr(self.credential, "pk", None) is None:
            return
        type(self.credential).objects.filter(pk=self.credential.pk).update(api_version=version)
        self.credential.api_version = version

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
            conventions=LANGFUSE,
            project=project,
            mapping=mapping,
        )
