"""``score_trace`` is idempotent per Verdict series and pinned to ``trace_scoring``
so live scoring never contends with eval or generation. ``sweep_unscored_traces``
is the beat backstop; completeness is judged on ``ScoringPass`` rows, never by
re-reading feedback blocks."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db.models import Case, Count, F, Max, OuterRef, Q, Subquery, UUIDField, When
from django.utils import timezone

from overbae.services.eval.trace_scoring import STATUS_ERROR
from overbae.services.eval.trace_scoring import score_trace as _score_trace

logger = logging.getLogger(__name__)

# Bounded lookback — an unbounded scan grows with the whole span table.
_SWEEP_LOOKBACK = timedelta(hours=2)
_SWEEP_LIMIT = 200

# --pool=threads never enforces Celery time limits, so the deadline is
# cooperative: the service stops starting units past it and the sweep resumes
# the rest. The lease must outlive deadline + one unit's judge dispatch.
_UNIT_DEADLINE_SECONDS = 300
_LEASE_SECONDS = 1800


@contextmanager
def _single_flight(trace_id: str, project_id: str) -> Iterator[bool]:
    """Results only land per unit, so a duplicate pass re-runs every in-flight
    judge and multiplies the provider bill."""
    key = f"score_trace:{project_id}:{trace_id}"
    acquired = cache.add(key, "1", timeout=_LEASE_SECONDS)
    try:
        yield acquired
    finally:
        if acquired:
            cache.delete(key)


@shared_task(
    name="overbae.tasks.trace_scoring.score_trace",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def score_trace(self, *, trace_id: str, project_id: str, **kwargs) -> dict[str, Any]:  # noqa: ARG001
    with _single_flight(trace_id, project_id) as acquired:
        if not acquired:
            logger.info("score_trace already in flight for trace=%s", trace_id)
            return {"status": "in_flight", "trace_id": trace_id}
        result = _score_trace(
            trace_id, project_id, deadline=time.monotonic() + _UNIT_DEADLINE_SECONDS
        )
        if result.get("status") == "skipped":
            logger.info("trace scoring skipped trace=%s reason=%s", trace_id, result.get("reason"))
        return result


def _contracts_changed_at(eval_set_id, members_cache: dict):
    """The newest moment the set's trace-scoring contracts moved — a membership
    change or an evaluator edit. A pass finished before it is stale."""
    from overbae.models import EvalSetMember

    if eval_set_id not in members_cache:
        row = EvalSetMember.objects.filter(
            eval_set_id=eval_set_id,
            role=EvalSetMember.Role.TRACE_SCORING,
            enabled=True,
            evaluator__isnull=False,
        ).aggregate(members=Max("created_at"), evaluators=Max("evaluator__updated_at"))
        stamps = [v for v in row.values() if v is not None]
        members_cache[eval_set_id] = max(stamps) if stamps else None
    return members_cache[eval_set_id]


def _needs_scoring(
    trace_id, project_id, eval_set_id, members_cache: dict, root_received_at=None
) -> bool:
    """No finished pass → score. A finished pass with deferred or errored work
    → resume. A pass older than the newest contract change → rescore (the new
    series misses on the Verdict key; old verdicts stay untouched). A pass that
    STARTED before the root landed → rescore: it scored a run still in flight
    (interrupted skips), and the root-triggered enqueue bounced off its
    single-flight lock with no retry."""
    from overbae.models import ScoringPass

    changed_at = _contracts_changed_at(eval_set_id, members_cache)
    if changed_at is None:
        # No enabled trace-scoring members: a pass would skip without creating
        # a row, so enqueueing loops forever.
        return False
    latest = (
        ScoringPass.objects.filter(trace_id=trace_id, project_id=project_id, finished__isnull=False)
        .order_by("-finished")
        .values("started", "finished", "verdict_counts")
        .first()
    )
    if latest is None:
        return True
    counts = latest["verdict_counts"] or {}
    if counts.get("deferred") or counts.get("error"):
        return True
    if root_received_at is not None and latest["started"] < root_received_at:
        return True
    return latest["finished"] < changed_at


def _stale_rootless_traces(since) -> list[dict[str, Any]]:
    """A killed run never exports its root, so its live enqueue never fires."""
    from overbae.models import Span

    settle_before = timezone.now() - timedelta(seconds=settings.TRACE_SETTLE_SECONDS)
    trace_spans = Span.objects.filter(
        trace_id=OuterRef("trace_id"), project_id=OuterRef("project_id")
    )
    return list(
        Span.objects.filter(received_at__gte=since)
        .exclude(resource_attrs__has_key="connector.source")
        .values("trace_id", "project_id")
        .annotate(
            last_received=Max("received_at"),
            root_count=Count("span_id", filter=Q(parent_span_id__isnull=True)),
            attributed=Count("span_id", filter=Q(capability_id__isnull=False)),
            span_count=Count("span_id"),
            fragment_spans=Count(
                "span_id",
                filter=Q(span_type="function") & ~Q(attributes__has_key="overmind.unit_kind"),
            ),
        )
        .filter(root_count=0, attributed__gt=0, last_received__lt=settle_before)
        # Same orphan-fragment gate as the rooted sweep: the service skips
        # these without a ScoringPass or an execution, so they'd re-enqueue.
        .exclude(span_count=1, fragment_spans=1)
        .annotate(
            eval_set_id=Subquery(
                trace_spans.filter(
                    capability__isnull=False, capability__active_eval_set__isnull=False
                ).values("capability__active_eval_set_id")[:1],
                output_field=UUIDField(),
            ),
        )
        .order_by("-last_received")[:_SWEEP_LIMIT]
    )


@shared_task(name="overbae.tasks.trace_scoring.sweep_unscored_traces")
def sweep_unscored_traces() -> dict[str, Any]:
    from overbae.models import Span

    since = timezone.now() - _SWEEP_LOOKBACK
    # ``score_trace`` falls back to any attributed span when the root carries
    # no capability; the sweep must match or such traces lose their backstop.
    child_eval_set = Subquery(
        Span.objects.filter(
            trace_id=OuterRef("trace_id"),
            project_id=OuterRef("project_id"),
            capability__isnull=False,
            capability__active_eval_set__isnull=False,
        ).values("capability__active_eval_set_id")[:1],
        output_field=UUIDField(),
    )
    # An error root voids only a trace whose root is itself the unit; carved-unit
    # traces still score clean units and keep their backstop. ``has_key`` because
    # SQLite can't address dotted JSON keys; >=2 keeps a voided trace (which never
    # mints a ScoringPass) from re-enqueueing forever.
    unit_count = Subquery(
        Span.objects.filter(
            trace_id=OuterRef("trace_id"),
            project_id=OuterRef("project_id"),
            attributes__has_key="overmind.unit_kind",
        )
        .exclude(span_id=OuterRef("span_id"))
        .order_by()
        .values("trace_id")
        .annotate(n=Count("span_id"))
        .values("n")[:1]
    )
    # A boundary-less single-function-span trace skips without a ScoringPass and
    # would loop forever; a ``unit_kind`` key marks a boundary.
    other_spans = Subquery(
        Span.objects.filter(trace_id=OuterRef("trace_id"), project_id=OuterRef("project_id"))
        .exclude(span_id=OuterRef("span_id"))
        .order_by()
        .values("trace_id")
        .annotate(n=Count("span_id"))
        .values("n")[:1]
    )
    roots = (
        Span.objects.filter(parent_span_id__isnull=True, received_at__gte=since)
        .annotate(unit_spans=unit_count, other_spans=other_spans)
        .filter(~Q(status_code=STATUS_ERROR) | Q(unit_spans__gte=2))
        .exclude(
            Q(other_spans__isnull=True)
            & Q(span_type="function")
            & ~Q(attributes__has_key="overmind.unit_kind")
        )
        .exclude(resource_attrs__has_key="connector.source")
        .annotate(
            unit_eval_set_id=Case(
                When(capability__isnull=True, then=child_eval_set),
                default=F("capability__active_eval_set_id"),
                output_field=UUIDField(),
            )
        )
        .filter(unit_eval_set_id__isnull=False)
        .order_by("-received_at")
        .values_list("trace_id", "project_id", "unit_eval_set_id", "received_at")[:_SWEEP_LIMIT]
    )

    members_cache: dict = {}
    enqueued = 0
    for trace_id, project_id, eval_set_id, root_received_at in roots:
        if not _needs_scoring(trace_id, project_id, eval_set_id, members_cache, root_received_at):
            continue
        score_trace.delay(trace_id=trace_id, project_id=str(project_id))
        enqueued += 1

    rootless = _stale_rootless_traces(since)
    from overbae.models import TaskExecution

    have_execution = set(
        TaskExecution.objects.filter(
            trace_id__in=[row["trace_id"] for row in rootless]
        ).values_list("project_id", "trace_id")
    )
    rootless_enqueued = 0
    for row in rootless:
        if row["eval_set_id"]:
            if not _needs_scoring(
                row["trace_id"], row["project_id"], row["eval_set_id"], members_cache
            ):
                continue
        elif (row["project_id"], row["trace_id"]) in have_execution:
            # No eval set: one pass mints the interrupted execution; nothing
            # further can ever be scored, so stop re-enqueuing.
            continue
        score_trace.delay(trace_id=row["trace_id"], project_id=str(row["project_id"]))
        rootless_enqueued += 1
    return {"enqueued": enqueued, "rootless_enqueued": rootless_enqueued}
