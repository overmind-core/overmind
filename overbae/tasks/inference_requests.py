from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from overbae.models import DeployedModel, InferenceCall, InferenceRequest, OperationalRun
from overbae.services.inference_requests import ACTIVE, FINISHED, advance
from overbae.services.provider_progress import collect


@shared_task
def inspect_inference_request(request_id):
    advance(request_id)


@shared_task
def reconcile_inference_requests():
    for request_id in InferenceRequest.objects.filter(
        Q(state__in=ACTIVE, next_poll_at__lte=timezone.now())
        | (Q(state__in=FINISHED) & ~Q(pk__in=InferenceCall.objects.values("pk")))
    ).values_list("id", flat=True)[:100]:
        inspect_inference_request.delay(str(request_id))
    for operation_id in OperationalRun.objects.filter(
        next_collection_at__lte=timezone.now()
    ).values_list("id", flat=True)[:100]:
        collect_operation_progress.delay(str(operation_id))


@shared_task
def collect_operation_progress(operation_id):
    run = OperationalRun.objects.filter(
        pk=operation_id, next_collection_at__lte=timezone.now()
    ).first()
    if run is None:
        return
    context = run.collection
    deployed = (
        DeployedModel.objects.filter(pk=context["deployment_id"], project_id=run.project_id).first()
        if context.get("deployment_id")
        else None
    )
    collect(run, environment=context["environment"], call_id=context["call_id"], deployed=deployed)
