from celery import shared_task

from overbae.models import NativeEvaluationPlan
from overbae.services.native_evaluation import advance


@shared_task(name="overbae.tasks.native_evaluation.reconcile")
def reconcile():
    ids = NativeEvaluationPlan.objects.exclude(
        state__in=["completed", "failed", "submission_unknown"]
    ).values_list("id", flat=True)
    for plan_id in ids.iterator():
        advance_plan.delay(str(plan_id))


@shared_task(
    name="overbae.tasks.native_evaluation.advance_plan", soft_time_limit=1200, time_limit=1260
)
def advance_plan(plan_id):
    advance(plan_id)
