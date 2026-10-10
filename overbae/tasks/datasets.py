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
REAP_GRACE = 5 * 60


def _landed(targets: list) -> dict[str, Any]:
    rows = 0
    for target in targets:
        target.refresh_from_db()
        if target.operation.get("state") == "cancelled":
            continue
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
    from overbae.services.datasets import events

    events.publish(dataset_id, {"dataset_id": str(dataset_id), **event})


def _fail(dataset_id: Any, error: str) -> None:
    from overbae.models import Dataset

    Dataset.objects.filter(pk=dataset_id).update(
        state=Dataset.State.ERROR, error=error[:2000], updated_at=timezone.now()
    )
    _emit(dataset_id, {"type": "land_failed", "error": error[:2000]})


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
    attachment_request: str = "",
) -> dict[str, Any]:
    """``source`` is ``{"uploads": [...]}``, ``{"upload_id", "filename"}``, ``{"rows": [...]}``,
    ``{"traces": {trace_ids | filters}}`` or ``{"llm_calls": {...}}``. With ``split``
    (``eval_dataset_id``, ``eval_percent``, ``position``) the source is read once and cut
    in two. Landing records evidence and returns to idle without scheduling an agent."""
    from overbae.models import Dataset, User
    from overbae.services.datasets import attachments, files, imports, operations

    if not attachment_request:
        inputs = {
            "dataset_id": dataset_id,
            "source": source,
            "user_id": user_id,
            "infer_capability": infer_capability,
        }
        if split is not None:
            inputs["split"] = split
        return imports.execute(self.request.id, inputs)
    from overbae.services.datasets import land as landing

    dataset = Dataset.objects.filter(pk=dataset_id).first()
    if dataset is None:
        return {"status": "gone"}
    targets = [dataset]
    if split:
        evaluation = Dataset.objects.filter(pk=split["eval_dataset_id"]).first()
        if evaluation is None:
            return {"status": "gone"}
        targets.append(evaluation)
    with transaction.atomic():
        locked = {
            target.pk: target
            for target in Dataset.objects.select_for_update()
            .filter(pk__in=[target.pk for target in targets])
            .order_by("pk")
        }
        if len(locked) != len(targets):
            return {"status": "gone"}
        targets = [locked[target.pk] for target in targets]
        dataset = targets[0]
        if any(
            target.state != Dataset.State.LANDING
            or target.source_spec.get("attachment_request", "") != attachment_request
            for target in targets
        ):
            if split:
                for target in targets:
                    if (
                        target.state == Dataset.State.LANDING
                        and target.source_spec.get("attachment_request", "") == attachment_request
                        and target.operation.get("state") not in {"running", "cancelled"}
                    ):
                        _fail(
                            target.pk,
                            "The paired source import was cancelled or replaced. Attach the source again.",
                        )
            return {"status": "landed"}
        # An unacknowledged worker kill must not trigger an unbounded import retry loop.
        if (self.request.delivery_info or {}).get("redelivered"):
            if any(
                target.operation.get("task_id") not in (None, "", self.request.id)
                for target in targets
            ):
                return {"status": "not_claimed"}
            error = "Source import was interrupted. Check worker memory and retry the upload."
            for target in targets:
                _fail(target.id, error)
            return {"status": "failed", "error": error}
        for target in targets:
            if (
                operations.started(
                    target.id, self.request.id or "", attachment_request=attachment_request
                )
                is None
            ):
                transaction.set_rollback(True)
                return {"status": "not_claimed"}
    appended = None
    user = User.objects.filter(pk=user_id).first() if user_id else None
    for target in targets:
        _emit(target.id, {"type": "land_started"})

    def progress(done: int) -> None:
        _emit(dataset_id, {"type": "land_progress", "traces": done})

    def file_progress(detail: dict) -> None:
        for target in targets:
            target.source_spec = {**target.source_spec, "landing_progress": detail}
            Dataset.objects.filter(pk=target.id, state=Dataset.State.LANDING).update(
                source_spec=target.source_spec, updated_at=timezone.now()
            )
            _emit(target.id, {"type": "land_progress", **detail})

    upload_id = source.get("upload_id")
    upload_ids = source.get("uploads") or []
    try:
        if upload_ids:
            read = landing.read_uploads(upload_ids, on_progress=file_progress)
        elif upload_id:
            filename = source.get("filename") or files.upload_filename(upload_id) or "upload"
            path = files.upload_data_path(upload_id)
            if not path.exists():
                raise landing.LandError("The upload has expired. Start it again.")
            read = landing.read_file(
                path,
                json_rows_field=source.get("json_rows_field"),
                filename=filename,
                on_progress=lambda detail: file_progress({"completed": 0, "total": 1, **detail}),
            )
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
        if upload_id or upload_ids:
            total = len(upload_ids) if upload_ids else 1
            file_progress(
                {"completed": total, "total": total, "stage": "publishing", "rows": len(read.rows)}
            )
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
                        state=Dataset.State.IDLE,
                        infer_capability=infer_capability,
                    )
        elif attachment_request:
            with transaction.atomic():
                dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
                if dataset.source_spec.get("attachment_request") != attachment_request:
                    return {"status": "landed"}
                if dataset.cells.exists():
                    appended = attachments.commit(dataset, read, user=user)
                else:
                    landing.commit(
                        dataset,
                        read,
                        user=user,
                        state=Dataset.State.IDLE,
                        infer_capability=False,
                    )
                targets = [dataset]
        else:
            landing.commit(
                dataset,
                read,
                user=user,
                state=Dataset.State.IDLE,
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
        for target in targets:
            operations.finished(target.id, task_id=self.request.id or "")
        if upload_id:
            files.discard_upload(upload_id)
        for uploaded_id in upload_ids:
            files.discard_upload(uploaded_id)

    rows = 0
    for target in targets:
        target.refresh_from_db()
        source_cell = target.source
        landed = (
            appended.review["added_rows"] if appended else source_cell.rows if source_cell else 0
        )
        rows += landed
        _emit(target.id, {"type": "land_done", "rows": landed})
        if target.operation.get("state") != "cancelled":
            Dataset.objects.filter(pk=target.pk).update(state=Dataset.State.IDLE)
    return {"status": "ok", "rows": rows}


@shared_task(name="overbae.tasks.datasets.reconcile_imports")
def reconcile_imports():
    from overbae.services.datasets import imports

    return imports.reconcile()


@shared_task(name="overbae.tasks.datasets.reap_stuck_runs")
def reap_stuck_runs() -> dict[str, Any]:
    """A killed worker never marks its dataset terminal; anything busy for
    longer than the hard limit is dead."""
    from datetime import timedelta

    from overbae.models import Dataset, DatasetPipelineRun
    from overbae.services.datasets import imports

    imports.reconcile()

    now = timezone.now()
    from overbae.services.datasets.workbench import expire_runs

    expire_runs()
    active_workflows = DatasetPipelineRun.objects.filter(state__in=["queued", "running"]).values(
        "dataset_id"
    )
    ids: list[Any] = []
    for state, limit in ((Dataset.State.LANDING, LAND_HARD_LIMIT),):
        stuck = (
            Dataset.objects.filter(
                state=state, updated_at__lt=now - timedelta(seconds=limit + REAP_GRACE)
            )
            .exclude(pk__in=active_workflows)
            .filter(import_run__isnull=True, split_imports__isnull=True)
        )
        with transaction.atomic():
            found = list(
                stuck.select_for_update(skip_locked=True, of=("self",)).values_list("id", flat=True)
            )
            Dataset.objects.filter(pk__in=found, state=state).update(
                state=Dataset.State.ERROR,
                error="The worker stopped before this finished.",
                updated_at=now,
            )
        ids += found
    for dataset_id in ids:
        _emit(
            dataset_id, {"type": "run_failed", "error": "The worker stopped before this finished."}
        )
    return {"reaped": len(ids)}


@shared_task(name="overbae.tasks.datasets.execute_pipeline", soft_time_limit=1500, time_limit=1560)
def execute_pipeline(run_id):
    from overbae.services.datasets.workbench import execute

    execute(run_id)
