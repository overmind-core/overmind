from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F
from django.utils import timezone

from overbae.models import LiveScoringProject, LiveScoringScheduler, LiveScoringWork

ACTIVE = ("queued", "running")
QUEUED_LEASE = timedelta(minutes=10)
RUNNING_LEASE = timedelta(minutes=6)


def enqueue(project_id, kind: str, keys: list[str]) -> None:
    LiveScoringProject.objects.get_or_create(project_id=project_id)
    LiveScoringWork.objects.bulk_create(
        [LiveScoringWork(project_id=project_id, kind=kind, key=key) for key in keys],
        ignore_conflicts=True,
    )
    # Completed failed passes may retry after the existing span/trajectory claim grace.
    LiveScoringWork.objects.filter(
        project_id=project_id, kind=kind, key__in=keys, state="done"
    ).update(state="waiting", task_id="", expires_at=None)


@transaction.atomic
def dispatch(publish) -> dict:
    LiveScoringScheduler.objects.get_or_create(pk=1)
    scheduler = (
        LiveScoringScheduler.objects.select_for_update(skip_locked=True).filter(pk=1).first()
    )
    if scheduler is None:
        return {"admitted": 0, "expired": 0, "busy": True}
    now = timezone.now()
    scheduler.last_tick_at = now
    scheduler.save(update_fields=["last_tick_at"])
    work = LiveScoringWork.objects
    # A prefork hard limit terminates the attempt before its running lease expires.
    expired = work.filter(state__in=ACTIVE, expires_at__lte=now).update(
        state="done", finished_at=now, error="worker admission expired"
    )
    cap = max(1, int(getattr(settings, "LIVE_SCORING_MAX_IN_FLIGHT", 16)))
    per_project = max(1, int(getattr(settings, "LIVE_SCORING_MAX_PER_PROJECT", 2)))
    admitted = 0
    while work.filter(state__in=ACTIVE).count() < cap:
        active = dict(
            work.filter(state__in=ACTIVE)
            .values("project_id")
            .annotate(total=Count("pk"))
            .values_list("project_id", "total")
        )
        project = (
            LiveScoringProject.objects.filter(
                project_id__in=work.filter(state="waiting").values("project_id")
            )
            .exclude(project_id__in=[key for key, count in active.items() if count >= per_project])
            .order_by(F("last_admitted_at").asc(nulls_first=True), "project_id")
            .first()
        )
        if project is None:
            break
        kinds = ["span", "trajectory", "signals"]
        if project.last_kind in kinds:
            offset = kinds.index(project.last_kind) + 1
            kinds = kinds[offset:] + kinds[:offset]
        item = None
        for kind in kinds:
            item = (
                work.filter(project_id=project.project_id, state="waiting", kind=kind)
                .order_by("id")
                .first()
            )
            if item is not None:
                break
        if item is None:
            break
        token = str(uuid.uuid4())
        work.filter(pk=item.pk).update(
            state="queued", task_id=token, queued_at=now, expires_at=now + QUEUED_LEASE
        )
        project.last_admitted_at = timezone.now()
        project.last_kind = item.kind
        project.save(update_fields=["last_admitted_at", "last_kind"])
        publish(item.pk, token)
        admitted += 1
    return {"admitted": admitted, "expired": expired}


@transaction.atomic
def claim(work_id: int, token: str):
    item = LiveScoringWork.objects.select_for_update().filter(pk=work_id).first()
    if not item or item.state != "queued" or item.task_id != token:
        return None
    now = timezone.now()
    if item.expires_at <= now:
        return None
    LiveScoringWork.objects.filter(pk=work_id).update(
        state="running", started_at=now, expires_at=now + RUNNING_LEASE
    )
    return item


def finish(work_id: int, token: str, error: str = "") -> None:
    LiveScoringWork.objects.filter(pk=work_id, task_id=token, state="running").update(
        state="done", finished_at=timezone.now(), error=error
    )
