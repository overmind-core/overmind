import logging

from celery import shared_task

from overbae.tasks.utils.task_lock import with_task_lock

logger = logging.getLogger(__name__)


@shared_task(name="overbae.tasks.baseten_billing_sync.sync_baseten_finetuning_costs")
@with_task_lock(lock_name="baseten_billing_sync", timeout=1800)
def sync_baseten_finetuning_costs(*, lookback_days: int = 14) -> dict:
    """Beat: hourly. Refreshes recent/active Baseten training costs onto FinetuningJob."""
    from overbae.services.baseten_billing import sync_recent_finetuning_costs

    result = sync_recent_finetuning_costs(lookback_days=lookback_days)
    logger.info("Baseten finetuning cost sync: %s", result)
    return result


@shared_task(name="overbae.tasks.baseten_billing_sync.sync_baseten_job_cost")
def sync_baseten_job_cost(*, job_id: str) -> dict | None:
    from overbae.models.finetuning import FinetuningJob
    from overbae.services.baseten_billing import sync_job_cost_window

    try:
        job = FinetuningJob.objects.get(pk=job_id)
    except FinetuningJob.DoesNotExist:
        logger.warning("sync_baseten_job_cost: job %s not found", job_id)
        return None
    return sync_job_cost_window(job)
