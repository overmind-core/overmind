import json
import logging
import uuid
from datetime import timedelta

from celery import current_app, current_task
from django.conf import settings
from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import Exists, F, OuterRef, Subquery
from django.utils import timezone

from overbae.models import Dataset, DatasetImport, DatasetLandingWork, Project
from overbae.services.datasets.lifecycle import DatasetError

logger = logging.getLogger(__name__)
ACTIVE = ("admitted", "running")
HEADER = "overmind_landing_owner"
WAKE_KEY = "dataset-landing-dispatch"
PUBLISH_SECONDS = 30
LEASE_SECONDS = 5 * 60
EXECUTION_SECONDS = 60 * 60


def enqueue(inputs, task_id):
    inputs = json.loads(
        json.dumps(
            {
                "user_id": None,
                "split": None,
                "infer_capability": True,
                "attachment_request": "",
                **inputs,
            },
            cls=DjangoJSONEncoder,
        )
    )
    dataset = Dataset.objects.get(pk=inputs["dataset_id"])
    with transaction.atomic():
        work, created = DatasetLandingWork.objects.get_or_create(
            pk=task_id,
            defaults={"dataset": dataset, "project_id": dataset.project_id, "inputs": inputs},
        )
        if work.inputs != inputs:
            raise DatasetError(
                "The landing request changed under the same task ID.", code="request_conflict"
            )
        recovered = False
        if (
            not created
            and work.state in ("finished", "failed", "cancelled")
            and dataset.state == "landing"
        ):
            explicit_retry = DatasetImport.objects.filter(
                pk=work.pk, state="queued", published_at__isnull=True
            ).exists()
            if explicit_retry:
                recovered = bool(
                    DatasetLandingWork.objects.filter(
                        pk=work.pk, state=work.state, owner=work.owner
                    ).update(
                        state="waiting",
                        owner=None,
                        queued_at=timezone.now(),
                        started_at=None,
                        finished_at=None,
                        lease_until=None,
                        published_at=None,
                        error="",
                        failure_code="",
                        updated_at=timezone.now(),
                    )
                )
                work.refresh_from_db()
        targets = {dataset.pk}
        evaluation_id = (inputs.get("split") or {}).get("eval_dataset_id")
        if evaluation_id:
            targets.add(uuid.UUID(evaluation_id))
        for target in Dataset.objects.filter(pk__in=targets, project_id=dataset.project_id):
            if recovered or (
                created and target.operation.get("state") not in ("running", "cancelled")
            ):
                Dataset.objects.filter(
                    pk=target.pk, state="landing", operation=target.operation
                ).update(
                    operation={
                        "state": "queued",
                        "task_id": str(work.pk),
                        "queued_at": work.queued_at.isoformat(),
                    }
                )
        transaction.on_commit(wake)
    return work


def wake():
    from overbae.tasks.datasets import dispatch_landings

    try:
        if cache.add(WAKE_KEY, True, timeout=2):
            with current_app.connection_for_write(
                connect_timeout=2,
                transport_options={"socket_connect_timeout": 2, "socket_timeout": 2},
            ) as broker:
                dispatch_landings.apply_async(connection=broker, retry=False, expires=10)
    except Exception:  # noqa: BLE001 — the control tick recovers durable waiting work
        logger.exception("Landing dispatcher wakeup unavailable")


def capacity():
    try:
        workers = cache.get("queue-capacity:landing:workers")
    except Exception:  # noqa: BLE001 — missing telemetry retains the approved warm floor
        workers = None
    return min(
        settings.DATASET_LANDING_MAX_IN_FLIGHT, 2 if workers is None else max(0, int(workers))
    )


