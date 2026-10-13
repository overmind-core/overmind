from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from overbae.models import DecisionPerformanceRun
from overbae.services.decision_performance import advance


@shared_task(
    name="overbae.tasks.decision_performance.measure", soft_time_limit=1200, time_limit=1260
)
def measure(run_id):
    advance(run_id)


@shared_task(name="overbae.tasks.decision_performance.reconcile")
def reconcile():
    runs = DecisionPerformanceRun.objects.filter(
        Q(state="queued")
        | Q(state="running", updated_at__lt=timezone.now() - timedelta(seconds=1260))
    )
    for run_id in runs.values_list("pk", flat=True).iterator():
        measure.delay(str(run_id))
