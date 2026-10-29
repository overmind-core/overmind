from celery import shared_task

from overbae.services.dedicated import advance


@shared_task
def advance_dedicated_deployment(*, deployment_id: str) -> None:
    advance(deployment_id)
