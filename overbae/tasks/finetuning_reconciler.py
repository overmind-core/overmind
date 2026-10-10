"""Observe live trains; re-enqueue submission only when the driving task is gone.
Beat: every 15 s."""

import logging

from celery import shared_task
from django.utils import timezone

from overbae.services import operational_progress, training_submission, training_transfer
from overbae.tasks.utils.task_lock import with_task_lock

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = {"queued", "preparing", "running", "submission_unknown"}


_RUN_TASK = "overbae.tasks.finetuning.run_finetuning"


def _reconcile() -> dict:
    from overbae.celery import get_celery_app
    from overbae.models.finetuning import FinetuningJob
    from overbae.tasks.finetuning import observe_finetuning_job

    operational_progress.reconcile_training_terminals()
    celery_app = get_celery_app()
    inspect = celery_app.control.inspect(timeout=2)

    active_response, reserved_response, scheduled_response = (
        inspect.active(),
        inspect.reserved(),
        inspect.scheduled(),
    )
    inventory_known = (
        bool(active_response) and reserved_response is not None and scheduled_response is not None
    )
    active, reserved, scheduled = (
        active_response or {},
        reserved_response or {},
        scheduled_response or {},
    )
    running_task_ids: set[str] = set()
    for tasks in (*active.values(), *reserved.values(), *scheduled.values()):
        for t in tasks:
            req = t.get("request", t)  # scheduled entries nest under "request"
            running_task_ids.add(req.get("id"))

    orphaned_jobs = list(
        FinetuningJob.objects.filter(status__in=_ACTIVE_STATUSES).order_by("created_at")
    )
    kicked = 0
    observed = 0
    for job in orphaned_jobs:
        training_transfer.observe(job)
        if job.celery_task_id and job.celery_task_id in running_task_ids:
            continue
        if (
            job.status in {"preparing", "submission_unknown"}
            and inventory_known
            and (timezone.now() - job.updated_at).total_seconds() >= 120
        ):
            training_submission.restage(job)
        if job.status == "submission_unknown":
            from overbae.services.training_submission import recover

            try:
                recover(job)
            except Exception:
                logger.exception("Provider submission reconciliation unavailable for %s", job.id)
            continue
        if job.remote_job_id and job.status in {"running", "preparing", "queued"}:
            observe_finetuning_job(job)
            observed += 1
            continue
        if job.status == "preparing" and not job.remote_job_id:
            if not inventory_known or (timezone.now() - job.updated_at).total_seconds() < 120:
                continue
            if job.provider_submission:
                training_submission.unknown(job, RuntimeError("Submission task disappeared"))
                job.refresh_from_db()
                try:
                    training_submission.recover(job)
                except Exception:
                    logger.exception("Lost submission could not yet be reconciled for %s", job.id)
                continue
        task_name = _RUN_TASK
        logger.info(
            "Reconciler: re-enqueuing %s for orphaned job %s (status=%s)",
            task_name,
            job.id,
            job.status,
        )
        result = celery_app.send_task(task_name, kwargs={"job_id": str(job.id)})
        job.celery_task_id = result.id
        job.save(update_fields=["celery_task_id"])
        kicked += 1

    return {"orphaned_found": len(orphaned_jobs), "kicked": kicked, "observed": observed}


@shared_task(name="overbae.tasks.finetuning_reconciler.reconcile_finetuning_jobs")
# Provider observation can outlive one tick; only a live collector renews its lease.
@with_task_lock(lock_name="finetuning_reconciler", timeout=30, renew=True)
def reconcile_finetuning_jobs() -> dict:
    return _reconcile()
