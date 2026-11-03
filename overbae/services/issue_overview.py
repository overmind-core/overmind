"""What the Observability page and MCP read: issues with their rates against all traffic.

Everything comes from hourly rollups, so a read costs the same at any traffic. A trace is
counted in the hour it started, in both the numerator and the denominator, which keeps every
sum across hours exact. A rate with no traffic behind it is unknown, never zero.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from math import ceil

from django.db.models import Count, Max, Min, Q, Sum
from django.db.models.functions import TruncDay, TruncHour
from django.utils import timezone

from overbae.innermind import evidence
from overbae.innermind.issues import RESOLVE_AFTER
from overbae.innermind.signals import clip, signal_factory
from overbae.models import (
    Capability,
    Issue,
    IssueHour,
    IssueTrace,
    ModelHour,
    Span,
    TrafficHour,
)

RANGES = {
    "24h": (timedelta(hours=24), "hour"),
    "7d": (timedelta(days=7), "day"),
    "30d": (timedelta(days=30), "day"),
    "90d": (timedelta(days=90), "day"),
    "180d": (timedelta(days=180), "day"),
}
# Repetition across traces and time is what makes a failure worth attention.
MIN_TRACES = 5
MIN_HOURS = 2
# Data exposure is an issue from its first trace, at any rate.
ALWAYS_SURFACE = {"leak"}
HIGH_RATE = 0.05
MEDIUM_RATE = 0.01
RECENT = timedelta(days=7)
TREND_SPAN = timedelta(days=30)
TRAINING_CARDS = 3
TRAINING_REASONS = 3
SYSTEM_LINES = 3
CAUSES = 3
EXAMPLES = 3
RELATED = 10
# The newest passing traces a one-click training run takes, and the share held out to test it.
TRAINING_SET_LIMIT = 20_000
EVAL_PERCENT = 10

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
STATE_RANK = {
    "new": 0,
    "regressed": 1,
    "escalating": 2,
    "ongoing": 3,
    "resolved": 4,
    "muted": 5,
}
ACTIVE = {"new", "regressed", "escalating", "ongoing"}


def hour_floor(moment):
    return moment.replace(minute=0, second=0, microsecond=0)


def window(range_key: str):
    span, size = RANGES[range_key]
    until = timezone.now()
    since = hour_floor(until - span)
    return since, until, since - span, size


def buckets(since, until, size: str) -> list:
    if size == "hour":
        at, step = hour_floor(since), timedelta(hours=1)
    else:
        at, step = since.replace(hour=0, minute=0, second=0, microsecond=0), timedelta(days=1)
    found = []
    while at <= until:
        found.append(at)
        at += step
    return found


def truncate(size: str):
    return TruncHour if size == "hour" else TruncDay


def traffic_by_capability(project_id, since, until) -> dict:
    return dict(
        TrafficHour.objects.filter(project_id=project_id, hour__gte=since, hour__lte=until)
        .values_list("capability_id")
        .annotate(Sum("traces"))
        .order_by()
    )


def severity_of(signal_type: str, cause: str, rate: float | None) -> str:
    if signal_type in ALWAYS_SURFACE:
        return "critical"
    if cause == Issue.Cause.DATA or rate is None or rate < MEDIUM_RATE:
        return "low"
    return "high" if rate >= HIGH_RATE else "medium"


def state_of(issue, now, rate, prior_rate, traces, prior_traces) -> str:
    if issue.muted_at:
        return "muted"
    if issue.resolved_at or now - issue.last_seen_at >= RESOLVE_AFTER:
        return "resolved"
    if issue.regressed_at and now - issue.regressed_at < RECENT:
        return "regressed"
    if now - issue.first_seen_at < RECENT:
        return "new"
    if rate and prior_rate and rate >= 2 * prior_rate and traces - prior_traces >= 3:
        return "escalating"
    return "ongoing"


def describe(issue, counts, traffic, prior_traffic, now) -> dict:
    traces, prior_traces = counts["traces"], counts["prior"]
    denominator = traffic.get(issue.capability_id)
    prior_denominator = prior_traffic.get(issue.capability_id)
    rate = traces / denominator if denominator else None
    prior_rate = prior_traces / prior_denominator if prior_denominator else None
    signal = signal_factory.signals.get(issue.signal_type)
    return {
        "id": issue.pk,
        "family": issue.family,
        "title": issue.title,
        "cause": issue.cause,
        "signal_type": issue.signal_type,
        "severity": severity_of(issue.signal_type, issue.cause, rate),
        "state": state_of(issue, now, rate, prior_rate, traces, prior_traces),
        "capability": issue.capability_id,
        "capability_name": issue.capability.name if issue.capability_id else "",
        "traces": traces,
        "rate": rate,
        "prior_rate": prior_rate,
        "first_seen": issue.first_seen_at,
        "last_seen": issue.last_seen_at,
        "remedy": signal.remedy if signal else "",
        "trend": [],
    }


def issue_counts(project_id, since, until, before, capability_id=None) -> dict:
    rows = IssueHour.objects.filter(project_id=project_id, hour__gte=before, hour__lte=until)
    if capability_id:
        rows = rows.filter(issue__capability_id=capability_id)
    current = Q(hour__gte=since)
    return {
        row["issue_id"]: {
            "traces": row["window_traces"] or 0,
            "prior": row["prior"] or 0,
            "hours": row["hours"],
        }
        for row in rows.values("issue_id")
        .annotate(
            window_traces=Sum("traces", filter=current),
            prior=Sum("traces", filter=Q(hour__lt=since)),
            hours=Count("id", filter=current),
        )
        .order_by()
    }


def surfaced(issue, counts) -> bool:
    if not counts["traces"]:
        return False
    if issue.signal_type in ALWAYS_SURFACE:
        return True
    return counts["traces"] >= MIN_TRACES and counts["hours"] >= MIN_HOURS


def rank(row) -> tuple:
    return (
        SEVERITY_RANK[row["severity"]],
        STATE_RANK[row["state"]],
        -(row["rate"] or 0),
        -row["traces"],
    )


def all_rows(project_id, range_key: str, capability_id=None) -> list[dict]:
    """Every issue that repeats in the range, ranked, without trends."""
    since, until, before, _ = window(range_key)
    counts = issue_counts(project_id, since, until, before, capability_id)
    issues = Issue.objects.filter(pk__in=counts).select_related("capability")
    traffic = traffic_by_capability(project_id, since, until)
    prior = traffic_by_capability(project_id, before, since)
    rows = [
        describe(issue, counts[issue.pk], traffic, prior, until)
        for issue in issues
        if surfaced(issue, counts[issue.pk])
    ]
    rows.sort(key=rank)
    return rows


def with_trends(rows: list[dict], range_key: str) -> list[dict]:
    since, until, _, size = window(range_key)
    spine = buckets(since, until, size)
    position = {at: index for index, at in enumerate(spine)}
    trends = defaultdict(lambda: [0] * len(spine))
    for row in (
        IssueHour.objects.filter(issue__in=[row["id"] for row in rows], hour__gte=since)
        .annotate(at=truncate(size)("hour"))
        .values("issue_id", "at")
        .annotate(count=Sum("traces"))
        .order_by()
    ):
        trends[row["issue_id"]][position[row["at"]]] = row["count"]
    return [{**row, "trend": trends[row["id"]]} for row in rows]


def issue_page(
    project_id, range_key: str, capability_id=None, cause=None, page=1, page_size=25
) -> dict:
    rows = all_rows(project_id, range_key, capability_id)
    if cause:
        rows = [row for row in rows if row["cause"] == cause]
    start = (page - 1) * page_size
    return {"count": len(rows), "results": with_trends(rows[start : start + page_size], range_key)}


def issue_rows(project_id, range_key: str, capability_id=None) -> list[dict]:
    return with_trends(all_rows(project_id, range_key, capability_id), range_key)


def signal_severity(signal_type: str) -> str:
    signal = signal_factory.signals.get(signal_type)
    return signal.severity if signal else "medium"


def latest_models(project_id, since, until) -> dict:
    """Each capability's model in its latest hour, the busiest one if several served it."""
    latest = {}
    for row in (
        ModelHour.objects.filter(project_id=project_id, hour__gte=since, hour__lte=until)
        .values("capability_id", "model")
        .annotate(last=Max("hour"), total=Sum("calls"))
        .order_by("last", "total")
    ):
        latest[row["capability_id"]] = row["model"]
    return latest


