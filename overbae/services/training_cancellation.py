import logging

from django.db import transaction
from django.utils import timezone

from overbae.celery import app as celery_app
from overbae.models import FinetuningJob, FinetuningJobEvent
from overbae.services import training_monitoring
from overbae.services.finetuning_eval import cancel_related_evals
from overbae.services.finetuning_runner import get_runner

logger = logging.getLogger(__name__)


def cancel(job):
    with transaction.atomic():
        current = FinetuningJob.objects.select_for_update().get(pk=job.pk)
        if current.is_terminal:
            job.refresh_from_db()
            return job
        FinetuningJob.objects.filter(pk=job.pk).update(
            status=FinetuningJob.Status.CANCELLED,
            completed_at=timezone.now(),
        )
    remote_error = ""
    if current.remote_job_id:
        try:
            runner = get_runner(current.provider, job=current)
            runner.cancel(current.remote_job_id)
        except Exception as exc:
            remote_error = str(exc)
            logger.warning("Training cancellation not acknowledged for %s: %s", job.pk, exc)
    if current.celery_task_id:
        try:
            celery_app.control.revoke(current.celery_task_id, terminate=True)
        except Exception:
            logger.exception("Training task revoke failed for %s", job.pk)
    if remote_error:
        FinetuningJob.objects.filter(pk=job.pk).update(
            error_message=f"Cancelled (remote cancel warning: {remote_error})"
        )
    job.refresh_from_db()
    training_monitoring.interrupt(job, reason="cancelled")
    if job.remote_job_id:
        # Import after service initialization: the task imports the shared training lifecycle.
        from overbae.tasks.finetuning import collect_training_evidence

        try:
            collect_training_evidence.apply_async(kwargs={"job_id": str(job.pk)}, countdown=30)
        except Exception:
            logger.exception("Failed to enqueue retained evidence collection for %s", job.pk)
    try:
        cancel_related_evals(job)
    except Exception:
        logger.exception("Failed cancelling related evaluations for %s", job.pk)
    FinetuningJobEvent.objects.create(
        job=job,
        event_type="status_change",
        message="Cancelled by user",
        data={
            "status": "cancelled",
            "remote_acknowledged": not bool(remote_error),
            **({"remote_error": remote_error} if remote_error else {}),
        },
    )
    return job