def dispatch(publish):
    now = timezone.now()
    reserved = []
    with transaction.atomic():
        # Advisory admission locks never wait behind another dispatcher or a long import.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [0x4F4D4C414E44])
            if not cursor.fetchone()[0]:
                return {"admitted": 0, "busy": True}
        available = max(
            0,
            capacity() - DatasetLandingWork.objects.filter(state__in=ACTIVE).count(),
        )
        for _ in range(available):
            active = DatasetLandingWork.objects.filter(project_id=OuterRef("pk"), state__in=ACTIVE)
            waiting = DatasetLandingWork.objects.filter(project_id=OuterRef("pk"), state="waiting")
            last = DatasetLandingWork.objects.filter(
                project_id=OuterRef("pk"), admitted_at__isnull=False
            ).order_by("-admitted_at")
            project = (
                Project.objects.filter(
                    pk__in=DatasetLandingWork.objects.filter(state="waiting").values("project_id")
                )
                .annotate(
                    has_active=Exists(active),
                    last_admitted=Subquery(last.values("admitted_at")[:1]),
                    first_waiting=Subquery(
                        waiting.order_by("queued_at", "id").values("queued_at")[:1]
                    ),
                )
                .filter(has_active=False)
                .order_by(F("last_admitted").asc(nulls_first=True), "first_waiting", "pk")
                .first()
            )
            if project is None:
                break
            work = (
                DatasetLandingWork.objects.filter(project=project, state="waiting")
                .order_by("queued_at", "id")
                .first()
            )
            owner = uuid.uuid4()
            DatasetLandingWork.objects.filter(pk=work.pk, state="waiting").update(
                state="admitted",
                owner=owner,
                admitted_at=now,
                lease_until=now + timedelta(seconds=PUBLISH_SECONDS),
                updated_at=now,
            )
            reserved.append((str(work.pk), owner, work.inputs))
    for task_id, owner, inputs in reserved:
        try:
            publish(kwargs=inputs, task_id=task_id, headers={HEADER: str(owner)})
        except Exception:  # noqa: BLE001 — late messages are fenced before admission is retried
            logger.exception("Landing publication unacknowledged: %s", task_id)
        else:
            DatasetLandingWork.objects.filter(pk=task_id, owner=owner).update(
                published_at=timezone.now()
            )
    return {"admitted": len(reserved)}


def claim(task_id, owner):
    now = timezone.now()
    with transaction.atomic():
        work = (
            DatasetLandingWork.objects.select_for_update(of=("self",))
            .select_related("dataset")
            .filter(pk=task_id)
            .first()
        )
        if (
            work is None
            or str(work.owner) != str(owner)
            or work.state != "admitted"
            or work.lease_until is None
            or work.lease_until <= now
        ):
            return False
        if work.dataset.state != "landing" or work.dataset.operation.get("state") == "cancelled":
            DatasetLandingWork.objects.filter(pk=work.pk).update(
                state="cancelled", finished_at=now, lease_until=None
            )
            return False
        DatasetLandingWork.objects.filter(pk=work.pk).update(
            state="running",
            started_at=now,
            lease_until=now + timedelta(seconds=LEASE_SECONDS),
            updated_at=now,
        )
    return True


def renew(task_id, owner):
    now = timezone.now()
    work = DatasetLandingWork.objects.filter(
        pk=task_id, owner=owner, state="running", lease_until__gt=now
    ).first()
    if work:
        DatasetLandingWork.objects.filter(
            pk=task_id, owner=owner, state="running", lease_until__gt=now
        ).update(
            lease_until=min(
                now + timedelta(seconds=LEASE_SECONDS),
                work.started_at + timedelta(seconds=EXECUTION_SECONDS),
            ),
            updated_at=now,
        )


def check_current():
    request = getattr(current_task, "request", None)
    owner = (getattr(request, "headers", None) or {}).get(HEADER)
    if not owner:
        return
    work = DatasetLandingWork.objects.filter(
        pk=request.id, owner=owner, state="running", lease_until__gt=timezone.now()
    )
    # Publication holds the dataset lock, which also fences recovery. Locking this
    # row would block the heartbeat throughout file publication and expire its lease.
    if work.first() is None:
        raise DatasetError(
            "Landing ownership expired; no output was published.", code="ownership_lost"
        )


def finish(task_id, owner, outcome=None):
    work = (
        DatasetLandingWork.objects.select_related("dataset")
        .filter(pk=task_id, owner=owner, state="running")
        .first()
    )
    if work is None:
        wake()
        return
    outcome = outcome or {}
    status = outcome.get("status")
    source = (
        DatasetImport.objects.filter(pk=task_id).values("state", "failure_code", "error").first()
    )
    if work.dataset.operation.get("state") == "cancelled" or (
        source and source["state"] == "cancelled"
    ):
        state = "cancelled"
    elif status in ("ok", "landed", "complete"):
        state = "finished"
    elif status in ("cancelled", "gone", "superseded"):
        state = "cancelled"
    else:
        state = "failed"
    code = (source or {}).get("failure_code") or "import_failed"
    error = (
        (source or {}).get("error")
        or outcome.get("error")
        or "The landing execution did not complete."
    )
    DatasetLandingWork.objects.filter(pk=task_id, owner=owner, state="running").update(
        state=state,
        failure_code=code if state == "failed" else "",
        error=str(error)[:2000] if state == "failed" else "",
        lease_until=None,
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )
    wake()


