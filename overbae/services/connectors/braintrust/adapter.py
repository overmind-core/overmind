"""Braintrust implementation of ConnectorAdapter.

Braintrust has no ``updated_at``, so the cursor carries two watermarks:
``created`` (event time, never rewritten) plans the backfill windows, and
``_xact_id`` (write time, monotonic) drives incremental catch-up. ``_xact_id`` is
not unique per row — rows committed together share one — so it is compared with
``>=`` and correctness rests on the idempotent upsert.
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
from overbae.services.connectors.braintrust.client import (
    BraintrustClient,
    BraintrustError,
    SpanRowsPage,
    build_span_query,
)
from overbae.services.connectors.braintrust.mapping import (
    BRAINTRUST,
    group_rows_by_trace,
    rows_to_records,
)
from overbae.services.connectors.mapping import observations_to_span_dicts
from overbae.services.connectors.windows import plan_windows

# Braintrust has no CAPABILITY span type, so the wizard picks observation names.
BRAINTRUST_CAPABILITIES = Capabilities(
    # Counting would burn a request from a budget shared with the customer's UI.
    exact_count=False,
    capability_sources=("observation_name", "metadata", "tag", "trace_name"),
    needs_source_project=True,
    retention_note=(
        "Braintrust logs are retained 14 days on Starter and 30 on Pro. Data past that "
        "window is filtered out silently, so a longer range imports nothing."
    ),
    # One bearer API key; there is no second secret to collect.
    needs_secret=False,
)

# Backfill is always bounded: past retention Braintrust returns an empty page
# rather than an error, so an empty window can never mean "finished".
_DEFAULT_LOOKBACK_DAYS = 30
# A row created days ago can be rewritten today by async scoring or human
# review, so the live poll's created floor must exceed that lag.
_LIVE_LOOKBACK_DAYS = 7
_PRIMARY_ORDER = "_pagination_key ASC"
# _pagination_key is nullable and only exists in Brainstore.
_FALLBACK_ORDER = "_xact_id ASC"
_SAMPLE_TRACE_LIMIT = 100


def _sql_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class BraintrustAdapter:
    source = BRAINTRUST.source
    capabilities = BRAINTRUST_CAPABILITIES
    conventions = BRAINTRUST

    def __init__(self, credential):
        self.credential = credential
        self._client = BraintrustClient(
            api_key=credential.api_key,
            base_url=credential.base_url,
        )

    def verify(self) -> VerifyResult:
        try:
            projects = self.list_source_projects()
            self._probe_data_plane(projects)
        except BraintrustError as exc:
            return VerifyResult(ok=False, detail=str(exc), capabilities=self.capabilities)
        return VerifyResult(ok=True, projects=projects, capabilities=self.capabilities)

    def _probe_data_plane(self, projects: list[SourceProject]) -> None:
        """One bounded BTQL read, because listing projects proves nothing about it.

        Project names live in Braintrust's global control plane, so ``/v1/project``
        succeeds against any host; log records live in a region-pinned data plane.
        Without this probe a wrong base URL verifies green and then imports nothing.
        Ordered by ``_xact_id`` so a deployment without ``_pagination_key`` still passes.
        """
        project_id = self._source_project_id() or (projects[0].id if projects else "")
        if not project_id:
            return
        self._client.query(
            build_span_query(
                [project_id],
                where=f"created >= '{_sql_timestamp(timezone.now() - timedelta(days=1))}'",
                limit=1,
                order_by=_FALLBACK_ORDER,
            )
        )

    def list_source_projects(self) -> list[SourceProject]:
        return [SourceProject(id=p.id, name=p.name) for p in self._client.list_projects()]

    def count(
        self,
        *,
        lookback_days: int | None,
        source_project_id: str = "",  # noqa: ARG002
        window_from: Any = None,  # noqa: ARG002
        window_to: Any = None,  # noqa: ARG002
    ) -> int | None:
        """Always None — an exact count costs a request from a shared per-org budget."""
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
        end = _parse_iso(window_to) or timezone.now()
        start = _parse_iso(window_from) or end - timedelta(days=max(1, lookback_days))
        if start >= end:
            return []
        where = f"created >= '{_sql_timestamp(start)}' AND created < '{_sql_timestamp(end)}'"
        units = self._collect(
            where, limit=_SAMPLE_TRACE_LIMIT, source_project_id=source_project_id
        )[0]
        return [records for records, _, _ in units][:limit]

    def fetch_page(self, state: dict[str, Any]) -> Page:
        config = self.credential.active_config()
        lookback_days = (config.lookback_days if config else None) or _DEFAULT_LOOKBACK_DAYS
        mode = state.get("mode") or ("live" if state.get("watermark") else "backfill")

        if mode == "live":
            return self._live_page(state)

        # Windows are planned against an anchor pinned on the first page, not a
        # fresh now(): re-planning as the clock moves shifts every boundary, and
        # the resume filter then steps over a whole window.
        configured_from = _parse_iso(getattr(config, "backfill_from", None))
        configured_to = _parse_iso(getattr(config, "backfill_to", None))
        anchor = _parse_iso(state.get("backfill_anchor")) or configured_to or timezone.now()
        windows = plan_windows(
            configured_from,
            anchor,
            max_lookback=timedelta(days=int(lookback_days)),
        )
        next_end = state.get("next_window_end")
        if next_end:
            windows = [w for w in windows if w.end.isoformat() <= next_end]
        if not windows:
            return Page(
                units=[],
                next_state=self._live_state(state, None, None),
                done=True,
                mode="backfill",
            )

        window = windows[0]
        where = (
            f"created >= '{_sql_timestamp(window.start)}' "
            f"AND created < '{_sql_timestamp(window.end)}'"
        )
        units, newest_created, max_xact_id, cursor, complete = self._collect(
            where,
            cursor=state.get("btql_cursor"),
        )
        if not complete:
            return Page(
                units=[_unit(records, trace_id, newest) for records, trace_id, newest in units],
                next_state={
                    "mode": "backfill",
                    "watermark": newest_created or state.get("watermark"),
                    "xact_id": _max_version(state.get("xact_id"), max_xact_id),
                    "backfill_anchor": anchor.isoformat(),
                    "next_window_end": window.end.isoformat(),
                    "btql_cursor": cursor,
                    "windows_remaining": len(windows),
                },
                done=False,
                window_from=window.start,
                window_to=window.end,
                mode="backfill",
            )
        remaining = windows[1:]
        if remaining:
            next_state = {
                "mode": "backfill",
                "watermark": newest_created or state.get("watermark"),
                "xact_id": _max_version(state.get("xact_id"), max_xact_id),
                "backfill_anchor": anchor.isoformat(),
                "next_window_end": remaining[0].end.isoformat(),
                "windows_remaining": len(remaining),
            }
        else:
            next_state = self._live_state(state, newest_created, max_xact_id)

        return Page(
            units=[_unit(records, trace_id, newest) for records, trace_id, newest in units],
            next_state=next_state,
            done=not remaining,
            window_from=window.start,
            window_to=window.end,
            mode="backfill",
        )

    def _live_page(self, state: dict[str, Any]) -> Page:
        cursor = state.get("btql_cursor")
        window_to = _parse_iso(state.get("live_window_to")) or timezone.now()
        window_from = _parse_iso(state.get("live_window_from")) or (
            window_to - timedelta(days=_LIVE_LOOKBACK_DAYS)
        )
        watermark = _as_version(state.get("live_xact_floor", state.get("xact_id")))
        if watermark is None:
            where = (
                f"created >= '{_sql_timestamp(window_from)}' "
                f"AND created < '{_sql_timestamp(window_to)}'"
            )
        else:
            # _xact_id is Braintrust's monotonic write version, so this catches
            # async scoring and review updates even when their created time is old.
            where = f"_xact_id >= '{watermark}'"
        units, newest_created, max_xact_id, next_cursor, complete = self._collect(
            where, cursor=cursor
        )
        if not complete:
            return Page(
                units=[_unit(records, trace_id, newest) for records, trace_id, newest in units],
                next_state={
                    "mode": "live",
                    "watermark": state.get("watermark"),
                    "xact_id": state.get("xact_id"),
                    "live_xact_floor": watermark,
                    "live_window_from": window_from.isoformat(),
                    "live_window_to": window_to.isoformat(),
                    "btql_cursor": next_cursor,
                },
                done=False,
                window_from=window_from,
                window_to=window_to,
                mode="live",
            )
        return Page(
            units=[_unit(records, trace_id, newest) for records, trace_id, newest in units],
            next_state=self._live_state(state, newest_created, max_xact_id),
            done=True,
            window_from=window_from,
            window_to=window_to,
            mode="live",
        )

    def _live_state(
        self, state: dict[str, Any], newest_created: str | None, max_xact_id: int | None
    ) -> dict[str, Any]:
        return {
            "mode": "live",
            "watermark": newest_created or state.get("watermark") or timezone.now().isoformat(),
            "xact_id": _max_version(state.get("xact_id"), max_xact_id),
        }

    def _source_project_id(self) -> str:
        """Empty during wizard verification: that runs before the credential is
        saved, on a stand-in carrying only the connection fields.
        """
        config = getattr(self.credential, "active_config", None)
        config = config() if callable(config) else None
        return (getattr(config, "source_project_id", "") or "") if config else ""

    def _project_ids(self, override: str = "") -> list[str]:
        source_project_id = override or self._source_project_id()
        if not source_project_id:
            raise BraintrustError("No Braintrust source project is configured.")
        return [source_project_id]

    def _collect(
        self,
        where: str,
        *,
        limit: int | None = None,
        source_project_id: str = "",
        cursor: str | None = None,
    ) -> tuple[
        list[tuple[list[Any], str, str | None]],
        str | None,
        int | None,
        str | None,
        bool,
    ]:
        """Rows for *where*, grouped into one record list per trace."""
        result = self._fetch_rows(
            where, limit=limit, source_project_id=source_project_id, cursor=cursor
        )
        rows = result.rows
        credential_id = str(getattr(self.credential, "id", "") or "")

        units: list[tuple[list[Any], str, str | None]] = []
        newest_created: str | None = None
        max_xact_id: int | None = None
        for trace_id, trace_rows in group_rows_by_trace(rows).items():
            records = rows_to_records(trace_rows, credential_id=credential_id)
            if not records:
                continue
            unit_newest = max((r.start_time or "" for r in records), default="") or None
            if unit_newest and (newest_created is None or unit_newest > newest_created):
                newest_created = unit_newest
            for row in trace_rows:
                max_xact_id = _max_version(max_xact_id, _as_version(row.get("_xact_id")))
            units.append((records, trace_id, unit_newest))
        return units, newest_created, max_xact_id, result.cursor, result.done

    def _fetch_rows(
        self,
        where: str,
        *,
        limit: int | None = None,
        source_project_id: str = "",
        cursor: str | None = None,
    ) -> SpanRowsPage:
        kwargs: dict[str, Any] = {"where": where}
        if limit is not None:
            kwargs["limit"] = limit
        if cursor is not None:
            kwargs["cursor"] = cursor
        project_ids = self._project_ids(source_project_id)
        try:
            return self._client.fetch_span_rows(project_ids, order_by=_PRIMARY_ORDER, **kwargs)
        except BraintrustError as exc:
            if "_pagination_key" not in str(exc):
                raise
            return self._client.fetch_span_rows(project_ids, order_by=_FALLBACK_ORDER, **kwargs)

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
            conventions=BRAINTRUST,
            project=project,
            mapping=mapping,
        )


def _unit(records: list[Any], trace_id: str, newest: str | None) -> IngestUnit:
    return IngestUnit(records=records, external_trace_id=trace_id, newest_ts=newest)


def _as_version(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _max_version(current: Any, incoming: int | None) -> int | None:
    left = _as_version(current)
    if left is None:
        return incoming
    if incoming is None:
        return left
    return max(left, incoming)
