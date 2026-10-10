"""Durable source bindings, broker publication and fenced import attempts."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta

from celery import current_app
from django.conf import settings
from django.db import transaction
from django.db.models import DateTimeField, ExpressionWrapper, F, Q, Value
from django.db.models.functions import Least
from django.utils import timezone

from overbae.models import Dataset, DatasetImport, User
from overbae.services.datasets import events, files, heartbeat, llm_calls, operations, paths
from overbae.services.datasets import land as landing
from overbae.services.datasets.lifecycle import DatasetError

logger = logging.getLogger(__name__)

LANDING_RECEIPT_VERSION = 1
EXECUTION_SECONDS = 60 * 60
LEASE_GRACE_SECONDS = 5 * 60
LEASE_SECONDS = 5 * 60
MAX_ATTEMPTS = 3
MAX_PUBLICATIONS = 8
RECONCILE_BATCH = 40
PUBLISH_LEASE_SECONDS = 30


@dataclass(frozen=True)
class ImportClaim:
    run_id: uuid.UUID
    owner: uuid.UUID


def _target_ids(run):
    ids = [run.dataset_id]
    evaluation_id = (run.inputs.get("split") or {}).get("eval_dataset_id")
    if evaluation_id:
        ids.append(uuid.UUID(str(evaluation_id)))
    return ids


def _uploads(source):
    return list(source.get("uploads") or []) + (
        [source["upload_id"]] if source.get("upload_id") else []
    )


def _source_inputs(inputs):
    inputs = {**inputs, "source": dict(inputs["source"])}
    source = inputs["source"]
    if source.get("rows") is not None:
        upload_id, _filename = files.begin_upload("pasted.jsonl")
        with files.upload_data_path(upload_id).open("w", encoding="utf-8") as output:
            for row in source["rows"]:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
        inputs["source"] = {"uploads": [upload_id], "pasted": True}
    manifest = []
    for value in _uploads(inputs["source"]):
        upload_id = str(uuid.UUID(str(value)))
        path = files.upload_data_path(upload_id)
        filename = files.upload_filename(upload_id)
        if not filename or not path.is_file():
            raise DatasetError(
                "The source upload is missing. Upload it again.", code="source_missing"
            )
        if not inputs["source"].get("pasted") and files.inspection(upload_id) is None:
            raise DatasetError(
                f"Inspect upload {upload_id} with POST /api/uploads/{upload_id}/inspect/ "
                "before creating a dataset from it.",
                code="upload_not_inspected",
            )
        stat = path.stat()
        manifest.append(
            {
                "upload_id": upload_id,
                "filename": filename,
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return inputs, manifest


@transaction.atomic
def queue_landing_receipt(dataset, task_id, inputs, *, published=False, preserve_error=False):
    """Bind a known message without publishing it; also used by bounded recovery."""
    task_id = uuid.UUID(str(task_id))
    existing = DatasetImport.objects.filter(dataset=dataset).first()
    if existing is not None:
        if existing.pk != task_id or existing.inputs != inputs:
            raise DatasetError(
                "Another source import already owns this dataset.", code="ownership_lost"
            )
        return existing
    locked = Dataset.objects.select_for_update().get(pk=dataset.pk)
    # A second delivery may have created the receipt while this row lock waited.
    existing = DatasetImport.objects.filter(dataset=locked).first()
    if existing is not None:
        if existing.pk != task_id or existing.inputs != inputs:
            raise DatasetError(
                "Another source import already owns this dataset.", code="ownership_lost"
            )
        return existing
    if str(inputs.get("dataset_id")) != str(locked.pk) or locked.cells.exists():
        raise DatasetError("The import does not match an empty dataset.", code="source_mismatch")
    if locked.state not in (Dataset.State.LANDING, Dataset.State.ERROR):
        raise DatasetError("The dataset is not waiting for a source.", code="source_mismatch")
    evaluation = None
    evaluation_id = (inputs.get("split") or {}).get("eval_dataset_id")
    if evaluation_id:
        evaluation = Dataset.objects.select_for_update().get(
            pk=evaluation_id, project_id=locked.project_id
        )
        if evaluation.cells.exists() or evaluation.state not in (
            Dataset.State.LANDING,
            Dataset.State.ERROR,
        ):
            raise DatasetError("The split target is no longer empty.", code="source_mismatch")
    was_error = locked.state == Dataset.State.ERROR or (
        evaluation is not None and evaluation.state == Dataset.State.ERROR
    )
    inputs, manifest = _source_inputs(inputs)
    now = timezone.now()
    run = DatasetImport.objects.create(
        id=task_id,
        dataset=locked,
        evaluation=evaluation,
        inputs=inputs,
        source_manifest=manifest,
        queued_at=now,
        published_at=now if published else None,
        next_publish_at=None if published else now,
    )
    Dataset.objects.filter(pk__in=_target_ids(run)).update(
        state=Dataset.State.LANDING, error="", updated_at=now
    )
    if preserve_error and was_error:
        _block(
            run,
            "legacy_interrupted",
            "The source import stopped before publication. Retry the import.",
        )
    return run


def enqueue(dataset, inputs):
    run = queue_landing_receipt(dataset, uuid.uuid4(), inputs)
    transaction.on_commit(lambda: publish(run.pk))
    return run


def publish(run_id):
    # The task imports this service, so task imports stay at dispatch boundaries.
    from overbae.tasks.datasets import land

    with transaction.atomic():
        run = DatasetImport.objects.select_for_update().get(pk=run_id)
        now = timezone.now()
        if run.state != DatasetImport.State.QUEUED or run.published_at is not None:
            return
        if run.next_publish_at and run.next_publish_at > now:
            return
        if run.publish_attempts >= MAX_PUBLICATIONS:
            _block(run, "dispatch_failed", "The source could not be queued. Retry the import.")
            return
        run.publish_attempts += 1
        owner = uuid.uuid4()
        run.publish_owner = owner
        run.next_publish_at = now + timedelta(
            seconds=max(PUBLISH_LEASE_SECONDS, min(2**run.publish_attempts, 60))
        )
        run.save(
            update_fields=["publish_attempts", "publish_owner", "next_publish_at", "updated_at"]
        )
        inputs = run.inputs
    try:
        with current_app.connection_for_write(
            connect_timeout=5,
            transport_options={"socket_connect_timeout": 5, "socket_timeout": 5},
        ) as connection:
            land.apply_async(kwargs=inputs, task_id=str(run_id), connection=connection, retry=False)
    except Exception:  # noqa: BLE001 — the durable row remains publishable
        logger.exception("could not publish dataset import %s", run_id)
        DatasetImport.objects.filter(
            pk=run_id, state=DatasetImport.State.QUEUED, publish_owner=owner
        ).update(
            failure_code="dispatch_failed",
            error="The broker did not acknowledge the import.",
            updated_at=timezone.now(),
        )
        return
    DatasetImport.objects.filter(
        pk=run_id, state=DatasetImport.State.QUEUED, publish_owner=owner
    ).update(
        published_at=timezone.now(),
        publish_owner=None,
        next_publish_at=None,
        failure_code="",
        error="",
        updated_at=timezone.now(),
    )


def _block(run, code, error):
    run.state = DatasetImport.State.BLOCKED
    run.failure_code = code
    run.error = error[:2000]
    run.owner = None
    run.lease_until = None
    run.save(update_fields=["state", "failure_code", "error", "owner", "lease_until", "updated_at"])
    Dataset.objects.filter(pk__in=_target_ids(run), state=Dataset.State.LANDING).update(
        state=Dataset.State.ERROR, error=run.error, updated_at=timezone.now()
    )
    for dataset_id in _target_ids(run):
        transaction.on_commit(
            lambda dataset_id=dataset_id: events.publish(
                dataset_id, {"dataset_id": str(dataset_id), "type": "land_failed", "error": error}
            )
        )


@transaction.atomic
def claim(run_id):
    run = DatasetImport.objects.select_for_update().get(pk=run_id)
    if run.state != DatasetImport.State.QUEUED:
        return None
    now = timezone.now()
    queue_seconds = getattr(settings, "DATASET_IMPORT_MAX_QUEUE_SECONDS", 15 * 60)
    if run.queued_at < now - timedelta(seconds=queue_seconds):
        _block(
            run,
            "queue_timeout",
            "The source waited too long for an import worker. Retry the import.",
        )
        return None
    if run.attempts >= MAX_ATTEMPTS:
        _block(run, "attempts_exhausted", "The import failed repeatedly. Its source is retained.")
        return None
    targets = list(
        Dataset.objects.select_for_update().filter(pk__in=_target_ids(run)).order_by("id")
    )
    if len(targets) != len(_target_ids(run)) or any(
        target.source is not None for target in targets
    ):
        _block(run, "source_mismatch", "The import targets changed before the source could land.")
        return None
    if any(target.state not in (Dataset.State.LANDING, Dataset.State.ERROR) for target in targets):
        _block(run, "ownership_lost", "Another operation changed this dataset.")
        return None
    run.state = DatasetImport.State.RUNNING
    run.owner = uuid.uuid4()
    run.started_at = now
    run.lease_until = now + timedelta(seconds=LEASE_SECONDS)
    run.attempts += 1
    run.failure_code = run.error = ""
    run.save(
        update_fields=[
            "state",
            "owner",
            "started_at",
            "lease_until",
            "attempts",
            "failure_code",
            "error",
            "updated_at",
        ]
    )
    Dataset.objects.filter(pk__in=_target_ids(run)).update(
        state=Dataset.State.LANDING, error="", updated_at=now
    )
    return ImportClaim(run.pk, run.owner)


def renew(claimed):
    """Progress never extends the absolute execution limit."""
    limit = ExpressionWrapper(
        F("started_at") + timedelta(seconds=EXECUTION_SECONDS + LEASE_GRACE_SECONDS),
        output_field=DateTimeField(),
    )
    lease = Value(timezone.now() + timedelta(seconds=LEASE_SECONDS), DateTimeField())
    DatasetImport.objects.filter(
        pk=claimed.run_id, owner=claimed.owner, state=DatasetImport.State.RUNNING
    ).update(lease_until=Least(lease, limit))


@contextmanager
def publication(claimed):
    with transaction.atomic():
        run = DatasetImport.objects.select_for_update().get(pk=claimed.run_id)
        if (
            run.state != DatasetImport.State.RUNNING
            or run.owner != claimed.owner
            or run.lease_until is None
            or run.lease_until <= timezone.now()
        ):
            raise DatasetError("Import ownership changed.", code="ownership_lost")
        targets = list(
            Dataset.objects.select_for_update().filter(pk__in=_target_ids(run)).order_by("id")
        )
        if len(targets) != len(_target_ids(run)) or any(
            target.state != Dataset.State.LANDING or target.source is not None for target in targets
        ):
            raise DatasetError("The import targets changed.", code="ownership_lost")
        yield run


@transaction.atomic
def fail(claimed, error, *, code="import_failed"):
    run = DatasetImport.objects.select_for_update().get(pk=claimed.run_id)
    if run.state == DatasetImport.State.RUNNING and run.owner == claimed.owner:
        _block(run, code, error)


@transaction.atomic
def resume(run_id):
    run = DatasetImport.objects.select_for_update().get(pk=run_id)
    if run.state != DatasetImport.State.BLOCKED:
        raise DatasetError("Only a stopped import can be retried.", code="import_busy")
    if run.attempts >= MAX_ATTEMPTS:
        raise DatasetError(
            "The import attempt limit was reached. Its source is retained.",
            code="attempts_exhausted",
        )
    return _requeue(run)


def resume_dataset(dataset):
    run = DatasetImport.objects.filter(
        Q(dataset=dataset) | Q(evaluation=dataset), state=DatasetImport.State.BLOCKED
    ).first()
    if run is None:
        raise DatasetError("No stopped source import is available.", code="import_not_found")
    resume(run.pk)
    dataset.refresh_from_db()
    return dataset


def status(dataset):
    run = DatasetImport.objects.filter(Q(dataset=dataset) | Q(evaluation=dataset)).first()
    if run is None:
        return None
    return {
        "id": str(run.pk),
        "state": run.state,
        "attempts": run.attempts,
        "queued_at": run.queued_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "failure_code": run.failure_code,
        "error": run.error,
        "can_resume": run.state == DatasetImport.State.BLOCKED and run.attempts < MAX_ATTEMPTS,
    }


def _requeue(run):
    """The caller holds the receipt's row lock."""
    targets = list(
        Dataset.objects.select_for_update().filter(pk__in=_target_ids(run)).order_by("id")
    )
    if len(targets) != len(_target_ids(run)) or any(
        target.source is not None
        or target.state not in (Dataset.State.LANDING, Dataset.State.ERROR)
        for target in targets
    ):
        raise DatasetError("The import targets changed before recovery.", code="ownership_lost")
    validate_sources(run)
    now = timezone.now()
    run.state = DatasetImport.State.QUEUED
    run.queued_at = now
    run.started_at = run.published_at = run.owner = run.lease_until = None
    run.next_publish_at = now
    run.publish_attempts = 0
    run.publish_owner = None
    run.failure_code = run.error = ""
    run.save()
    Dataset.objects.filter(pk__in=_target_ids(run)).update(
        state=Dataset.State.LANDING, error="", updated_at=now
    )
    transaction.on_commit(lambda: publish(run.pk))
    return run