def capability_traffic(project_id, since, until) -> dict:
    return {
        row["capability_id"]: row
        for row in TrafficHour.objects.filter(
            project_id=project_id, hour__gte=since, hour__lte=until
        )
        .values("capability_id")
        .annotate(
            traces=Sum("traces"),
            trainable=Sum("trainable"),
            flagged=Sum("flagged"),
            failing=Sum("failing"),
            scored=Sum("scored"),
            score=Sum("score_sum"),
        )
        .order_by()
    }


def training_signal_rows(project_id, range_key: str, capability_id=None, rows=None) -> list[dict]:
    """The training queue, across every capability: the worst issue first, then the capability
    with the most failures a model can learn from."""
    since, until, _, _ = window(range_key)
    rows = all_rows(project_id, range_key, capability_id) if rows is None else rows
    by_capability = defaultdict(list)
    for row in rows:
        if row["state"] in ACTIVE and row["cause"] == Issue.Cause.MODEL and row["capability"]:
            by_capability[row["capability"]].append(row)
    traffic = capability_traffic(project_id, since, until)
    models = latest_models(project_id, since, until)
    signals = []
    for capability, issues in by_capability.items():
        urgent = [row for row in issues if row["severity"] in ("critical", "high")]
        if not urgent:
            continue
        totals = traffic.get(capability, {})
        signals.append(
            {
                "capability": capability,
                "capability_name": urgent[0]["capability_name"],
                "model": models.get(capability, ""),
                "issues": urgent[:TRAINING_REASONS],
                "more_issues": len(issues) - len(urgent[:TRAINING_REASONS]),
                "failing_traces": totals.get("trainable") or 0,
                "passing_traces": (totals.get("traces") or 0) - (totals.get("flagged") or 0),
            }
        )
    signals.sort(
        key=lambda item: (SEVERITY_RANK[item["issues"][0]["severity"]], -item["failing_traces"])
    )
    return signals


