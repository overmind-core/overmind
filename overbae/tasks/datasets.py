"""Landing runs on the ``ingest`` queue; runs and agent turns on ``workshop``."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

LAND_SOFT_LIMIT = 55 * 60
LAND_HARD_LIMIT = 60 * 60
RUN_SOFT_LIMIT = 20 * 60
RUN_HARD_LIMIT = 22 * 60
TURN_SOFT_LIMIT = 40 * 60
TURN_HARD_LIMIT = 42 * 60


def _emit(dataset_id: Any, event: dict[str, Any]) -> None:
    from overbae.services.datasets.notebook import events

    events.publish(dataset_id, {"dataset_id": str(dataset_id), **event})


def _fail(dataset_id: Any, error: str) -> None:
    from overbae.models import Dataset

    Dataset.objects.filter(pk=dataset_id).update(
        state=Dataset.State.ERROR, error=error[:2000], updated_at=timezone.now()
    )
    _emit(dataset_id, {"type": "land_failed", "error": error[:2000]})


@shared_task(
    name="overbae.tasks.datasets.land",
    soft_time_limit=LAND_SOFT_LIMIT,
    time_limit=LAND_HARD_LIMIT,
    acks_late=True,
    reject_on_worker_lost=True,
)
def land(*, dataset_id: str, source: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
    """``source`` is ``{"upload_id", "filename"}``, ``{"rows": [...]}`` or
    ``{"traces": {trace_ids | filters}}``. The diagnosis follows."""
    from overbae.models import Dataset, User
    from overbae.services.datasets import files
    from overbae.services.datasets import land as landing

    dataset = Dataset.objects.filter(pk=dataset_id).first()
    if dataset is None:
        return {"status": "gone"}
    user = User.objects.filter(pk=user_id).first() if user_id else None
    _emit(dataset_id, {"type": "land_started"})

    def progress(done: int) -> None:
        _emit(dataset_id, {"type": "land_progress", "traces": done})

    try:
        if source.get("upload_id"):
            upload_id = source["upload_id"]
            filename = source.get("filename") or files.upload_filename(upload_id) or "upload"
            path = files.upload_data_path(upload_id)
            if not path.exists():
                raise landing.LandError("The upload has expired. Start it again.")
            landing.land_file(dataset, path, filename=filename, user=user)
            files.discard_upload(upload_id)
        elif source.get("rows") is not None:
            landing.land_rows(dataset, list(source["rows"]), user=user, spec={"pasted": True})
        elif source.get("traces") is not None:
            landing.land_traces(dataset, dict(source["traces"]), user=user, on_progress=progress)
        else:
            raise landing.LandError("No source given.")
    except landing.LandError as exc:
        _fail(dataset_id, str(exc))
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — the user must see why landing died
        logger.exception("landing failed for dataset %s", dataset_id)
        _fail(dataset_id, f"Landing failed: {exc}")
        return {"status": "failed", "error": str(exc)}

    dataset.refresh_from_db()
    source_cell = dataset.source
    _emit(dataset_id, {"type": "land_done", "rows": source_cell.rows if source_cell else 0})
    diagnose.apply_async(kwargs={"dataset_id": str(dataset.id), "user_id": user_id})
    return {"status": "ok", "rows": source_cell.rows if source_cell else 0}


@shared_task(
    name="overbae.tasks.datasets.run",
    soft_time_limit=RUN_SOFT_LIMIT,
    time_limit=RUN_HARD_LIMIT,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run(*, dataset_id: str, user_id: str | None = None) -> dict[str, Any]:
    from celery.exceptions import SoftTimeLimitExceeded

    from overbae.models import Dataset, User
    from overbae.services.datasets.notebook import run as run_svc

    dataset = Dataset.objects.filter(pk=dataset_id).first()
    if dataset is None:
        return {"status": "gone"}
    user = User.objects.filter(pk=user_id).first() if user_id else None
    try:
        run_svc.execute(dataset, user=user)
    except SoftTimeLimitExceeded:
        Dataset.objects.filter(pk=dataset_id).update(
            state=Dataset.State.ERROR, error="The run took too long and was stopped."
        )
        _emit(dataset_id, {"type": "run_failed", "error": "The run took too long and was stopped."})
        return {"status": "timeout"}
    except Exception as exc:  # noqa: BLE001 — a run must land in a terminal state
        logger.exception("run failed for dataset %s", dataset_id)
        Dataset.objects.filter(pk=dataset_id).update(
            state=Dataset.State.ERROR, error=str(exc)[:4000]
        )
        _emit(dataset_id, {"type": "run_failed", "error": str(exc)[:4000]})
        return {"status": "failed", "error": str(exc)}
    return {"status": dataset.state}


@shared_task(
    name="overbae.tasks.datasets.diagnose",
    soft_time_limit=TURN_SOFT_LIMIT,
    time_limit=TURN_HARD_LIMIT,
    acks_late=True,
    reject_on_worker_lost=True,
)
def diagnose(*, dataset_id: str, user_id: str | None = None) -> dict[str, Any]:
    from overbae.models import User
    from overbae.services.datasets.notebook import agent

    user = User.objects.filter(pk=user_id).first() if user_id else None
    try:
        for _event in agent.diagnose(dataset_id, user=user):
            pass
    except Exception as exc:  # noqa: BLE001 — the page shows the failure instead of hanging
        logger.exception("diagnosis failed for dataset %s", dataset_id)
        _emit(dataset_id, {"type": "chat_failed", "error": str(exc)[:400]})
        return {"status": "failed"}
    return {"status": "ok"}


@shared_task(
    name="overbae.tasks.datasets.turn",
    soft_time_limit=TURN_SOFT_LIMIT,
    time_limit=TURN_HARD_LIMIT,
    acks_late=True,
    reject_on_worker_lost=True,
)
def turn(*, dataset_id: str, message: str, user_id: str | None = None) -> dict[str, Any]:
    from overbae.models import Dataset, User
    from overbae.services.datasets.notebook import agent

    user = User.objects.filter(pk=user_id).first() if user_id else None
    Dataset.objects.filter(pk=dataset_id, state=Dataset.State.IDLE).update(
        state=Dataset.State.DIAGNOSING
    )
    try:
        for _event in agent.follow_up(dataset_id, message, user=user):
            pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent turn failed for dataset %s", dataset_id)
        _emit(dataset_id, {"type": "chat_failed", "error": str(exc)[:400]})
        return {"status": "failed"}
    finally:
        Dataset.objects.filter(pk=dataset_id, state=Dataset.State.DIAGNOSING).update(
            state=Dataset.State.IDLE
        )
        _emit(dataset_id, {"type": "dataset_changed"})
    return {"status": "ok"}


@shared_task(name="overbae.tasks.datasets.reap_stuck_runs")
def reap_stuck_runs() -> dict[str, Any]:
    """A killed worker never marks its dataset terminal; anything busy for
    longer than the hard limit is dead."""
    from datetime import timedelta

    from overbae.models import Cell, Dataset

    cutoff = timezone.now() - timedelta(seconds=TURN_HARD_LIMIT)
    stuck = Dataset.objects.filter(
        state__in=[Dataset.State.RUNNING, Dataset.State.DIAGNOSING, Dataset.State.LANDING],
        updated_at__lt=cutoff,
    )
    ids = list(stuck.values_list("id", flat=True))
    stuck.update(state=Dataset.State.ERROR, error="The worker stopped before this finished.")
    Cell.objects.filter(dataset_id__in=ids, state=Cell.State.RUNNING).update(
        state=Cell.State.QUEUED
    )
    for dataset_id in ids:
        _emit(
            dataset_id, {"type": "run_failed", "error": "The worker stopped before this finished."}
        )
    return {"reaped": len(ids)}
