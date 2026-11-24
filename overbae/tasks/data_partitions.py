from celery import shared_task
from django.db.models import Q

from overbae.models import DataPartitionPlan
from overbae.services.datasets import heartbeat
from overbae.services.datasets.partition_plans import build


@shared_task(name="overbae.tasks.data_partitions.reconcile")
def reconcile():
    pending = DataPartitionPlan.objects.filter(
        Q(state="queued") | (Q(state="running") & heartbeat.lapsed())
    )
    for plan_id in pending.values_list("pk", flat=True).iterator():
        build_plan.delay(str(plan_id))


@shared_task(name="overbae.tasks.data_partitions.build_plan", soft_time_limit=1200, time_limit=1260)
def build_plan(plan_id):
    build(plan_id)
