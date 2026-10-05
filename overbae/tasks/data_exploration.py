from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from overbae.models import DataExploration
from overbae.services.datasets.exploration import advance


@shared_task(name="overbae.tasks.data_exploration.reconcile")
def reconcile():
    for identifier in (
        DataExploration.objects.filter(
            Q(state="queued")
            | Q(state="running", updated_at__lt=timezone.now() - timedelta(seconds=1260))
        )
        .values_list("pk", flat=True)
        .iterator()
    ):
        run.delay(str(identifier))


@shared_task(name="overbae.tasks.data_exploration.run", soft_time_limit=1200, time_limit=1260)
def run(identifier):
    advance(identifier)
