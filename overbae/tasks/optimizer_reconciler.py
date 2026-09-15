"""Optimizer reconciler — re-drives stuck experiment FSMs after restarts or worker crashes."""

import logging

from celery import shared_task

from overbae.tasks.utils.task_lock import with_task_lock

logger = logging.getLogger(__name__)

_DRIVE_STATUSES = frozenset(["evaluating_baseline_outputs", "evaluating_candidate_outputs"])


@shared_task(name="overbae.tasks.optimizer_reconciler.reconcile_optimizer_experiments")
@with_task_lock(lock_name="optimizer_reconciler", timeout=300)
def reconcile_optimizer_experiments() -> dict:
    from overbae.models.optimizer import OptimizerExperiment, run_experiment_advance

    rows = list(
        OptimizerExperiment.objects.filter(status__in=_DRIVE_STATUSES).values_list("id", "status")
    )
    if not rows:
        logger.debug("optimizer reconciler: no non-terminal experiments")
        return {"dispatched": 0}

    for exp_id, status in rows:
        logger.info("optimizer reconciler dispatching advance exp=%s status=%s", exp_id, status)
        run_experiment_advance.delay(str(exp_id))

    logger.info("optimizer reconciler dispatched %d experiments", len(rows))
    return {"dispatched": len(rows)}
