"""Live trace scoring and failure-row helpers.

Per-evaluator grades come from Verdict rows — the score store every detail
surface reads. The span's slim ``trace_scoring`` block carries only the
composed ``_execution`` marker and the invocations summary, which the list
paths read for coverage and ordering.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone

from overbae.models import Capability, Project, Span, Verdict
from overbae.models.traces import TOOL_OPERATION_TYPES
from overbae.services.eval import dispatch
from overbae.services.eval.trace_scoring import (
    FEEDBACK_KEY,
    INVOCATIONS_SUMMARY_KEY,
)

_SPAN_CAP = 2000
_REASONING_SNIPPET = 300
_RATIONALE_MAX = 300


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def _slim_block(span: Span) -> dict[str, Any]:
    feedback = span.feedback_score if isinstance(span.feedback_score, dict) else {}
    block = feedback.get(FEEDBACK_KEY)
    return block if isinstance(block, dict) else {}


def _invocations_entry(span: Span) -> dict[str, Any] | None:
    entry = _slim_block(span).get(INVOCATIONS_SUMMARY_KEY)
    return entry if isinstance(entry, dict) else None


def verdict_passed(verdict: Verdict) -> bool | None:
    passed = (verdict.metadata or {}).get("passed") if isinstance(verdict.metadata, dict) else None
    return passed if isinstance(passed, bool) else None


def display_verdicts(project_id: Any, span_ids: Iterable[str]) -> dict[str, list[Verdict]]:
    """Displayable verdicts keyed by target span id — latest row per evaluator;
    skip rows and unscored grounding stay out, matching what the scorer
    composed into the block."""
    latest: dict[tuple[str, str], Verdict] = {}
    rows = Verdict.objects.filter(
        project_id=project_id,
        target_kind=Verdict.TargetKind.SPAN,
        target_id__in=list(span_ids),
    ).order_by("-updated_at")
    for verdict in rows:
        if dispatch.is_skip_verdict(verdict):
            continue
        if (
            verdict.evaluator_name == dispatch.GROUNDING_VERDICT_NAME
            and verdict.outcome != Verdict.Outcome.SCORED
        ):
            continue
        latest.setdefault((verdict.target_id, verdict.evaluator_name), verdict)
    out: dict[str, list[Verdict]] = defaultdict(list)
    for (target_id, _), verdict in sorted(latest.items()):
        out[target_id].append(verdict)
    return out


def compact_verdict_scores(
    verdicts: Iterable[Verdict], *, include_rationale: bool = False
) -> dict[str, dict[str, Any]]:
    """Trim verdict rows for LLM tool payloads, keyed by evaluator name."""
    out: dict[str, dict[str, Any]] = {}
    for verdict in verdicts:
        compact: dict[str, Any] = {
            "score": verdict.score,
            "passed": verdict_passed(verdict),
            "outcome": verdict.outcome,
        }
        if include_rationale:
            rationale = str(verdict.explanation or "")
            if len(rationale) > _RATIONALE_MAX:
                rationale = f"{rationale[: _RATIONALE_MAX - 1].rstrip()}…"
            compact["rationale"] = rationale
        out[verdict.evaluator_name] = compact
    return out


def _accumulate_evaluators(
    verdicts_by_span: dict[str, list[Verdict]],
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "values": [], "n_passed": 0, "n_failed": 0, "n_graded": 0}
    )
    for rows in verdicts_by_span.values():
        for verdict in rows:
            row = buckets[verdict.evaluator_name]
            row["n"] += 1
            if isinstance(verdict.score, (int, float)):
                row["values"].append(float(verdict.score))
            passed = verdict_passed(verdict)
            if passed is True:
                row["n_passed"] += 1
                row["n_graded"] += 1
            elif passed is False:
                row["n_failed"] += 1
                row["n_graded"] += 1
    return buckets


def _evaluator_rows(buckets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, row in buckets.items():
        avg_value = round(sum(row["values"]) / len(row["values"]), 3) if row["values"] else None
        rows.append(
            {
                "evaluator": name,
                "n": row["n"],
                "avg_value": avg_value,
                "pass_rate": _rate(row["n_passed"], row["n_graded"]),
                "n_failed": row["n_failed"],
            }
        )

    def _worst_first(entry: dict[str, Any]) -> float:
        for key in ("pass_rate", "avg_value"):
            if entry[key] is not None:
                return entry[key]
        return 1.0

    rows.sort(key=_worst_first)
    return rows


def _apply_deltas(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> None:
    prev_by_name = {row["evaluator"]: row for row in previous}
    for entry in current:
        prev = prev_by_name.get(entry["evaluator"])
        if not prev:
            continue
        if entry["pass_rate"] is not None and prev.get("pass_rate") is not None:
            entry["pass_rate_delta"] = round(entry["pass_rate"] - prev["pass_rate"], 3)
        if entry["avg_value"] is not None and prev.get("avg_value") is not None:
            entry["avg_value_delta"] = round(entry["avg_value"] - prev["avg_value"], 3)


def _window_rollup(project_id: Any, spans: list[Span]) -> tuple[dict[str, dict[str, Any]], int]:
    """(evaluator buckets, distinct scored trace ids) for one span window."""
    by_span = display_verdicts(project_id, [span.span_id for span in spans])
    trace_ids = {
        span.trace_id
        for span in spans
        if by_span.get(span.span_id) or _invocations_entry(span) is not None
    }
    return _accumulate_evaluators(by_span), len(trace_ids)


def aggregate_live_scores(
    project_id: Any,
    scored_spans: Iterable[Span],
    *,
    previous_scored_spans: Iterable[Span] | None = None,
    traces_in_window: int | None = None,
) -> dict[str, Any]:
    """Roll up the verdicts behind live-scored spans into health aggregates."""
    current_buckets, traces_scored = _window_rollup(project_id, list(scored_spans))
    evaluators = _evaluator_rows(current_buckets)

    if previous_scored_spans is not None:
        previous_buckets, _ = _window_rollup(project_id, list(previous_scored_spans))
        _apply_deltas(evaluators, _evaluator_rows(previous_buckets))

    total_passed = sum(row["n_passed"] for row in current_buckets.values())
    total_graded = sum(row["n_graded"] for row in current_buckets.values())
    coverage = _rate(traces_scored, traces_in_window) if traces_in_window else None

    return {
        "traces_scored": traces_scored,
        "traces_in_window": traces_in_window,
        "coverage": coverage,
        "overall_pass_rate": _rate(total_passed, total_graded),
        "evaluators": evaluators,
    }


def _is_entry_point(span: Span) -> bool:
    if (span.span_type or "").strip().lower() == "entry_point":
        return True
    attrs = span.attributes or {}
    explicit = (
        attrs.get("overmind.span.type") or attrs.get("overmind.span_type") or attrs.get("type")
    )
    return isinstance(explicit, str) and explicit.strip().lower() == "entry_point"


def _compact_summary(entry: dict[str, Any], *, include_rationale: bool = False) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "score": entry.get("score"),
        "passed": entry.get("passed"),
        "outcome": entry.get("outcome"),
    }
    if include_rationale:
        compact["rationale"] = str(entry.get("rationale") or "")[:_RATIONALE_MAX]
    return compact


def list_trace_score_fields(
    span: Span, verdicts: list[Verdict]
) -> tuple[dict[str, dict[str, Any]], int, bool]:
    """Compact list-row scores: ``({name: {score, passed}}, n_scored, any_failed)``."""
    compact = {
        verdict.evaluator_name: {"score": verdict.score, "passed": verdict_passed(verdict)}
        for verdict in verdicts
    }
    any_failed = any(
        verdict.outcome == Verdict.Outcome.SCORED and verdict_passed(verdict) is False
        for verdict in verdicts
    )
    summary = _invocations_entry(span)
    if summary is not None:
        compact[INVOCATIONS_SUMMARY_KEY] = {
            "score": summary.get("score"),
            "passed": summary.get("passed"),
        }
        if summary.get("outcome") == "scored" and summary.get("passed") is False:
            any_failed = True
    n_scored = len(verdicts) or (1 if summary is not None else 0)
    return compact, n_scored, any_failed


def trace_score_detail(root: Span, spans: list[Span]) -> dict[str, Any]:
    """Detail payload for ``get_trace``: root scores plus multi-entry children when needed."""
    by_span = display_verdicts(root.project_id, [span.span_id for span in spans])
    root_verdicts = by_span.get(root.span_id) or []
    trace_scores = compact_verdict_scores(root_verdicts, include_rationale=True)
    summary = _invocations_entry(root)
    if summary is not None:
        trace_scores[INVOCATIONS_SUMMARY_KEY] = _compact_summary(summary, include_rationale=True)
    out: dict[str, Any] = {"scoring_mode": "single", "trace_scores": trace_scores}
    if summary is not None and not root_verdicts:
        out["scoring_mode"] = "multi_entry"
        entry_points: list[dict[str, Any]] = []
        for span in spans:
            if not _is_entry_point(span):
                continue
            child_scores = compact_verdict_scores(
                by_span.get(span.span_id) or [], include_rationale=True
            )
            child_summary = _invocations_entry(span)
            if child_summary is not None:
                child_scores[INVOCATIONS_SUMMARY_KEY] = _compact_summary(
                    child_summary, include_rationale=True
                )
            if not child_scores:
                continue
            entry_points.append({"span_id": span.span_id, "trace_scores": child_scores})
        if entry_points:
            out["entry_point_scores"] = entry_points
    return out


def spans_with_live_scores(
    project: Project,
    *,
    capability: Capability | None = None,
    since: datetime,
    until: datetime | None = None,
    limit: int = _SPAN_CAP,
):
    """Spans in [*since*, *until*) carrying a ``trace_scoring`` block."""
    cutoff_ns = int(since.timestamp() * 1_000_000_000)
    qs = (
        Span.objects.filter(project=project, start_time_ns__gte=cutoff_ns)
        .exclude(feedback_score={})
        .filter(feedback_score__has_key=FEEDBACK_KEY)
        .order_by("-start_time_ns")
    )
    if until is not None:
        qs = qs.filter(start_time_ns__lt=int(until.timestamp() * 1_000_000_000))
    if capability is not None:
        qs = qs.filter(capability=capability)
    return qs[:limit]


def live_trace_failures(
    project: Project,
    *,
    capability: Capability | None,
    since: datetime,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Recent traces with at least one failing live score."""
    spans = list(
        spans_with_live_scores(project, capability=capability, since=since, limit=_SPAN_CAP)
    )
    by_span = display_verdicts(project.id, [span.span_id for span in spans])
    by_trace: dict[str, list[Span]] = defaultdict(list)
    for span in spans:
        by_trace[span.trace_id].append(span)

    failures: list[dict[str, Any]] = []
    for trace_id, trace_spans in by_trace.items():
        failed_scores: list[dict[str, str]] = []
        for span in trace_spans:
            for verdict in by_span.get(span.span_id) or []:
                if verdict.outcome == Verdict.Outcome.SCORED and verdict_passed(verdict) is False:
                    failed_scores.append(
                        {
                            "evaluator": verdict.evaluator_name,
                            "reasoning": str(verdict.explanation or "")[:_REASONING_SNIPPET],
                        }
                    )
        if not failed_scores:
            continue
        root = next((s for s in trace_spans if s.parent_span_id is None), trace_spans[0])
        failures.append(
            {
                "trace_ref": f"traces:{trace_id}",
                "trace_id": trace_id,
                "summary": root.name or "",
                "failed_scores": failed_scores,
                "tools": [],
            }
        )

    failures.sort(
        key=lambda row: max(s.start_time_ns or 0 for s in by_trace[row["trace_id"]]),
        reverse=True,
    )
    failures = failures[:limit]
    tools_by_trace = _tools_by_trace(project, [row["trace_id"] for row in failures])
    for row in failures:
        row["tools"] = tools_by_trace.get(row["trace_id"], [])
    return failures


def _tools_by_trace(project: Project, trace_ids: list[str]) -> dict[str, list[str]]:
    """Tool names each trace called, in one query for the whole page."""
    if not trace_ids:
        return {}
    by_trace: dict[str, list[str]] = defaultdict(list)
    rows = Span.objects.filter(
        project_id=project.id,
        trace_id__in=trace_ids,
        span_type__in=TOOL_OPERATION_TYPES,
    ).values_list("trace_id", "name", "attributes")[:_SPAN_CAP]
    for trace_id, name, attributes in rows:
        tool = str((attributes or {}).get("tool.name") or name or "").strip()
        if tool and tool not in by_trace[trace_id] and len(by_trace[trace_id]) < 100:
            by_trace[trace_id].append(tool)
    return dict(by_trace)


def collect_capability_failure_rows(
    project: Project,
    *,
    capability: Capability,
    since_days: int = 7,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Recent traces for a capability that carry at least one failing live verdict."""
    since_days = max(1, min(since_days, 90))
    limit = max(1, int(limit))
    since = timezone.now() - timedelta(days=since_days)
    return live_trace_failures(project, capability=capability, since=since, limit=limit)
