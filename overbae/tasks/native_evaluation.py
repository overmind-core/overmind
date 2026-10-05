from datetime import datetime

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from overbae.models import FinetuningJob, NativeEvaluationPlan, TrainingExperiment
from overbae.services.native_evaluation import advance, ready_stages


@shared_task(name="overbae.tasks.native_evaluation.reconcile")
def reconcile():
    # Experiment validation imports API serializers, which load this task module.
    from overbae.services.training_experiments import dispatch
    from overbae.services.training_experiments import reconcile as reconcile_experiment

    queued = FinetuningJob.objects.filter(
        group_id__in=TrainingExperiment.objects.filter(state__in=["launched", "training"]).values(
            "id"
        ),
        status="queued",
        celery_task_id="",
    )
    for job in queued.iterator():
        dispatch(job)
    for experiment in TrainingExperiment.objects.filter(
        state__in=["launched", "training", "evaluating", "incomplete"]
    ).select_related("project"):
        reconcile_experiment(experiment)
    ids = NativeEvaluationPlan.objects.exclude(
        state__in=["draft", "prepared", "paused", "completed", "failed", "submission_unknown"]
    ).values_list("id", flat=True)
    for plan_id in ids.iterator():
        advance_plan.delay(str(plan_id))


@shared_task(
    name="overbae.tasks.native_evaluation.advance_plan", soft_time_limit=1200, time_limit=1260
)
def advance_plan(plan_id, stage=None):
    advance(plan_id, stage=stage)
    plan = NativeEvaluationPlan.objects.get(pk=plan_id)
    for ready in ready_stages(plan):
        with transaction.atomic():
            locked = NativeEvaluationPlan.objects.select_for_update().get(pk=plan.pk)
            call = dict(locked.calls.get(ready, {}))
            queued = call.get("enqueued_at")
            leased = call.get("lease_started_at")
            if (
                call.get("lease")
                and leased
                and (timezone.now() - datetime.fromisoformat(leased)).total_seconds() < 1260
            ) or (
                queued and (timezone.now() - datetime.fromisoformat(queued)).total_seconds() < 60
            ):
                continue
            if ready not in ready_stages(locked):
                continue
            call["enqueued_at"] = timezone.now().isoformat()
            locked.calls = {**locked.calls, ready: call}
            locked.save()
            external = ready not in {"verify_inputs", "fit_calibration", "score"} and any(
                ready.endswith("_" + p["key"]) and p["kind"] == "external"
                for p in locked.config["participants"]
            )
            transaction.on_commit(
                lambda ready=ready, delay=2 if external else 15 if call.get("id") else 0: (
                    advance_plan.apply_async(args=[str(plan.pk), ready], countdown=delay)
                ),
                robust=True,
            )
