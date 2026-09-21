"""Observe live trains; re-enqueue submission only when the driving task is gone.
Beat: every 15 s."""

import logging

from celery import shared_task

from overbae.tasks.utils.task_lock import with_task_lock

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = {"queued", "preparing", "running"}


_RUN_TASK = "overbae.tasks.finetuning.run_finetuning"


def _reconcile() -> dict:
    from overbae.celery import get_celery_app
    from overbae.models.finetuning import FinetuningJob
    from overbae.tasks.finetuning import observe_finetuning_job

    celery_app = get_celery_app()
    inspect = celery_app.control.inspect(timeout=2)

    active: dict = inspect.active() or {}
    reserved: dict = inspect.reserved() or {}
    scheduled: dict = inspect.scheduled() or {}
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
        if job.remote_job_id and job.status in {"running", "preparing", "queued"}:
            observe_finetuning_job(job)
            observed += 1
            continue
        if job.status == "preparing" and not job.remote_job_id:
            # Submit still in flight (inspect is best-effort). A second run_finetuning
            # here starts a second GPU job.
            continue
        if job.celery_task_id and job.celery_task_id in running_task_ids:
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
# Short lock timeout: beat is every 15 s, so a lock orphaned by a worker restart
# must expire before the next tick rather than after the 7-day default.
@with_task_lock(lock_name="finetuning_reconciler", timeout=300)
def reconcile_finetuning_jobs() -> dict:
    return _reconcile()
