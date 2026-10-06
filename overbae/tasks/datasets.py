"""Imports have dedicated ``landing`` capacity; runs and turns use ``interactive``."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

LAND_SOFT_LIMIT = 55 * 60
LAND_HARD_LIMIT = 60 * 60
RUN_SOFT_LIMIT = 20 * 60
RUN_HARD_LIMIT = 22 * 60
TURN_SOFT_LIMIT = 40 * 60
TURN_HARD_LIMIT = 42 * 60
REAP_GRACE = 5 * 60


def _emit(dataset_id: Any, event: dict[str, Any]) -> None:
    from overbae.services.datasets.notebook import events

    events.publish(dataset_id, {"dataset_id": str(dataset_id), **event})


@shared_task(
    bind=True,
    name="overbae.tasks.datasets.land",
    soft_time_limit=LAND_SOFT_LIMIT,
    time_limit=LAND_HARD_LIMIT,
    acks_late=True,
    reject_on_worker_lost=True,
)
def land(
    self,
    *,
    dataset_id: str,
    source: dict[str, Any],
    user_id: str | None = None,
    split: dict[str, Any] | None = None,
    infer_capability: bool = True,
) -> dict[str, Any]:
    from overbae.services.datasets import imports

    inputs = {
        "dataset_id": dataset_id,
        "source": source,
        "user_id": user_id,
        "infer_capability": infer_capability,
    }
    if split is not None:
        inputs["split"] = split
    return imports.execute(self.request.id, inputs)


@shared_task(name="overbae.tasks.datasets.reconcile_imports")
def reconcile_imports():
    from overbae.services.datasets import imports

    return imports.reconcile()


@shared_task(
    name="overbae.tasks.datasets.run",
    soft_time_limit=RUN_SOFT_LIMIT,
    time_limit=RUN_HARD_LIMIT,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run(
    *, dataset_id: str, user_id: str | None = None, proposal_id: str | None = None
) -> dict[str, Any]:
    from celery.exceptions import SoftTimeLimitExceeded

    from overbae.models import Dataset, User
    from overbae.services.datasets import dispatch
    from overbae.services.datasets.notebook import run as run_svc

    dataset = Dataset.objects.filter(pk=dataset_id).first()
    if dataset is None:
        return {"status": "gone"}
    if proposal_id and any(proposal_id in item.get("decisions", {}) for item in dataset.chat):
        return {"status": dataset.state}
    user = User.objects.filter(pk=user_id).first() if user_id else None
    try:
        run_svc.execute(
            dataset,
            user=user,
            activate_cell_id=proposal_id,
            hold=Dataset.State.DIAGNOSING if proposal_id else None,
        )
        if dataset.error:
            Dataset.objects.filter(pk=dataset_id).update(state=Dataset.State.ERROR)
            dataset.state = Dataset.State.ERROR
        elif proposal_id:
            proposal = dataset.cells.get(pk=proposal_id)
            dispatch.resume_after_decision(
                dataset.id, proposal.id, proposal.title, "approved", user_id=user_id
            )
            dataset.refresh_from_db()
            _emit(dataset_id, {"type": "dataset_changed"})
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
    bind=True,
    name="overbae.tasks.datasets.diagnose",
    soft_time_limit=TURN_SOFT_LIMIT,
    time_limit=TURN_HARD_LIMIT,
    acks_late=True,
    reject_on_worker_lost=True,
)
def diagnose(self, *, dataset_id: str, user_id: str | None = None) -> dict[str, Any]:
    from overbae.models import User
    from overbae.services.datasets import imports
    from overbae.services.datasets.notebook import agent

    if not imports.claim_diagnosis(dataset_id, self.request.id):
        return {"status": "superseded"}
    user = User.objects.filter(pk=user_id).first() if user_id else None
    try:
        for _event in agent.diagnose(dataset_id, user=user, turn_key=self.request.id or ""):
            pass
    except Exception as exc:  # noqa: BLE001 — the page shows the failure instead of hanging
        logger.exception("diagnosis failed for dataset %s", dataset_id)
        _emit(dataset_id, {"type": "chat_failed", "error": str(exc)[:400]})
        agent.settle(dataset_id)
        return {"status": "failed"}
    return {"status": "ok"}


@shared_task(
    bind=True,
    name="overbae.tasks.datasets.turn",
    soft_time_limit=TURN_SOFT_LIMIT,
    time_limit=TURN_HARD_LIMIT,
    acks_late=True,
    reject_on_worker_lost=True,
)
def turn(
    self, *, dataset_id: str, message: str, user_id: str | None = None, display: str | None = None
) -> dict[str, Any]:
    from overbae.models import User
    from overbae.services.datasets.notebook import agent

    user = User.objects.filter(pk=user_id).first() if user_id else None
    try:
        for _event in agent.follow_up(
            dataset_id, message, user=user, turn_key=self.request.id or "", display=display
        ):
            pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent turn failed for dataset %s", dataset_id)
        _emit(dataset_id, {"type": "chat_failed", "error": str(exc)[:400]})
        agent.settle(dataset_id)
        return {"status": "failed"}
    return {"status": "ok"}


@shared_task(name="overbae.tasks.datasets.reap_stuck_runs")
def reap_stuck_runs() -> dict[str, Any]:
    """A killed worker never marks its dataset terminal; anything busy for
    longer than the hard limit is dead."""
    from datetime import timedelta

    from overbae.models import Cell, Dataset, DatasetImport
    from overbae.services.datasets import imports

    imports.reconcile()
    now = timezone.now()
    ids: list[Any] = []
    for state, limit in (
        (Dataset.State.LANDING, LAND_HARD_LIMIT),
        (Dataset.State.RUNNING, RUN_HARD_LIMIT),
        (Dataset.State.DIAGNOSING, TURN_HARD_LIMIT),
    ):
        stuck = Dataset.objects.filter(
            state=state, updated_at__lt=now - timedelta(seconds=limit + REAP_GRACE)
        )
        if state == Dataset.State.LANDING:
            stuck = stuck.exclude(pk__in=DatasetImport.objects.values("dataset_id")).exclude(
                pk__in=DatasetImport.objects.exclude(evaluation=None).values("evaluation_id")
            )
        with transaction.atomic():
            found = list(stuck.select_for_update(skip_locked=True).values_list("id", flat=True))
            stuck.filter(pk__in=found).update(
                state=Dataset.State.ERROR,
                error="The worker stopped before this finished.",
                updated_at=now,
            )
            ids += found
    Cell.objects.filter(dataset_id__in=ids, state=Cell.State.RUNNING).update(
        state=Cell.State.QUEUED
    )
    for dataset_id in ids:
        _emit(
            dataset_id, {"type": "run_failed", "error": "The worker stopped before this finished."}
        )
    return {"reaped": len(ids)}