def system_lines(rows: list[dict], traffic: dict) -> list[dict]:
    """One line per failure, however many capabilities it hits: a shared outage is one thing
    to fix, measured against the traffic of everything it touched."""
    families = defaultdict(list)
    for row in rows:
        if (
            row["state"] in ACTIVE
            and row["cause"] == Issue.Cause.SYSTEM
            and row["severity"] in ("critical", "high", "medium")
            # Latency and spend spikes are symptoms: they stay in the table.
            and signal_severity(row["signal_type"]) != "low"
        ):
            families[row["family"]].append(row)
    lines = []
    for issues in families.values():
        lead = min(issues, key=rank)
        denominator = sum(traffic.get(row["capability"], 0) for row in issues)
        traces = sum(row["traces"] for row in issues)
        lines.append(
            {
                **lead,
                "traces": traces,
                "rate": traces / denominator if denominator else None,
                "capability_count": len(issues),
            }
        )
    lines.sort(key=rank)
    return lines


def overview(project_id, range_key: str, capability_id=None) -> dict:
    """What to know first: capabilities with a signal to train, then what training can't fix."""
    since, until, _, _ = window(range_key)
    rows = all_rows(project_id, range_key, capability_id)
    traffic = traffic_by_capability(project_id, since, until)
    signals = training_signal_rows(project_id, range_key, capability_id, rows)
    system = system_lines(rows, traffic)
    scoped = {capability_id: traffic.get(capability_id, 0)} if capability_id else traffic
    return {
        "traces_checked": sum(scoped.values()),
        "signals_to_train": len(signals),
        "training_signals": signals[:TRAINING_CARDS],
        "system_issue_count": len(system),
        "system_issues": system[:SYSTEM_LINES],
    }


def model_markers(project_id, since, until, capability_id=None) -> list[dict]:
    """A model's first hour on a capability that already had traffic."""
    rows = ModelHour.objects.filter(project_id=project_id, capability__isnull=False)
    if capability_id:
        rows = rows.filter(capability_id=capability_id)
    first = (
        rows.values("capability_id", "capability__name", "model")
        .annotate(at=Min("hour"))
        .order_by("at")
    )
    opened = {}
    markers = []
    for row in first:
        opened.setdefault(row["capability_id"], row["at"])
        if row["at"] > opened[row["capability_id"]] and since <= row["at"] <= until:
            markers.append(
                {"at": row["at"], "capability_name": row["capability__name"], "model": row["model"]}
            )
    return markers


def scoped(rows, capability_id):
    return rows.filter(capability_id=capability_id) if capability_id else rows


def period_totals(project_id, since, until, capability_id) -> dict:
    traffic = scoped(
        TrafficHour.objects.filter(project_id=project_id, hour__gte=since, hour__lt=until),
        capability_id,
    ).aggregate(
        traces=Sum("traces"), failing=Sum("failing"), scored=Sum("scored"), score=Sum("score_sum")
    )
    models = scoped(
        ModelHour.objects.filter(project_id=project_id, hour__gte=since, hour__lt=until),
        capability_id,
    )
    traces = traffic["traces"] or 0
    failing = traffic["failing"] or 0
    return {
        "traces": traces,
        "failing": failing,
        "failure_rate": failing / traces if traces else None,
        "score": traffic["score"] / traffic["scored"] if traffic["scored"] else None,
        "cost": models.aggregate(cost=Sum("cost"))["cost"],
    }


