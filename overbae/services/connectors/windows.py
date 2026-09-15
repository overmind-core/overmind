"""Newest-first time-window planner for Langfuse ingest.

Splits a requested range into fixed windows and yields them newest-first.
Each window is bounded by from/to timestamps so pagination cannot skip data
the way a page-capped DESC walk with an advancing watermark can.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_DEFAULT_WINDOW = timedelta(days=1)


@dataclass(frozen=True)
class TimeWindow:
    start: datetime  # inclusive
    end: datetime  # exclusive


def plan_windows(
    window_from: datetime | None,
    window_to: datetime | None,
    *,
    now: datetime | None = None,
    chunk: timedelta = _DEFAULT_WINDOW,
    max_lookback: timedelta | None = None,
) -> list[TimeWindow]:
    """Return time windows covering [*window_from*, *window_to*), newest first.

    Missing bounds default to ``now`` (end) and ``now - max_lookback`` (start),
    or unbounded-past when *max_lookback* is None (then a single open window).
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    end = window_to or now
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)

    if window_from is not None:
        start = window_from if window_from.tzinfo else window_from.replace(tzinfo=UTC)
    elif max_lookback is not None:
        start = end - max_lookback
    else:
        # Unbounded history — one open window ending at *end*. Callers pass
        # fromStartTime=None so Langfuse returns everything before *end*.
        return [TimeWindow(start=datetime.min.replace(tzinfo=UTC), end=end)]

    if start >= end:
        return []

    windows: list[TimeWindow] = []
    cursor = end
    while cursor > start:
        chunk_start = max(start, cursor - chunk)
        windows.append(TimeWindow(start=chunk_start, end=cursor))
        cursor = chunk_start
    return windows
