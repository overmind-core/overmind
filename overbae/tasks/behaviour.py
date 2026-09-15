"""Idempotent: rebinding re-enqueues ``score_trace``, whose binder does
``update_or_create`` on the same (project, unit_span) row and no-ops when nothing changed."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.db.models import Max

logger = logging.getLogger(__name__)

_REBIND_LIMIT = 500


@shared_task(name="overbae.tasks.behaviour.rebind_parked_executions")
def rebind_parked_executions(*, project_id: str) -> dict[str, Any]:
    """No sha filter: the binder falls back to the current registry when the
    trace sha was never analyzed, so any new mint can unlock any parked row."""
    from overbae.models import TaskExecution
    from overbae.tasks.trace_scoring import score_trace

    trace_ids = list(
        TaskExecution.objects.filter(
            project_id=project_id,
            binding_source=TaskExecution.BindingSource.UNBOUND,
            behaviour__isnull=True,
        )
        .values("trace_id")
        # ``order_by("-started_at").distinct()`` would be distinct per (trace_id,
        # started_at) and enqueue one trace several times.
        .annotate(latest=Max("started_at"))
        .order_by("-latest")
        .values_list("trace_id", flat=True)[:_REBIND_LIMIT]
    )
    for trace_id in trace_ids:
        score_trace.delay(trace_id=trace_id, project_id=project_id)
    logger.info(
        "[behaviour] rebind enqueued %d trace(s) for project %s", len(trace_ids), project_id
    )
    return {"enqueued": len(trace_ids)}
