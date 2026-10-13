from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from overbae.models import DataPartitionPlan
from overbae.services.datasets.partition_plans import build


@shared_task(name="overbae.tasks.data_partitions.reconcile")
def reconcile():
    pending = DataPartitionPlan.objects.filter(
        Q(state="queued")
        | Q(state="running", updated_at__lt=timezone.now() - timedelta(seconds=1260))
    )
    for plan_id in pending.values_list("pk", flat=True).iterator():
        build_plan.delay(str(plan_id))


@shared_task(name="overbae.tasks.data_partitions.build_plan", soft_time_limit=1200, time_limit=1260)
def build_plan(plan_id):
    build(plan_id)
