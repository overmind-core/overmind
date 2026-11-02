from collections import Counter
from datetime import datetime, time, timedelta

from django.db.models import Count, F, Func, JSONField, Min, Value
from django.db.models.fields.json import KT, KeyTransform
from django.db.models.functions import TruncDate
from django.utils import timezone

from overbae.innermind.signals import signal_factory

CAUSES = 3
TRACE_IDS = 200
# Rows read for causes and traces; counts come from aggregates and are always complete.
ROWS = 5_000

STATUS_RANK = {"new": 0, "rising": 1, "ongoing": 2, "falling": 3}
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def json_path(path: str) -> Func:
    return Func(
        F("details"), Value(path), function="jsonb_path_query_array", output_field=JSONField()
    )


def cause_label(name: str, reason: str) -> str:
    reason = " ".join((reason or "").split())
    if not reason:
        return name
    if not name or name in reason:
        return reason
    return f"{name}: {reason}"


def trend_status(events: int, previous: int, first_seen, since) -> str:
    if first_seen >= since:
        return "new"
    if events >= 2 * previous and events - previous >= 3:
        return "rising"
    if 2 * events <= previous:
        return "falling"
    return "ongoing"


def signal_insights(events, days: int) -> dict:
    """Signal events of the last ``days`` days grouped by signal type and capability,
    ranked so what needs attention comes first: new and rising issues, then severity,
    then volume. Each insight names its most frequent concrete causes."""
    today = timezone.localdate()
    window = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    index = {day: position for position, day in enumerate(window)}
    since = timezone.make_aware(datetime.combine(window[0], time.min))
    before = since - timedelta(days=days)
    key = ("signal_type", "capability_id")

    current = events.filter(occurred_at__gte=since)
    groups: dict[tuple, dict] = {}
    for row in (
        current.annotate(day=TruncDate("occurred_at"))
        .values(*key, "capability__name", "day")
        .annotate(count=Count("id"))
        .order_by()
    ):
        group = groups.setdefault(
            (row["signal_type"], row["capability_id"]),
            {
                "capability_name": row["capability__name"] or "",
                "daily": [0] * days,
                "causes": Counter(),
                "trace_ids": {},
            },
        )
        group["daily"][index[row["day"]]] += row["count"]
    sessions = {
        (row["signal_type"], row["capability_id"]): row["sessions"]
        for row in current.values(*key)
        .annotate(sessions=Count("conversation_id", distinct=True))
        .order_by()
    }
    previous = {
        (row["signal_type"], row["capability_id"]): row["count"]
        for row in events.filter(occurred_at__gte=before, occurred_at__lt=since)
        .values(*key)
        .annotate(count=Count("id"))
        .order_by()
    }
    first_seen = {
        (row["signal_type"], row["capability_id"]): row["first"]
        for row in events.values(*key).annotate(first=Min("occurred_at")).order_by()
    }

    def read(queryset):
        return (
            queryset.order_by("-occurred_at")
            .values(*key, "id", "trace_id", "message", "occurred_at")
            .annotate(
                why=KT("details__why"),
                trace_ids=KeyTransform("trace_ids", "details"),
                names=json_path("$.evidence[*].name"),
                reasons=json_path("$.evidence[*].reason"),
            )
        )

    def absorb(row):
        group = groups[(row["signal_type"], row["capability_id"])]
        if "latest_event" not in group:
            group.update(
                latest_event=row["id"],
                last_seen=row["occurred_at"],
                message=row["message"],
                why=row["why"] or "",
            )
        for trace_id in row["trace_ids"] or [row["trace_id"]]:
            if trace_id:
                group["trace_ids"][trace_id] = None
        for name, reason in zip(row["names"] or [], row["reasons"] or [], strict=False):
            if label := cause_label(name or "", reason or ""):
                group["causes"][label] += 1

    rows = list(read(current)[:ROWS])
    for row in rows:
        absorb(row)
    for (signal_type, capability_id), group in groups.items():
        if "latest_event" not in group:
            absorb(read(current.filter(signal_type=signal_type, capability_id=capability_id))[0])

    insights = []
    for (signal_type, capability_id), group in groups.items():
        count = sum(group["daily"])
        before_count = previous.get((signal_type, capability_id), 0)
        signal = signal_factory.signals.get(signal_type)
        insights.append(
            {
                "signal_type": signal_type,
                "severity": signal.severity if signal else "medium",
                "status": trend_status(
                    count, before_count, first_seen[(signal_type, capability_id)], since
                ),
                "capability": capability_id,
                "capability_name": group["capability_name"],
                "events": count,
                "previous_events": before_count,
                "daily": group["daily"],
                "traces": len(group["trace_ids"]),
                "sessions": sessions.get((signal_type, capability_id), 0),
                "first_seen": first_seen[(signal_type, capability_id)],
                "last_seen": group["last_seen"],
                "latest_event": group["latest_event"],
                "message": group["message"],
                "why": group["why"],
                "remedy": signal.remedy if signal else "",
                "causes": [
                    {"label": label[:300], "count": n}
                    for label, n in group["causes"].most_common(CAUSES)
                ],
                "trace_ids": list(group["trace_ids"])[:TRACE_IDS],
            }
        )
    insights.sort(
        key=lambda item: (
            STATUS_RANK[item["status"]] > STATUS_RANK["rising"],
            SEVERITY_RANK.get(item["severity"], 1),
            STATUS_RANK[item["status"]],
            -item["events"],
        )
    )
    return {
        "days": [day.isoformat() for day in window],
        "complete": len(rows) < ROWS,
        "insights": insights,
    }