def trends(project_id, range_key: str, capability_id=None) -> dict:
    since, until, before, size = window(range_key)
    spine = buckets(since, until, size)
    series = {
        at: {"at": at, "traces": 0, "failing": 0, "scored": 0, "score_sum": 0.0, "cost": None}
        for at in spine
    }
    trunc = truncate(size)
    traffic = scoped(
        TrafficHour.objects.filter(project_id=project_id, hour__gte=spine[0], hour__lte=until),
        capability_id,
    )
    models = scoped(
        ModelHour.objects.filter(project_id=project_id, hour__gte=spine[0], hour__lte=until),
        capability_id,
    )
    for row in (
        traffic.annotate(at=trunc("hour"))
        .values("at")
        .annotate(
            traces=Sum("traces"),
            failing=Sum("failing"),
            scored=Sum("scored"),
            score_sum=Sum("score_sum"),
        )
        .order_by()
    ):
        series[row["at"]].update(
            traces=row["traces"],
            failing=row["failing"],
            scored=row["scored"],
            score_sum=row["score_sum"],
        )
    for row in models.annotate(at=trunc("hour")).values("at").annotate(cost=Sum("cost")).order_by():
        series[row["at"]]["cost"] = row["cost"]
    points = [
        {
            "at": point["at"],
            "traces": point["traces"],
            "failing": point["failing"],
            "failure_rate": point["failing"] / point["traces"] if point["traces"] else None,
            "score": point["score_sum"] / point["scored"] if point["scored"] else None,
            "cost": point["cost"],
        }
        for point in series.values()
    ]
    first_hour = scoped(TrafficHour.objects.filter(project_id=project_id), capability_id).aggregate(
        first=Min("hour")
    )["first"]
    return {
        "bucket": size,
        "points": points,
        # A prior window the project only partly existed in is not a fair comparison.
        "comparable": bool(first_hour and first_hour <= before),
        "current": period_totals(project_id, since, until, capability_id),
        "previous": period_totals(project_id, before, since, capability_id),
        "markers": model_markers(project_id, since, until, capability_id),
    }


def capability_rows(project_id, range_key: str) -> list[dict]:
    since, until, _, _ = window(range_key)
    traffic = capability_traffic(project_id, since, until)
    costs = dict(
        ModelHour.objects.filter(project_id=project_id, hour__gte=since, hour__lte=until)
        .values_list("capability_id")
        .annotate(Sum("cost"))
        .order_by()
    )
    issues = defaultdict(list)
    for row in all_rows(project_id, range_key):
        if row["state"] in ACTIVE:
            issues[row["capability"]].append(row)
    rows = []
    for capability in Capability.objects.filter(project_id=project_id).current():
        totals = traffic.get(capability.pk)
        if not totals or not totals["traces"]:
            continue
        own = issues.get(capability.pk, [])
        trainable = [row for row in own if row["cause"] == Issue.Cause.MODEL]
        rows.append(
            {
                "capability": capability.pk,
                "capability_name": capability.name,
                "severity": own[0]["severity"] if own else None,
                "issues": len(own),
                "can_train": any(row["severity"] in ("critical", "high") for row in trainable),
                "traces": totals["traces"],
                "failing": totals["failing"],
                "failure_rate": totals["failing"] / totals["traces"],
                "score": totals["score"] / totals["scored"] if totals["scored"] else None,
                "cost": costs.get(capability.pk),
            }
        )
    rows.sort(
        key=lambda row: (
            not row["can_train"],
            SEVERITY_RANK.get(row["severity"], len(SEVERITY_RANK)),
            -row["failure_rate"],
        )
    )
    return rows


def training_plan(project_id, capability_id, range_key: str) -> dict:
    """Passing traces both teach the model and test it, with no trace in both. Failures stay
    out of each: what they recorded is the wrong answer. The issue's rate on live traffic is
    what shows whether the new model fixed them."""
    since, until, _, _ = window(range_key)
    totals = capability_traffic(project_id, since, until)
    row = next(
        (value for key, value in totals.items() if str(key) == str(capability_id)),
        {},
    )
    passing = max((row.get("traces") or 0) - (row.get("flagged") or 0), 0)
    selected = min(passing, TRAINING_SET_LIMIT)
    eval_rows = ceil(selected * EVAL_PERCENT / 100)
    return {
        "capability": capability_id,
        "passing_traces": passing,
        "failing_traces": row.get("trainable") or 0,
        "training_rows": selected - eval_rows,
        "eval_rows": eval_rows,
        "eval_percent": EVAL_PERCENT,
        "selection": {
            "filters": {
                "project": str(project_id),
                "capability": str(capability_id),
                "start_time_ns__gte": str(int(since.timestamp()) * 1_000_000_000),
                "outcome": "passing",
            },
            "limit": TRAINING_SET_LIMIT,
            "ordering": "-start_time_ns",
        },
    }


