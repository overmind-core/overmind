"""Durable admission and completion barrier for evaluation preparation."""

import uuid
from datetime import timedelta
from itertools import batched

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, F, IntegerField, Max, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from overbae.models import EvalRun, EvalSample
from overbae.models.eval_generation import (
    EvalGenerationRun,
    EvalGenerationScheduler,
    EvalGenerationWork,
)

TERMINAL = ("failed", "cancelled", "completed")
IN_FLIGHT = ("queued", "running")
UNFINISHED = ("waiting", "queued", "running")
QUEUED_LEASE = timedelta(minutes=30)
# The worker's hard time limit is 22 minutes; never replay an attempt whose outcome is unknown.
RUNNING_LEASE = timedelta(minutes=23)


class GenerationStoppedError(Exception):
    pass


def ensure_run_active(run_id):
    if not EvalRun.objects.filter(pk=run_id).exclude(status__in=TERMINAL).exists():
        raise GenerationStoppedError("evaluation is no longer active")


@transaction.atomic
def initialize_generation(run, samples):
    EvalGenerationRun.objects.create(run=run)
    for batch in batched(samples, 500, strict=False):
        EvalGenerationWork.objects.bulk_create(
            [EvalGenerationWork(sample=sample) for sample in batch]
        )


@transaction.atomic
def seed_generation(run, samples):
    EvalGenerationRun.objects.create(run=run)
    for batch in batched(samples, 500, strict=False):
        saved = EvalSample.objects.bulk_create(batch)
        EvalGenerationWork.objects.bulk_create(
            [EvalGenerationWork(sample=sample) for sample in saved]
        )


@transaction.atomic
def dispatch_generation(publish, publish_scoring):
    EvalGenerationScheduler.objects.get_or_create(pk=1)
    scheduler = EvalGenerationScheduler.objects.select_for_update().get(pk=1)
    now = timezone.now()
    scheduler.last_tick_at = now
    scheduler.save(update_fields=["last_tick_at"])
    work = EvalGenerationWork.objects
    work.filter(state__in=("waiting", "queued"), sample__run__status__in=TERMINAL).update(
        state="cancelled", finished_at=now
    )
    expired = list(work.filter(state__in=IN_FLIGHT, expires_at__lt=now).select_related("sample"))
    for item in expired:
        saved = bool(item.sample.trajectory or item.sample.error)
        changed = work.filter(pk=item.pk, state=item.state, task_id=item.task_id).update(
            state="done" if saved else "unknown", finished_at=now
        )
        if changed and not saved:
            message = (
                "generation outcome unknown after worker deadline; not replayed"
                if item.state == "running"
                else "generation outcome unknown: dispatch expired before worker claim"
            )
            EvalSample.objects.filter(pk=item.sample_id, error="").update(error=message)

    ready = EvalGenerationRun.objects.filter(run__status="running", scoring_task_id="")
    unfinished = work.filter(sample__run_id=OuterRef("run_id"), state__in=UNFINISHED)
    scoring = 0
    for receipt in ready.annotate(unfinished=Exists(unfinished)).filter(unfinished=False):
        receipt.scoring_task_id = str(uuid.uuid4())
        receipt.save(update_fields=["scoring_task_id"])
        # Consumers check the committed token; a publish/commit ambiguity cannot start work.
        publish_scoring(str(receipt.run_id), receipt.scoring_task_id)
        scoring += 1

    cap = max(1, int(getattr(settings, "EVAL_MAX_IN_FLIGHT", 12)))
    per_run = max(1, int(getattr(settings, "EVAL_MAX_IN_FLIGHT_PER_RUN", 2)))
    available = max(0, cap - work.filter(state__in=IN_FLIGHT).count())
    own = work.filter(sample__run_id=OuterRef("run_id"))
    own_active = (
        own.filter(state__in=IN_FLIGHT)
        .values("sample__run_id")
        .annotate(total=Count("pk"))
        .values("total")
    )
    project_last = (
        EvalGenerationRun.objects.filter(
            run__project_id=OuterRef("run__project_id"), run__status="running"
        )
        .values("run__project_id")
        .annotate(latest=Max("last_admitted_at"))
        .values("latest")
    )
    admitted = 0
    while admitted < available:
        receipt = (
            ready.annotate(
                has_waiting=Exists(own.filter(state="waiting")),
                active=Coalesce(Subquery(own_active, output_field=IntegerField()), 0),
                project_last=Subquery(project_last),
            )
            .filter(has_waiting=True, active__lt=per_run)
            .order_by(
                F("project_last").asc(nulls_first=True),
                F("last_admitted_at").asc(nulls_first=True),
                "run__created_at",
                "run_id",
            )
            .first()
        )
        if receipt is None:
            break
        selected = (
            work.filter(sample__run_id=receipt.run_id, state="waiting")
            .order_by("sample__created_at", "sample_id")
            .first()
        )
        if selected is None:
            break
        selected.task_id = str(uuid.uuid4())
        selected.state = "queued"
        selected.queued_at = timezone.now()
        selected.expires_at = selected.queued_at + QUEUED_LEASE
        selected.save(update_fields=["task_id", "state", "queued_at", "expires_at"])
        EvalSample.objects.filter(pk=selected.sample_id).update(celery_task_id=selected.task_id)
        EvalGenerationRun.objects.filter(pk=receipt.pk).update(last_admitted_at=selected.queued_at)
        EvalRun.objects.filter(pk=receipt.run_id, status="running").update(
            updated_at=selected.queued_at
        )
        publish(str(selected.sample_id), selected.task_id)
        admitted += 1
    return {"admitted": admitted, "scoring": scoring, "expired": len(expired)}


@transaction.atomic
def claim_generation(sample_id, task_id):
    work = EvalGenerationWork.objects.select_for_update().filter(sample_id=sample_id).first()
    sample = EvalSample.objects.select_related("run").filter(pk=sample_id).first()
    if sample is None or sample.run.status in TERMINAL:
        return False
    # Pre-existing broker messages are not re-admitted by the migration.
    if work is None:
        return True
    if work.state != "queued" or work.task_id != task_id:
        return False
    now = timezone.now()
    if work.expires_at is not None and work.expires_at <= now:
        return False
    work.state = "running"
    work.started_at = now
    work.expires_at = now + RUNNING_LEASE
    work.save(update_fields=["state", "started_at", "expires_at"])
    return True


def finish_generation(sample_id, task_id):
    return EvalGenerationWork.objects.filter(
        sample_id=sample_id, task_id=task_id, state="running"
    ).update(state="done", finished_at=timezone.now())


@transaction.atomic
def begin_scoring(run_id, task_id):
    run = EvalRun.objects.select_for_update().filter(pk=run_id).first()
    if run is None or run.status in TERMINAL:
        return False
    receipt = EvalGenerationRun.objects.select_for_update().filter(pk=run_id).first()
    if receipt is None:
        return True
    if receipt.scoring_task_id != task_id or receipt.scoring_started_at is not None:
        return False
    receipt.scoring_started_at = timezone.now()
    receipt.save(update_fields=["scoring_started_at"])
    return True


def generation_wait_is_healthy(run_id):
    if not EvalGenerationWork.objects.filter(sample__run_id=run_id, state__in=UNFINISHED).exists():
        return False
    return EvalGenerationScheduler.objects.filter(
        pk=1, last_tick_at__gte=timezone.now() - timedelta(minutes=2)
    ).exists()