def validate_sources(run):
    for source in run.source_manifest:
        path = files.upload_data_path(source["upload_id"])
        if not path.is_file():
            raise DatasetError(
                "The saved upload is missing. Upload it again.", code="source_missing"
            )
        stat = path.stat()
        if stat.st_size != source["bytes"] or stat.st_mtime_ns != source["mtime_ns"]:
            raise DatasetError(
                "The saved upload changed after it was queued.", code="source_changed"
            )


def retained_uploads():
    retained = set()
    for manifest in (
        DatasetImport.objects.exclude(state=DatasetImport.State.COMPLETE)
        .values_list("source_manifest", flat=True)
        .iterator()
    ):
        retained.update(source["upload_id"] for source in manifest)
    return retained


def _release_sources(run):
    for source in run.source_manifest:
        retained = (
            DatasetImport.objects.exclude(state=DatasetImport.State.COMPLETE)
            .filter(
                source_manifest__contains=[{"upload_id": source["upload_id"]}],
            )
            .exists()
        )
        if not retained:
            files.discard_upload(source["upload_id"])


def reconcile():
    now = timezone.now()
    queue_seconds = getattr(settings, "DATASET_IMPORT_MAX_QUEUE_SECONDS", 15 * 60)
    candidates = (
        DatasetImport.objects.filter(
            Q(
                state=DatasetImport.State.QUEUED,
                queued_at__lt=now - timedelta(seconds=queue_seconds),
            )
            | Q(
                state=DatasetImport.State.QUEUED,
                published_at__isnull=True,
                next_publish_at__lte=now,
            )
            | Q(state=DatasetImport.State.RUNNING, lease_until__lt=now)
        )
        .order_by("queued_at")
        .values_list("pk", flat=True)[:RECONCILE_BATCH]
    )
    processed = 0
    for run_id in list(candidates):
        with transaction.atomic():
            run = (
                DatasetImport.objects.select_for_update(skip_locked=True).filter(pk=run_id).first()
            )
            if run is None:
                continue
            if (
                run.state == DatasetImport.State.RUNNING
                and run.lease_until
                and run.lease_until < now
            ):
                if run.attempts >= MAX_ATTEMPTS:
                    _block(
                        run,
                        "worker_timeout",
                        "The import worker stopped before publication. Its source is retained.",
                    )
                else:
                    try:
                        _requeue(run)
                    except DatasetError as exc:
                        _block(run, exc.code, exc.detail)
            elif run.state == DatasetImport.State.QUEUED and run.queued_at < now - timedelta(
                seconds=queue_seconds
            ):
                _block(
                    run,
                    "queue_timeout",
                    "The source waited too long for an import worker. Retry the import.",
                )
            elif run.state == DatasetImport.State.QUEUED:
                transaction.on_commit(lambda run_id=run_id: publish(run_id))
        processed += 1
    return {"processed": processed}