def change_issue(issue: Issue, *, state: str | None = None, title: str | None = None) -> None:
    """A decision someone made about an issue, which its counts never overwrite."""
    fields = []
    if state:
        now = timezone.now()
        issue.resolved_at = now if state == "resolve" else None
        issue.muted_at = now if state == "mute" else None
        fields += ["resolved_at", "muted_at"]
    if title:
        issue.title = title
        fields.append("title")
    issue.save(update_fields=fields)


def trace_issues(project_id, trace_id: str) -> list[dict]:
    """The repeating issues one trace belongs to, which is why a single run matters. Rates are
    the last 7 days', the page's default, whatever day the trace ran."""
    shown = {
        item.issue_id: item
        for item in IssueTrace.objects.filter(project_id=project_id, trace_id=trace_id)
    }
    if not shown:
        return []
    rows = {row["id"]: row for row in all_rows(project_id, "7d")}
    since, until, before, _ = window("7d")
    counts = issue_counts(project_id, since, until, before)
    traffic = traffic_by_capability(project_id, since, until)
    prior = traffic_by_capability(project_id, before, since)
    found = []
    for issue in Issue.objects.filter(pk__in=shown).select_related("capability"):
        row = rows.get(issue.pk) or describe(
            issue, counts.get(issue.pk, {"traces": 0, "prior": 0}), traffic, prior, until
        )
        found.append(
            {
                **row,
                "repeating": issue.pk in rows,
                "span_id": shown[issue.pk].span_id,
                "reason": shown[issue.pk].reason,
            }
        )
    found.sort(key=rank)
    return found


def issue_detail(issue: Issue, range_key: str) -> dict:
    """Rate, severity and state over the page's range, so the sheet agrees with its row; the
    trend always covers 30 days, long enough to show what a release changed."""
    since, until, before, _ = window(range_key)
    project_id = issue.project_id
    counts = issue_counts(project_id, since, until, before).get(
        issue.pk, {"traces": 0, "prior": 0, "hours": 0}
    )
    trend_since = until - TREND_SPAN
    spine = buckets(trend_since, until, "day")
    position = {at: index for index, at in enumerate(spine)}
    trend = [0] * len(spine)
    for row in (
        IssueHour.objects.filter(issue=issue, hour__gte=spine[0])
        .annotate(at=TruncDay("hour"))
        .values("at")
        .annotate(count=Sum("traces"))
        .order_by()
    ):
        trend[position[row["at"]]] = row["count"]
    traffic = traffic_by_capability(project_id, since, until)
    prior = traffic_by_capability(project_id, before, since)
    row = describe(issue, counts, traffic, prior, until)
    row["trend"] = trend

    occurrences = IssueTrace.objects.filter(issue=issue, occurred_at__gte=since)
    causes = Counter(
        dict(
            occurrences.values_list("reason").annotate(Count("id")).order_by("-id__count")[:CAUSES]
        )
    )
    latest = list(occurrences.order_by("-occurred_at")[:EXAMPLES])
    spans = Span.objects.in_bulk([item.span_id for item in latest])
    examples = []
    for item in latest:
        span = spans.get(item.span_id)
        if span is None:
            continue
        examples.append(
            {
                "trace_id": item.trace_id,
                "occurred_at": item.occurred_at,
                "reason": item.reason,
                "input": clip(evidence.read_input(span)),
                "output": clip(evidence.read_output(span)),
            }
        )
    related_counts = issue_counts(project_id, since, until, before)
    related = []
    for other in (
        Issue.objects.filter(project_id=project_id, family=issue.family)
        .exclude(pk=issue.pk)
        .select_related("capability")
    ):
        traces = related_counts.get(other.pk, {}).get("traces", 0)
        if traces:
            denominator = traffic.get(other.capability_id)
            related.append(
                {
                    "id": other.pk,
                    "capability_name": other.capability.name if other.capability_id else "",
                    "traces": traces,
                    "rate": traces / denominator if denominator else None,
                }
            )
    related.sort(key=lambda item: -item["traces"])
    return {
        **row,
        "sessions": occurrences.exclude(conversation__isnull=True)
        .values("conversation_id")
        .distinct()
        .count(),
        "buckets": spine,
        "causes": [{"label": label, "count": n} for label, n in causes.most_common(CAUSES)],
        "examples": examples,
        "markers": model_markers(project_id, trend_since, until, issue.capability_id),
        "related": related[:RELATED],
    }
