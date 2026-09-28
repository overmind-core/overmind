from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta
from itertools import pairwise

from django.db.models import Avg, Count, Max, Q, Sum
from django.db.models.functions import Trunc
from django.utils import timezone

from overbae.core.errors import InputValidationError
from overbae.models import DeployedModel
from overbae.services.model_catalog import estimate_cost

# Cap the per-model sample pulled into Python for percentile math. Bounds memory
# for very chatty models while staying statistically ample.
METRIC_SAMPLE_LIMIT = 5000


MONITORING_PERIODS = {
    "1h": timedelta(hours=1),
    "24h": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}
MONITORING_SOURCES = ("all", "application")


def monitoring_options(period="all", source="all") -> dict:
    if period not in MONITORING_PERIODS or source not in MONITORING_SOURCES:
        raise InputValidationError("Select a supported inference period and traffic source.")
    return {"period": period, "source": source}


def _monitoring_calls(deployed, period, source):
    monitoring_options(period, source)
    end = timezone.now()
    duration = MONITORING_PERIODS[period]
    start = end - duration if duration else None
    calls = deployed.inference_calls.filter(created_at__lte=end)
    if start:
        calls = calls.filter(created_at__gte=start)
    if source == "application":
        calls = calls.filter(source="application")
    return calls, start, end