def _read_parts(run, targets):
    source = run.inputs["source"]
    split = run.inputs.get("split")

    def progress(detail):
        for target in targets:
            Dataset.objects.filter(pk=target.pk, state=Dataset.State.LANDING).update(
                source_spec={**target.source_spec, "landing_progress": detail},
                updated_at=timezone.now(),
            )
            events.publish(
                target.pk, {"type": "land_progress", "dataset_id": str(target.pk), **detail}
            )

    if source.get("uploads"):
        read = landing.read_uploads(source["uploads"], on_progress=progress)
        if source.get("pasted"):
            read = replace(read, spec={"pasted": True})
    elif source.get("upload_id"):
        read = landing.read_file(
            files.upload_data_path(source["upload_id"]),
            filename=source.get("filename") or files.upload_filename(source["upload_id"]),
            json_rows_field=source.get("json_rows_field"),
            on_progress=progress,
        )
    elif source.get("traces") is not None:
        read = landing.read_traces(
            run.dataset.project_id,
            source["traces"],
            on_progress=lambda done: events.publish(
                run.dataset_id,
                {
                    "dataset_id": str(run.dataset_id),
                    "type": "land_progress",
                    "traces": done,
                },
            ),
        )
    elif source.get("llm_calls") is not None:
        read = llm_calls.read(
            run.dataset.project_id,
            source["llm_calls"],
            intents=("train", "eval") if split else (run.dataset.intent,),
        )
        if split:
            train, evaluation = llm_calls.hash_split(read.rows, int(split["eval_percent"]))
            parts = []
            for target, rows, sibling in (
                (targets[0], train, targets[1]),
                (targets[1], evaluation, targets[0]),
            ):
                spec = {
                    **read.spec,
                    "split": {
                        "eval_percent": int(split["eval_percent"]),
                        "position": llm_calls.HASH_POSITION,
                        "role": target.intent,
                        "sibling": str(sibling.pk),
                    },
                }
                parts.append(
                    (
                        target,
                        landing.Landing(
                            llm_calls.shape(rows, target.intent),
                            kind=Dataset.SourceKind.LLM_CALLS,
                            spec=spec,
                            manifest=llm_calls.manifest_for(target.intent),
                        ),
                    )
                )
            return parts, Dataset.State.IDLE
        return [
            (
                targets[0],
                landing.Landing(
                    llm_calls.shape(read.rows, targets[0].intent),
                    kind=Dataset.SourceKind.LLM_CALLS,
                    spec=read.spec,
                    manifest=llm_calls.manifest_for(targets[0].intent),
                ),
            )
        ], Dataset.State.IDLE
    else:
        raise landing.LandError("No saved source is available.")
    if not split:
        return [(targets[0], read)], Dataset.State.IDLE
    cut = {
        "eval_percent": int(split["eval_percent"]),
        "position": split["position"],
        "group_by": split.get("group_by", []),
        "stratify_by": split.get("stratify_by"),
        "deduplicate": split.get("deduplicate", True),
    }
    train, evaluation = read.split(**cut)
    return [
        (
            target,
            replace(
                part, spec={**part.spec, "split": {**cut, "role": role, "sibling": str(sibling.pk)}}
            ),
        )
        for target, part, role, sibling in (
            (targets[0], train, "train", targets[1]),
            (targets[1], evaluation, "eval", targets[0]),
        )
    ], Dataset.State.IDLE


