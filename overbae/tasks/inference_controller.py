from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)

_STUCK_THRESHOLD_MINUTES = 90


@shared_task
def janitor_stuck_fsm() -> None:
    from django.db.models.functions import Coalesce
    from django.utils import timezone

    from overbae.models import DeployedModel

    cutoff = timezone.now() - timezone.timedelta(minutes=_STUCK_THRESHOLD_MINUTES)
    # Age from the last status change, not from row creation: a redeploy reuses the row, so
    # created_at would make every attempt on an existing model instantly "stuck".
    stale = (
        DeployedModel.objects.filter(
            status__in=[
                DeployedModel.Status.QUANTIZING,
                DeployedModel.Status.DEPLOYING,
                DeployedModel.Status.WARMING,
            ],
        )
        .annotate(entered_at=Coalesce("status_changed_at", "created_at"))
        .filter(entered_at__lt=cutoff)
    )
    stale_pks = list(stale.values_list("pk", flat=True))
    base_ids = list(
        DeployedModel.objects.filter(pk__in=stale_pks, model_id__startswith="base--").values_list(
            "model_id", flat=True
        )
    )
    count = DeployedModel.objects.filter(pk__in=stale_pks).update(
        status=DeployedModel.Status.FAILED,
        error_message=f"Timed out after {_STUCK_THRESHOLD_MINUTES} minutes in transient state.",
    )
    if count:
        logger.warning("Janitor marked %d stuck DeployedModel(s) as FAILED.", count)
    if base_ids:
        _requeue_baseline_deploys(base_ids)


def _requeue_baseline_deploys(model_ids: list[str]) -> None:
    """A stuck base deploy is shared across jobs. ``deploy_base_model_for_eval``
    resumes from FAILED; FT deploys (``ft-*``) are job-scoped and are not re-driven."""
    from overbae.modal.model_registry import get_hf_base
    from overbae.models import FinetuningJob
    from overbae.services.finetuning_eval import job_wants_evals
    from overbae.tasks.model_deployment import base_model_slug, deploy_base_model_for_eval

    wanted = set(model_ids)
    queued: set[str] = set()
    jobs = FinetuningJob.objects.filter(
        eval_set_id__isnull=False,
        eval_dataset_id__isnull=False,
    ).exclude(
        status__in=(FinetuningJob.Status.FAILED, FinetuningJob.Status.CANCELLED),
    )
    for job in jobs:
        if not job_wants_evals(job):
            continue
        try:
            slug = base_model_slug(get_hf_base(job.base_model))
        except Exception:
            # Unknown catalog rows must not fail the whole janitor.
            continue
        if slug in wanted and slug not in queued:
            deploy_base_model_for_eval.delay(job_id=str(job.id))
            queued.add(slug)
