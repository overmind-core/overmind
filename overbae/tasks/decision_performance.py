from celery import shared_task
from django.db.models import Q

from overbae.models import DecisionPerformanceRun
from overbae.services.datasets import heartbeat
from overbae.services.decision_performance import advance


@shared_task(
    name="overbae.tasks.decision_performance.measure", soft_time_limit=1200, time_limit=1260
)
def measure(run_id):
    advance(run_id)


@shared_task(name="overbae.tasks.decision_performance.reconcile")
def reconcile():
    runs = DecisionPerformanceRun.objects.filter(
        Q(state="queued") | (Q(state="running") & heartbeat.lapsed())
    )
    for run_id in runs.values_list("pk", flat=True).iterator():
        measure.delay(str(run_id))
