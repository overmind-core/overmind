from celery import shared_task

from overbae.models import TrainingPreparation
from overbae.services.training_preparation import advance


@shared_task
def reconcile():
    for preparation in TrainingPreparation.objects.filter(
        state__in=["queued", "starting", "running"]
    ).values_list("id", flat=True)[:100]:
        inspect_preparation.delay(str(preparation))


@shared_task(acks_late=True)
def inspect_preparation(preparation_id):
    advance(preparation_id)
