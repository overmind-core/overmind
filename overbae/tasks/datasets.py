"""Landing runs on the ``batch`` queue; runs and agent turns on ``interactive``."""

from __future__ import annotations

import logging
from dataclasses import replace
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


def _landed(targets: list) -> dict[str, Any]:
    rows = 0
    for target in targets:
        target.refresh_from_db()
        source_cell = target.source
        landed = source_cell.rows if source_cell else 0
        rows += landed
        _emit(target.id, {"type": "land_done", "rows": landed})
    return {"status": "ok", "rows": rows}


def _ensure_contract(dataset) -> bool:
    dataset.refresh_from_db()
    cell = dataset.active_cell
    if cell is None or not cell.fits(dataset.intent)[0]:
        reason = ""
        if cell is not None:
            reason = cell.fits(dataset.intent)[1]
        _fail(dataset.id, reason or "The table does not fit its intent.")
        return False
    return True


def _land_llm_calls(targets, read, *, user, split, infer_capability: bool) -> bool:
    from overbae.models import Dataset
    from overbae.services.datasets import land as landing
    from overbae.services.datasets import llm_calls

    if split:
        train_records, eval_records = llm_calls.hash_split(read.rows, int(split["eval_percent"]))
        parts = (
            (targets[0], train_records, "train", targets[1]),
            (targets[1], eval_records, "eval", targets[0]),
        )
        with transaction.atomic():
            for target, records, role, sibling in parts:
                shaped = llm_calls.shape(records, target.intent)
                spec = {
                    **read.spec,
                    "split": {
                        "eval_percent": int(split["eval_percent"]),
                        "position": llm_calls.HASH_POSITION,
                        "role": role,
                        "sibling": str(sibling.id),
                    },
                }
                landing.commit(
                    target,
                    landing.Landing(
                        shaped,
                        kind=Dataset.SourceKind.LLM_CALLS,
                        spec=spec,
                        manifest=llm_calls.manifest_for(target.intent),
                    ),
                    user=user,
                    state=Dataset.State.IDLE,
                    infer_capability=infer_capability,
                )
    else:
        target = targets[0]
        landing.commit(
            target,
            landing.Landing(
                llm_calls.shape(read.rows, target.intent),
                kind=Dataset.SourceKind.LLM_CALLS,
                spec=read.spec,
                manifest=llm_calls.manifest_for(target.intent),
            ),
            user=user,
            state=Dataset.State.IDLE,
            infer_capability=infer_capability,
        )
    return all(_ensure_contract(target) for target in targets)


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
def land(
    *,
    dataset_id: str,
    source: dict[str, Any],
    user_id: str | None = None,
    split: dict[str, Any] | None = None,
    infer_capability: bool = True,
) -> dict[str, Any]:
    """``source`` is ``{"uploads": [...]}``, ``{"upload_id", "filename"}``, ``{"rows": [...]}``,
    ``{"traces": {trace_ids | filters}}`` or ``{"llm_calls": {...}}``. With ``split``
    (``eval_dataset_id``, ``eval_percent``, ``position``) the source is read once and cut
    in two. Trace, file and row sources then start diagnosis. An LLM-call source already
    matches its contract, so it settles to idle instead."""
    from overbae.models import Dataset, User
    from overbae.services.datasets import files
    from overbae.services.datasets import land as landing
    from overbae.services.datasets.notebook import agent

    dataset = Dataset.objects.filter(pk=dataset_id).first()
    if dataset is None:
        return {"status": "gone"}
    targets = [dataset]
    if split:
        evaluation = Dataset.objects.filter(pk=split["eval_dataset_id"]).first()
        if evaluation is None:
            return {"status": "gone"}
        targets.append(evaluation)
    if any(target.state != Dataset.State.LANDING for target in targets):
        return {"status": "landed"}
    user = User.objects.filter(pk=user_id).first() if user_id else None
    for target in targets:
        _emit(target.id, {"type": "land_started"})

    def progress(done: int) -> None:
        _emit(dataset_id, {"type": "land_progress", "traces": done})

    upload_id = source.get("upload_id")
    upload_ids = source.get("uploads") or []
    try:
        if upload_ids:
            read = landing.read_uploads(upload_ids)
        elif upload_id:
            filename = source.get("filename") or files.upload_filename(upload_id) or "upload"
            path = files.upload_data_path(upload_id)
            if not path.exists():
                raise landing.LandError("The upload has expired. Start it again.")
            read = landing.read_file(path, filename=filename)
        elif source.get("rows") is not None:
            read = landing.read_rows(list(source["rows"]), spec={"pasted": True})
        elif source.get("traces") is not None:
            read = landing.read_traces(
                dataset.project_id, dict(source["traces"]), on_progress=progress
            )
        elif source.get("llm_calls") is not None:
            from overbae.services.datasets import llm_calls

            intents = ("train", "eval") if split else (dataset.intent,)
            read = llm_calls.read(dataset.project_id, dict(source["llm_calls"]), intents=intents)
        else:
            raise landing.LandError("No source given.")
        if source.get("llm_calls") is not None:
            if not _land_llm_calls(
                targets, read, user=user, split=split, infer_capability=infer_capability
            ):
                return {"status": "failed"}
            return _landed(targets)
        if split:
            cut = {
                "eval_percent": int(split["eval_percent"]),
                "position": split["position"],
                "group_by": split.get("group_by", []),
                "stratify_by": split.get("stratify_by"),
                "deduplicate": split.get("deduplicate", True),
            }
            train_part, eval_part = read.split(**cut)
            # Both halves land or neither does: a lone half would read as a whole dataset.
            with transaction.atomic():
                for target, part, role, sibling in (
                    (targets[0], train_part, "train", targets[1]),
                    (targets[1], eval_part, "eval", targets[0]),
                ):
                    spec = {**part.spec, "split": {**cut, "role": role, "sibling": str(sibling.id)}}
                    landing.commit(
                        target,
                        replace(part, spec=spec),
                        user=user,
                        state=Dataset.State.DIAGNOSING,
                        infer_capability=infer_capability,
                    )
        else:
            landing.commit(
                dataset,
                read,
                user=user,
                state=Dataset.State.DIAGNOSING,
                infer_capability=infer_capability,
            )
    except landing.LandError as exc:
        for target in targets:
            _fail(target.id, str(exc))
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — the dataset must reach a terminal state
        logger.exception("landing failed for dataset %s", dataset_id)
        for target in targets:
            _fail(target.id, "Landing failed on the server. The file was not the cause.")
        return {"status": "failed", "error": str(exc)}
    finally:
        if upload_id:
            files.discard_upload(upload_id)
        for uploaded_id in upload_ids:
            files.discard_upload(uploaded_id)

    rows = 0
    for target in targets:
        target.refresh_from_db()
        source_cell = target.source
        landed = source_cell.rows if source_cell else 0
        rows += landed
        _emit(target.id, {"type": "land_done", "rows": landed})
        try:
            diagnose.apply_async(kwargs={"dataset_id": str(target.id), "user_id": user_id})
        except Exception:  # noqa: BLE001 — a broker failure must not strand the dataset
            logger.exception("could not queue the first scan for dataset %s", target.id)
            agent.settle(target.id)
    return {"status": "ok", "rows": rows}


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
    from overbae.services.datasets.notebook import agent

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

    from overbae.models import Cell, Dataset

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
        found = list(stuck.values_list("id", flat=True))
        Dataset.objects.filter(pk__in=found, state=state).update(
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
