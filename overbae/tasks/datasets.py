"""Imports have dedicated ``landing`` capacity; script runs use ``interactive``."""

from __future__ import annotations

from typing import Any

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from overbae.services.datasets import executions, imports, measure

LAND_SOFT_LIMIT = 55 * 60
LAND_HARD_LIMIT = 60 * 60
EXECUTE_SOFT_LIMIT = 540
EXECUTE_HARD_LIMIT = 600
ANALYTICS_SOFT_LIMIT = 300
ANALYTICS_HARD_LIMIT = 360


@shared_task(
    bind=True,
    name="overbae.tasks.datasets.land",
    soft_time_limit=LAND_SOFT_LIMIT,
    time_limit=LAND_HARD_LIMIT,
    acks_late=True,
    reject_on_worker_lost=True,
)
def land(
    self,
    *,
    dataset_id: str,
    source: dict[str, Any],
    user_id: str | None = None,
    split: dict[str, Any] | None = None,
    infer_capability: bool = True,
) -> dict[str, Any]:
    inputs = {
        "dataset_id": dataset_id,
        "source": source,
        "user_id": user_id,
        "infer_capability": infer_capability,
    }
    if split is not None:
        inputs["split"] = split
    return imports.execute(self.request.id, inputs)


@shared_task(name="overbae.tasks.datasets.reconcile_imports")
def reconcile_imports():
    return imports.reconcile()


@shared_task(
    bind=True,
    name="overbae.tasks.datasets.execute",
    soft_time_limit=EXECUTE_SOFT_LIMIT,
    time_limit=EXECUTE_HARD_LIMIT,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
)
def execute(self, *, execution_id: str, unreachable: int = 0) -> None:
    """Idempotent: a run that is not ``queued`` is owned by another delivery. Retries
    are bounded by ``executions`` (time in the queue, unreachable sandbox)."""
    try:
        retry = executions.perform(execution_id, unreachable=unreachable)
    except SoftTimeLimitExceeded:
        executions.on_soft_limit(execution_id)
        return
    if retry is not None:
        raise self.retry(
            countdown=retry.countdown,
            kwargs={
                "execution_id": execution_id,
                "unreachable": retry.unreachable,
            },
        )


@shared_task(name="overbae.tasks.datasets.sweep_executions")
def sweep_executions() -> dict[str, int]:
    return executions.sweep()


@shared_task(
    name="overbae.tasks.datasets.measure_analytics",
    soft_time_limit=ANALYTICS_SOFT_LIMIT,
    time_limit=ANALYTICS_HARD_LIMIT,
)
def measure_analytics(*, dataset_id: str) -> int:
    return measure.backfill_analytics(dataset_id)
