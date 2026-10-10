from celery import shared_task

from overbae.models import TrainingPreparation
from overbae.services import operational_progress
from overbae.services.training_preparation import advance


@shared_task
def reconcile():
    for preparation in TrainingPreparation.objects.filter(
        state__in=["queued", "starting", "running"]
    ).values_list("id", flat=True)[:100]:
        inspect_preparation.delay(str(preparation))


@shared_task(acks_late=True)
def inspect_preparation(preparation_id):
    try:
        advance(preparation_id)
    finally:
        prep = (
            TrainingPreparation.objects.select_related("cell__dataset")
            .filter(pk=preparation_id)
            .first()
        )
        if prep:
            operational_progress.preparation(prep)
