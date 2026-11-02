from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone


def signal_trend(events, days: int) -> dict:
    """Daily event counts per signal type over the last ``days`` days, today included,
    in the server time zone the ``occurred_on`` filter uses."""
    today = timezone.localdate()
    window = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    rows = (
        events.filter(occurred_at__date__gte=window[0])
        .annotate(day=TruncDate("occurred_at"))
        .values("signal_type", "day")
        .annotate(count=Count("id"))
        .order_by()
    )
    index = {day: position for position, day in enumerate(window)}
    series: dict[str, list[int]] = {}
    for row in rows:
        counts = series.setdefault(row["signal_type"], [0] * days)
        counts[index[row["day"]]] = row["count"]
    return {
        "days": [day.isoformat() for day in window],
        "series": sorted(
            (
                {"signal_type": signal_type, "total": sum(counts), "counts": counts}
                for signal_type, counts in series.items()
            ),
            key=lambda item: (-item["total"], item["signal_type"]),
        ),
    }
