"""Live scoring health over a window of spans, read from ``Span.eval_attributes``."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean

from overbae.innermind.metrics import FAIL_BELOW
from overbae.models import Span

SPAN_CAP = 20_000


def window(project, capability, start, end):
    spans = Span.objects.filter(project=project, received_at__gte=start, received_at__lt=end)
    return spans.filter(capability=capability) if capability is not None else spans


def metric_rows(attributes: list[dict]) -> dict[str, dict]:
    by_name: dict[str, list[dict]] = defaultdict(list)
    for attrs in attributes:
        for name, metric in (attrs.get("metrics") or {}).items():
            by_name[name].append(metric)
    rows = {}
    for name, results in by_name.items():
        scored = [m["score"] for m in results if m.get("score") is not None]
        rows[name] = {
            "metric": name,
            "type": results[0].get("type", ""),
            "n": len(results),
            "avg_score": round(mean(scored), 3) if scored else None,
            "pass_rate": round(sum(s >= FAIL_BELOW for s in scored) / len(scored), 3)
            if scored
            else None,
            "n_failed": sum(s < FAIL_BELOW for s in scored),
            "n_errors": sum(bool(m.get("error")) for m in results),
        }
    return rows


def summarize(project, capability, start, end) -> dict:
    """Coverage, outcomes and per-metric health for spans received in ``[start, end)``."""
    spans = window(project, capability, start, end)
    total = spans.count()
    attributes = list(
        spans.exclude(eval_attributes={}).values_list("eval_attributes", flat=True)[:SPAN_CAP]
    )
    final = [attrs for attrs in attributes if "score" in attrs]
    scores = [attrs["score"] for attrs in final if attrs["score"] is not None]
    trajectories = [
        attrs["trajectory"]["score"]
        for attrs in final
        if (attrs.get("trajectory") or {}).get("score") is not None
    ]
    return {
        "spans": total,
        "scored": len(scores),
        "abstained": len(final) - len(scores),
        "errored": sum(bool(attrs.get("error")) for attrs in attributes),
        "pending": total - len(final),
        "coverage": round(len(final) / total, 3) if total else None,
        "avg_score": round(mean(scores), 3) if scores else None,
        "pass_rate": round(sum(s >= FAIL_BELOW for s in scores) / len(scores), 3)
        if scores
        else None,
        "trajectories": len(trajectories),
        "avg_trajectory_score": round(mean(trajectories), 3) if trajectories else None,
        "metrics": metric_rows(final),
    }


def failures(project, capability, start, limit: int) -> list[dict]:
    """Newest traces with a failing trajectory, span or individual check."""
    spans = Span.objects.filter(project=project, received_at__gte=start)
    if capability is not None:
        spans = spans.filter(capability=capability)
    found: dict[str, dict] = {}
    for span in (
        spans.exclude(eval_attributes={})
        .order_by("-received_at")
        .only("trace_id", "span_id", "name", "span_type", "eval_attributes", "attributes")[
            :SPAN_CAP
        ]
    ):
        attrs = span.eval_attributes
        trajectory = attrs.get("trajectory") or {}
        failing_trace = trajectory.get("score") is not None and trajectory["score"] < FAIL_BELOW
        failing_span = attrs.get("score") is not None and attrs["score"] < FAIL_BELOW
        failed_trajectory_metrics = [
            {
                "metric": f"trajectory.{name}",
                "score": metric["score"],
                "reason": metric.get("reason", ""),
            }
            for name, metric in (trajectory.get("metrics") or {}).items()
            if metric.get("score") is not None and metric["score"] < FAIL_BELOW
        ]
        failed_span_metrics = [
            {"metric": name, "score": metric["score"], "reason": metric.get("reason", "")}
            for name, metric in (attrs.get("metrics") or {}).items()
            if metric.get("score") is not None and metric["score"] < FAIL_BELOW
        ]
        if not (failing_trace or failing_span or failed_trajectory_metrics or failed_span_metrics):
            continue
        entry = found.setdefault(
            span.trace_id,
            {"trace_id": span.trace_id, "summary": "", "failed": [], "tools": []},
        )
        if failing_trace or failed_trajectory_metrics:
            entry["summary"] = (
                f"trajectory score {trajectory['score']:.2f}"
                if failing_trace
                else "failing trajectory checks"
            )
            entry["failed"] += failed_trajectory_metrics
        if failing_span or failed_span_metrics:
            entry["summary"] = entry["summary"] or (
                f"{span.name or span.span_type} scored {attrs['score']:.2f}"
                if failing_span
                else f"failing checks on {span.name or span.span_type}"
            )
            entry["failed"] += failed_span_metrics
        if span.span_type == "tool_call" and span.name and span.name not in entry["tools"]:
            entry["tools"].append(span.name)
        if len(found) > limit:
            break
    return list(found.values())[:limit]
