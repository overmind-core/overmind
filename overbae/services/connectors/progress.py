"""Durable source-count estimates shared by Console and MCP polling.

Metrics roots approximate trace IDs; they are never an exact completion gate.
Polling reads stored counts only. Time includes provider waits and retries.
"""

from datetime import datetime, timedelta
from math import ceil
from typing import Literal

from django.utils import timezone
from pydantic import BaseModel, Field

from overbae.services.connectors.langfuse.client import LangFuseError
from overbae.services.connectors.sync import connector_imported_spans
from overbae.services.connectors.windows import TimeWindow


class SyncProgress(BaseModel):
    state: Literal["estimating", "importing", "waiting", "complete"]
    total_estimate: int | None = Field(default=None, ge=0)
    observations_total: int | None = Field(default=None, ge=0)
    imported: int = Field(ge=0)
    count_basis: Literal["semantic_roots"] = "semantic_roots"
    traces_per_second: float | None = Field(default=None, ge=0)
    estimated_seconds_remaining: int | None = Field(default=None, ge=0)
    counted_at: datetime | None = None
    last_progress_at: datetime | None = None
    retry_at: datetime | None = None


def _scoped_imported(credential, snapshot):
    spans = connector_imported_spans(credential)
    if snapshot.get("window_from"):
        start = datetime.fromisoformat(snapshot["window_from"])
        spans = spans.filter(start_time_ns__gte=int(start.timestamp() * 1_000_000_000))
    end = datetime.fromisoformat(snapshot["window_to"])
    return (
        spans.filter(start_time_ns__lt=int(end.timestamp() * 1_000_000_000))
        .values("attributes__langfuse.trace_id")
        .distinct()
        .count()
    )


def prepare_backfill_progress(credential, client, state, windows, anchor):
    """Count once per backfill, before the first data page; failure is optional."""
    if not windows or not getattr(credential, "pk", None):
        return
    now = timezone.now().isoformat()
    start = windows[-1].start
    snapshot = state.get("progress")
    if snapshot:
        if snapshot.get("total") is not None:
            return
        if snapshot.get("count_retry_at") and timezone.now() < datetime.fromisoformat(
            snapshot["count_retry_at"]
        ):
            return
        attempted = datetime.fromisoformat(
            snapshot.get("count_attempted_at", snapshot["started_at"])
        )
        if (timezone.now() - attempted).total_seconds() < 300:
            return
    else:
        snapshot = {
            "started_at": now,
            "window_from": start.isoformat() if start.year > 1 else None,
            "window_to": anchor.isoformat(),
            "last_progress_at": None,
            "done": False,
        }
        baseline = _scoped_imported(credential, snapshot)
        snapshot.update(baseline=baseline, imported=baseline)
    snapshot["count_attempted_at"] = now
    attempts = client.retry_attempts
    try:
        client.retry_attempts = 1
        volume = client.volume(TimeWindow(start=start, end=anchor))
        snapshot.update(total=volume["roots"], observations=volume["observations"], counted_at=now)
    except LangFuseError as exc:
        # No permission, unsupported metrics, or quota exhaustion must not stop data import.
        snapshot.update(total=None, observations=None, counted_at=None)
        snapshot["count_retry_at"] = (
            timezone.now() + timedelta(seconds=max(300, exc.retry_after or 0))
        ).isoformat()
    finally:
        client.retry_attempts = attempts
    state["progress"] = snapshot
    state["backfill_anchor"] = anchor.isoformat()
    type(credential).objects.filter(pk=credential.pk).update(
        sync_cursor=state, backfill_total=snapshot["total"]
    )
    credential.backfill_total = snapshot["total"]


def checkpoint_progress(credential, snapshot, *, gained, done):
    """Add source traces new to this chunk; completion recounts the persisted spans once."""
    imported = _scoped_imported(credential, snapshot) if done else snapshot["imported"] + gained
    if imported > snapshot["imported"]:
        snapshot["last_progress_at"] = timezone.now().isoformat()
    snapshot.update(imported=imported, done=done)


def sync_progress(credential) -> SyncProgress | None:
    snapshot = (credential.sync_cursor or {}).get("progress")
    if not snapshot:
        return None
    now = timezone.now()
    last = (
        datetime.fromisoformat(snapshot["last_progress_at"])
        if snapshot.get("last_progress_at")
        else None
    )
    elapsed = (now - datetime.fromisoformat(snapshot["started_at"])).total_seconds()
    gained = max(0, snapshot["imported"] - snapshot["baseline"])
    stale = (now - last).total_seconds() > 300 if last else elapsed > 300
    done = snapshot.get("done", False)
    waiting = bool(credential.sync_error) or (stale and not done)
    rate = gained / elapsed if gained and elapsed > 0 and not waiting and not done else None
    total = snapshot.get("total")
    # A root estimate can be too low. Never claim zero seconds before exhaustion.
    remaining = max(0, total - snapshot["imported"]) if total is not None else None
    eta = ceil(remaining / rate) if rate and remaining else None
    if waiting:
        state = "waiting"
    elif done:
        state = "complete"
    elif rate:
        state = "importing"
    else:
        state = "estimating"
    return SyncProgress(
        state=state,
        total_estimate=total,
        observations_total=snapshot.get("observations"),
        imported=snapshot["imported"],
        traces_per_second=rate,
        estimated_seconds_remaining=0 if state == "complete" else eta,
        counted_at=snapshot.get("counted_at"),
        last_progress_at=last,
        retry_at=credential.next_poll_at if credential.sync_error else None,
    )
