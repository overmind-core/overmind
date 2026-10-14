"""Observe live trains; re-enqueue submission only when the driving task is gone.
Beat: every 15 s."""

import logging
import uuid

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

    # None means no worker answered: the tasks are unknown, so nothing is resubmitted.
    replies = (inspect.active(), inspect.reserved(), inspect.scheduled())
    workers_known = all(reply is not None for reply in replies)
    running_task_ids: set[str] = set()
    for reply in replies:
        for tasks in (reply or {}).values():
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
        if not workers_known or job.celery_task_id in running_task_ids:
            continue
        # A task still waiting in the broker is invisible here; ``run_finetuning``
        # claims the job, so whichever of the two runs second exits.
        task_id = str(uuid.uuid4())
        handed_off = FinetuningJob.objects.filter(
            pk=job.pk, status=job.status, remote_job_id=""
        ).update(celery_task_id=task_id)
        if not handed_off:
            continue
        logger.info(
            "Reconciler: re-enqueuing %s for orphaned job %s (status=%s)",
            _RUN_TASK,
            job.id,
            job.status,
        )
        celery_app.send_task(_RUN_TASK, kwargs={"job_id": str(job.id)}, task_id=task_id)
        kicked += 1

    return {"orphaned_found": len(orphaned_jobs), "kicked": kicked, "observed": observed}


@shared_task(name="overbae.tasks.finetuning_reconciler.reconcile_finetuning_jobs")
# Short lock timeout: beat is every 15 s, so a lock orphaned by a worker restart
# must expire before the next tick rather than after the 7-day default.
@with_task_lock(lock_name="finetuning_reconciler", timeout=300)
def reconcile_finetuning_jobs() -> dict:
    return _reconcile()