def percentile(sorted_xs: list[float], q: float) -> float | None:
    """Linear-interpolated percentile of a pre-sorted list (q in [0, 1])."""
    if not sorted_xs:
        return None
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    idx = q * (len(sorted_xs) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_xs[lo]
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (idx - lo)


def _warm_percentiles(calls, field: str, qs: tuple[float, ...]) -> tuple[float | None, ...]:
    """Percentiles of a warm-call field, robust to long-tail outliers (slow
    clients, very long generations) that a mean would let skew the headline."""
    xs = sorted(
        calls.filter(is_cold=False, outcome="succeeded", **{f"{field}__isnull": False})
        .order_by("-created_at")
        .values_list(field, flat=True)[:METRIC_SAMPLE_LIMIT]
    )
    return tuple(percentile(xs, q) for q in qs)


def model_metrics(deployed: DeployedModel, *, period="all", source="all") -> dict:
    calls, _, _ = _monitoring_calls(deployed, period, source)
    # Coldness is estimated from recent traffic, not measured from the shared GPU pool.
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cold = Q(is_cold=True, outcome="succeeded")
    agg = calls.aggregate(
        last_request_at=Max("created_at"),
        cost_recorded_request_count=Count("cost"),
        request_count=Count("id"),
        prompt_tokens=Sum("prompt_tokens"),
        completion_tokens=Sum("completion_tokens"),
        cost_total=Sum("cost"),
        cost_this_month=Sum("cost", filter=Q(created_at__gte=month_start)),
        cold_start_ms=Avg("latency_ms", filter=cold),
        cold_request_count=Count("id", filter=cold),
        failed_request_count=Count("id", filter=Q(outcome="failed")),
    )
    # Headline latency/throughput use the median (+ p95 tail), not the mean:
    # wall-clock latency bundles slow-client stream draining and very long
    # generations, so a few 100s+ outliers wreck an average. The median is
    # outlier-proof and adapts to each model's own baseline automatically.
    (p50_latency, p95_latency) = _warm_percentiles(calls, "latency_ms", (0.5, 0.95))
    (p50_tps,) = _warm_percentiles(calls, "tokens_per_second", (0.5,))
    prompt = agg["prompt_tokens"] or 0
    completion = agg["completion_tokens"] or 0
    our_cost = agg["cost_total"]
    # Savings vs the capability's original (frontier) model — price the same
    # tokens at that model's OpenRouter rate and subtract our GPU-time cost.
    job = deployed.finetuning_job if deployed.finetuning_job_id else None
    capability = getattr(job, "capability", None) if job else None
    baseline_model = (getattr(capability, "model", "") or "").strip()
    baseline_cost = estimate_cost(baseline_model, prompt, completion) if baseline_model else None
    savings = (
        baseline_cost - our_cost if baseline_cost is not None and our_cost is not None else None
    )
    end_to_end = sorted(
        calls.filter(outcome="succeeded", end_to_end_ms__isnull=False)
        .order_by("-created_at")
        .values_list("end_to_end_ms", flat=True)[:METRIC_SAMPLE_LIMIT]
    )
    latest_failure = (
        calls.filter(outcome="failed")
        .order_by("-created_at")
        .values("id", "created_at", "error_code")
        .first()
    )
    return {
        "last_request_at": agg["last_request_at"],
        "cost_recorded_request_count": agg["cost_recorded_request_count"],
        "failed_request_count": agg["failed_request_count"],
        "cold_request_count": agg["cold_request_count"],
        "end_to_end_p50_ms": percentile(end_to_end, 0.5),
        "end_to_end_p95_ms": percentile(end_to_end, 0.95),
        "latest_failure": latest_failure,
        "request_count": agg["request_count"] or 0,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost": our_cost,
        "cost_this_month": agg["cost_this_month"],
        "cost_is_estimate": our_cost is not None,
        "avg_tokens_per_second": p50_tps,
        "avg_latency_ms": p50_latency,
        "latency_p95_ms": p95_latency,
        "cold_start_ms": agg["cold_start_ms"],
        "baseline_model": baseline_model or None,
        "baseline_cost": baseline_cost,
        "savings": savings,
    }


def _bucket_start(value, granularity):
    parts = {"second": 0, "microsecond": 0}
    if granularity in ("hour", "day"):
        parts["minute"] = 0
    if granularity == "day":
        parts["hour"] = 0
    return value.replace(**parts)


def model_activity(
    deployed: DeployedModel, *, period="all", source="all", granularity="minute"
) -> dict:
    calls, start, end = _monitoring_calls(deployed, period, source)
    if granularity not in ("minute", "hour", "day"):
        raise InputValidationError("Select minute, hour or day buckets.")
    buckets = defaultdict(
        lambda: {
            "request_count": 0,
            "failed_request_count": 0,
            "total_tokens": 0,
            "lat": [],
            "tps": [],
            "end_to_end": [],
        }
    )
    rows = (
        calls.annotate(bucket=Trunc("created_at", granularity))
        .order_by("-created_at")
        .values_list(
            "bucket",
            "prompt_tokens",
            "completion_tokens",
            "latency_ms",
            "tokens_per_second",
            "is_cold",
            "outcome",
            "end_to_end_ms",
        )
    )
    for bucket, prompt, completion, latency, tps, is_cold, outcome, end_to_end in rows.iterator():
        b = buckets[bucket]
        b["request_count"] += 1
        b["total_tokens"] += (prompt or 0) + (completion or 0)
        b["failed_request_count"] += outcome == "failed"
        if outcome == "succeeded":
            samples = {"end_to_end": end_to_end}
            if not is_cold:
                samples.update(lat=latency, tps=tps)
            for key, value in samples.items():
                # Keep the most recent samples per bucket; counts include every request.
                if value is not None and len(b[key]) < METRIC_SAMPLE_LIMIT:
                    b[key].append(value)
    step = {
        "minute": timedelta(minutes=1),
        "hour": timedelta(hours=1),
        "day": timedelta(days=1),
    }[granularity]
    if start:
        bucket = _bucket_start(start, granularity)
        while bucket <= end:
            buckets[bucket]
            bucket += step
    elif buckets:
        # An all-time gap needs its endpoints, not a row for every historical minute.
        recorded = sorted(buckets)
        for left, right in pairwise(recorded):
            if left + step < right:
                buckets[left + step]
                buckets[right - step]
        if recorded[-1] + step <= end:
            buckets[recorded[-1] + step]
            buckets[_bucket_start(end, granularity)]
    points = []
    for bucket, b in sorted(buckets.items()):
        e2e = sorted(b["end_to_end"])
        points.append(
            {
                "bucket": bucket,
                "request_count": b["request_count"],
                "failed_request_count": b["failed_request_count"],
                "total_tokens": b["total_tokens"],
                "avg_latency_ms": percentile(sorted(b["lat"]), 0.5),
                "avg_tokens_per_second": percentile(sorted(b["tps"]), 0.5),
                "end_to_end_p50_ms": percentile(e2e, 0.5),
                "end_to_end_p95_ms": percentile(e2e, 0.95),
            }
        )
    return {"points": points}