def reconcile(publish):
    now = timezone.now()
    DatasetLandingWork.objects.filter(state="admitted", lease_until__lt=now).update(
        state="waiting",
        owner=None,
        lease_until=None,
        updated_at=now,
    )
    expired = DatasetLandingWork.objects.filter(
        state="running",
        lease_until__lt=now,
    ).values_list("pk", "dataset_id", "inputs__split__eval_dataset_id")[:100]
    queue_seconds = settings.DATASET_IMPORT_MAX_QUEUE_SECONDS or None
    queued = (
        DatasetLandingWork.objects.filter(
            state="waiting", queued_at__lt=now - timedelta(seconds=queue_seconds)
        ).values_list("pk", "dataset_id", "inputs__split__eval_dataset_id")[:100]
        if queue_seconds is not None
        else []
    )
    for task_id, dataset_id, evaluation_id in list(expired) + list(queued):
        with transaction.atomic():
            source = (
                DatasetImport.objects.select_for_update(skip_locked=True).filter(pk=task_id).first()
            )
            if source is None and DatasetImport.objects.filter(pk=task_id).exists():
                continue
            target_ids = {dataset_id}
            if evaluation_id:
                target_ids.add(uuid.UUID(evaluation_id))
            # Lock source receipts and datasets before admission so publication fences recovery.
            targets = list(
                Dataset.objects.select_for_update(skip_locked=True)
                .filter(pk__in=target_ids)
                .order_by("pk")
            )
            if len(targets) != len(target_ids):
                continue
            work = (
                DatasetLandingWork.objects.select_for_update(skip_locked=True)
                .filter(pk=task_id)
                .first()
            )
            if work is None or work.state not in ("running", "waiting"):
                continue
            if work.state == "running" and work.lease_until >= now:
                continue
            if work.state == "waiting" and (
                queue_seconds is None or work.queued_at >= now - timedelta(seconds=queue_seconds)
            ):
                continue
            if source and source.state in ("complete", "cancelled"):
                DatasetLandingWork.objects.filter(pk=task_id).update(
                    state="finished" if source.state == "complete" else "cancelled",
                    error="",
                    failure_code="",
                    finished_at=now,
                    lease_until=None,
                    updated_at=now,
                )
                continue
            error = (
                "The landing worker stopped; retry explicitly from the retained source."
                if work.state == "running"
                else "The import exceeded its configured queue-wait limit."
            )
            code = "worker_timeout" if work.state == "running" else "queue_timeout"
            DatasetLandingWork.objects.filter(pk=task_id).update(
                failure_code=code,
                state="failed",
                error=error,
                finished_at=now,
                lease_until=None,
                updated_at=now,
            )
            for target in targets:
                Dataset.objects.filter(pk=target.pk, state="landing").update(
                    state="error",
                    error=error,
                    updated_at=now,
                    operation={
                        **target.operation,
                        "task_id": str(task_id),
                        "state": "failed",
                        "finished_at": now.isoformat(),
                    },
                )
            DatasetImport.objects.filter(pk=task_id, state__in=("queued", "running")).update(
                state="blocked",
                failure_code=code,
                error=error,
                owner=None,
                lease_until=None,
                updated_at=now,
            )
    DatasetLandingWork.objects.filter(state="waiting").exclude(dataset__state="landing").update(
        state="cancelled", finished_at=now, updated_at=now
    )
    return dispatch(publish)


def status(dataset):
    task_id = dataset.operation.get("task_id")
    if not task_id:
        return None
    work = DatasetLandingWork.objects.filter(dataset=dataset, pk=task_id).first()
    if work is None:
        return None
    return {
        "id": str(work.pk),
        "state": work.state,
        "queued_at": work.queued_at.isoformat(),
        "admitted_at": work.admitted_at.isoformat() if work.admitted_at else None,
        "started_at": work.started_at.isoformat() if work.started_at else None,
        "finished_at": work.finished_at.isoformat() if work.finished_at else None,
        "queue_seconds": (work.started_at - work.queued_at).total_seconds()
        if work.started_at
        else None,
        "per_project_limit": 1,
        "failure_code": work.failure_code or None,
        "error": work.error or None,
    }
