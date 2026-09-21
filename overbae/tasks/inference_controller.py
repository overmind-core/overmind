from celery import shared_task
from django.db.models import Q

from overbae.models import FinetuningJob
from overbae.services.deployment import (
    due_deployments,
    ensure_baseline_deployment,
    ensure_training_deployment,
)
from overbae.tasks.model_deployment import advance_model_deployment


@shared_task
def reconcile_deployments() -> int:
    # A commit followed by a lost Celery enqueue must still create the deployment.
    for job_id in (
        FinetuningJob.objects.filter(status="deploying")
        .filter(
            Q(deployed_model__isnull=True) | Q(deployed_model__status__in=("ready", "failed")),
        )
        .values_list("id", flat=True)
    ):
        ensure_training_deployment(str(job_id))
    for job_id in (
        FinetuningJob.objects.exclude(status__in=("failed", "cancelled"))
        .filter(
            eval_dataset__isnull=False,
            eval_set__isnull=False,
            progress__before_evals_started_at__isnull=False,
        )
        .values_list("id", flat=True)
    ):
        ensure_baseline_deployment(str(job_id))
    ids = list(due_deployments().values_list("id", flat=True))
    for deployment_id in ids:
        advance_model_deployment.delay(deployment_id=str(deployment_id))
    return len(ids)
