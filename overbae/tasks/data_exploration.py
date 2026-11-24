from celery import shared_task
from django.db.models import Q

from overbae.models import DataExploration
from overbae.services.datasets import heartbeat
from overbae.services.datasets.exploration import advance


@shared_task(name="overbae.tasks.data_exploration.reconcile")
def reconcile():
    for identifier in (
        DataExploration.objects.filter(
            Q(state="queued") | (Q(state="running") & heartbeat.lapsed())
        )
        .values_list("pk", flat=True)
        .iterator()
    ):
        run.delay(str(identifier))


@shared_task(name="overbae.tasks.data_exploration.run", soft_time_limit=1200, time_limit=1260)
def run(identifier):
    advance(identifier)
