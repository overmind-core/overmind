from celery import shared_task

from overbae.services.deployment import (
    advance_deployment,
    ensure_baseline_deployment,
    ensure_training_deployment,
)
from overbae.services.model_activation import advance_activation


@shared_task
def register_finetuned_model(*, job_id: str) -> None:
    ensure_training_deployment(job_id)


@shared_task
def deploy_base_model_for_eval(*, job_id: str) -> None:
    ensure_baseline_deployment(job_id)


@shared_task
def advance_model_deployment(*, deployment_id: str) -> None:
    advance_deployment(deployment_id)


@shared_task
def advance_model_activation(*, activation_id: str) -> None:
    advance_activation(activation_id)
