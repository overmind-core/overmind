import uuid

import modal
from django.db import transaction
from django.utils import timezone

from overbae.models import FinetuningJob
from overbae.services import training_release


class SubmissionUnresolvedError(ValueError):
    pass


@transaction.atomic
def claim(job):
    locked = FinetuningJob.objects.select_for_update().get(pk=job.pk)
    if locked.remote_job_id or locked.provider_submission:
        raise SubmissionUnresolvedError(
            "A provider submission already exists. Reconcile its receipt before any retry."
        )
    intent = {
        "state": "submitting",
        "attempt": uuid.uuid4().hex,
        "intent_at": timezone.now().isoformat(),
        "job": str(job.pk),
    }
    FinetuningJob.objects.filter(pk=job.pk).update(provider_submission=intent)
    job.provider_submission = intent
    return intent


@transaction.atomic
def restage(job):
    """Called only after the reconciler confirms the staging task is absent."""
    locked = FinetuningJob.objects.select_for_update().get(pk=job.pk)
    intent = locked.provider_submission
    if (
        locked.provider != "modal"
        or locked.status not in {"preparing", "submission_unknown"}
        or locked.remote_job_id
        or not intent
        or intent.get("state") not in {"submitting", "unknown"}
        or intent.get("run_id")
        or intent.get("dispatch_at")
        or locked.celery_task_id != job.celery_task_id
        or intent != job.provider_submission
    ):
        return False
    progress = locked.progress or {}
    recovered = {
        "task_id": locked.celery_task_id,
        "submission": intent,
        "recovered_at": timezone.now().isoformat(),
        "reason": "Staging task exited before GPU dispatch",
    }
    FinetuningJob.objects.filter(pk=job.pk).update(
        status="queued",
        provider_submission={},
        celery_task_id="",
        error_message="",
        progress={
            **progress,
            "submission_recoveries": [*progress.get("submission_recoveries", []), recovered],
        },
        updated_at=timezone.now(),
    )
    job.refresh_from_db()
    return True


@transaction.atomic
def unknown(job, error):
    locked = FinetuningJob.objects.select_for_update().get(pk=job.pk)
    if locked.remote_job_id or locked.provider_submission.get("attempt") != (
        job.provider_submission or {}
    ).get("attempt"):
        return
    FinetuningJob.objects.filter(pk=job.pk).update(
        status="submission_unknown",
        provider_submission={
            **locked.provider_submission,
            "state": "unknown",
            "error_type": type(error).__name__,
        },
        error_message="Provider submission acknowledgement is unresolved. Reconcile the existing call; do not submit another job.",
    )


@transaction.atomic
def acknowledge(job, remote_id):
    locked = FinetuningJob.objects.select_for_update().get(pk=job.pk)
    if locked.remote_job_id and locked.remote_job_id != remote_id:
        raise ValueError("This job already belongs to a different provider call.")
    FinetuningJob.objects.filter(pk=job.pk).update(
        remote_job_id=remote_id,
        provider_submission={
            **locked.provider_submission,
            "state": "acknowledged",
            "remote_id": remote_id,
            "acknowledged_at": timezone.now().isoformat(),
        },
    )


def reconcile(job, remote_id):
    # The runner imports submission ownership when dispatching.
    from overbae.services.finetuning_runner import get_runner

    if (
        job.provider != "modal"
        or not remote_id.startswith(f"ft-{job.id}-")
        or ":fc-" not in remote_id
    ):
        raise ValueError("Supply the saved Modal call for this exact training job.")
    snapshot = get_runner("modal", job=job).poll(remote_id)
    if snapshot.state not in {"running", "succeeded", "failed", "cancelled"}:
        raise ValueError("The provider has not confirmed this call.")
    if (snapshot.raw or {}).get("meta", {}).get("call_id") != remote_id.split(":", 1)[1]:
        raise ValueError("The provider call does not belong to the saved training attempt.")
    acknowledge(job, remote_id)
    FinetuningJob.objects.filter(pk=job.pk, status="submission_unknown").update(
        status="running", error_message=""
    )
    job.refresh_from_db()
    return job


@transaction.atomic
def dispatching(job, run_id):
    locked = FinetuningJob.objects.select_for_update().get(pk=job.pk)
    if (
        locked.provider_submission.get("state") != "submitting"
        or not job.provider_submission.get("attempt")
        or locked.provider_submission.get("attempt") != job.provider_submission["attempt"]
    ):
        raise SubmissionUnresolvedError("The submission is not owned by this task.")
    FinetuningJob.objects.filter(pk=job.pk).update(
        provider_submission={
            **locked.provider_submission,
            "run_id": run_id,
            "dispatch_at": timezone.now().isoformat(),
        }
    )


def recover(job):
    run_id = job.provider_submission.get("run_id")
    if not run_id or job.remote_job_id:
        return job
    release = training_release.for_job(job)
    snapshot = modal.Function.from_name(
        release["app"], "get_progress", environment_name=release["environment"]
    ).remote(run_id)
    call_id = (snapshot.get("meta") or {}).get("call_id")
    if call_id and snapshot.get("run_id") == run_id:
        return reconcile(job, f"{run_id}:{call_id}")
    return job
