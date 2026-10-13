from datetime import datetime

from celery import shared_task
from django.utils import timezone

from overbae.models import TrainingExperiment


@shared_task(
    name="overbae.tasks.training_experiments.prepare_experiment",
    soft_time_limit=1200,
    time_limit=1260,
)
def prepare_experiment(identifier):
    # Serializers load training task modules while importing the service.
    from overbae.services.training_experiments import prepare

    prepare(TrainingExperiment.objects.get(pk=identifier))


@shared_task(name="overbae.tasks.training_experiments.reconcile")
def reconcile():
    for experiment in TrainingExperiment.objects.filter(state="preparing"):
        started = experiment.protocol.get("preparation_started_at")
        if (
            not started
            or (timezone.now() - datetime.fromisoformat(started)).total_seconds() >= 1260
        ):
            prepare_experiment.delay(str(experiment.pk))

    # The service imports this task through API serializers.
    from overbae.services.training_experiments import reconcile as reconcile_experiment

    for experiment in TrainingExperiment.objects.filter(
        state__in=["launched", "training", "evaluating", "incomplete"]
    ):
        reconcile_experiment(experiment)
