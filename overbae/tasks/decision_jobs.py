from celery import shared_task

from overbae.models import DecisionJob
from overbae.services import decision_jobs


@shared_task(acks_late=True, time_limit=900, soft_time_limit=840)
def start(job_id):
    decision_jobs.start(job_id)


@shared_task(acks_late=True)
def advance(job_id):
    decision_jobs.advance(job_id)


@shared_task
def reconcile():
    for job_id in DecisionJob.objects.filter(status__in=DecisionJob.ACTIVE).values_list(
        "id", flat=True
    )[:200]:
        if DecisionJob.objects.filter(pk=job_id, status=DecisionJob.Status.QUEUED).exists():
            start.delay(str(job_id))
        else:
            advance.delay(str(job_id))