def execute(task_id, inputs):
    dataset = Dataset.objects.filter(pk=inputs["dataset_id"]).first()
    if dataset is None:
        return {"status": "gone"}
    run = DatasetImport.objects.select_related("dataset").filter(dataset=dataset).first()
    if run is None:
        if dataset.source is not None:
            return {"status": "landed"}
        run = queue_landing_receipt(
            dataset, task_id or uuid.uuid4(), inputs, published=True, preserve_error=True
        )
    elif task_id and str(run.pk) != str(task_id):
        return {"status": "superseded"}
    if dataset.operation.get("state") == "cancelled":
        DatasetImport.objects.filter(pk=run.pk).update(state=DatasetImport.State.CANCELLED)
        return {"status": "cancelled"}
    claimed = claim(run.pk)
    if claimed is None:
        run.refresh_from_db()
        return {"status": run.state}
    stage = paths.dataset_dir(dataset.pk) / "imports" / str(run.pk) / str(claimed.owner)
    beat = heartbeat.start(lambda: renew(claimed))
    try:
        run.refresh_from_db()
        validate_sources(run)
        targets = [Dataset.objects.get(pk=pk) for pk in _target_ids(run)]
        user = (
            User.objects.filter(pk=run.inputs.get("user_id")).first()
            if run.inputs.get("user_id")
            else None
        )
        for target in targets:
            Dataset.objects.filter(pk=target.pk).update(
                operation={
                    "task_id": str(run.pk),
                    "state": "running",
                    "started_at": run.started_at.isoformat(),
                }
            )
            events.publish(target.pk, {"type": "land_started", "dataset_id": str(target.pk)})
        parts, state = _read_parts(run, targets)
        prepared = [
            (
                target,
                landing.prepare(
                    target,
                    part,
                    path=stage / f"{target.pk}.parquet",
                    state=state,
                    infer_capability=run.inputs.get("infer_capability", True),
                ),
            )
            for target, part in parts
        ]
        validate_sources(run)
        if run.inputs["source"].get("llm_calls") is not None:
            for target, result in prepared:
                report = result.cell_fields["intent_report"].get(target.intent, {})
                if not report.get("ok") or not result.cell_fields["rows"]:
                    raise landing.LandError(
                        report.get("reason") or "The source does not fit its intent."
                    )
        with publication(claimed) as locked:
            rows = 0
            for target, result in prepared:
                landing.publish(target, result, user=user)
                rows += result.cell_fields["rows"]
            locked.state = DatasetImport.State.COMPLETE
            locked.result = {"rows": rows}
            locked.owner = locked.lease_until = None
            locked.save(
                update_fields=[
                    "state",
                    "result",
                    "owner",
                    "lease_until",
                    "updated_at",
                ]
            )
        for target, result in prepared:
            events.publish(
                target.pk,
                {
                    "type": "land_done",
                    "dataset_id": str(target.pk),
                    "rows": result.cell_fields["rows"],
                },
            )
        run.refresh_from_db()
        try:
            _release_sources(run)
        except OSError:
            logger.exception("could not clean up completed dataset import %s", run.pk)
        return {"status": "ok", "rows": rows}
    except Exception as exc:  # noqa: BLE001 — a failed attempt retains its exact source
        logger.exception("dataset import %s failed", run.pk)
        fail(claimed, str(exc), code=getattr(exc, "code", "import_failed"))
        return {"status": "failed", "error": str(exc)}
    finally:
        beat.set()
        for target_id in _target_ids(run):
            operations.finished(target_id, task_id=str(run.pk))
        shutil.rmtree(stage, ignore_errors=True)
