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
from django.db.models import Q
from django.utils import timezone

from overbae.models import Dataset, DatasetImport, User
from overbae.services.datasets import files, llm_calls, paths
from overbae.services.datasets import land as landing
from overbae.services.datasets.lifecycle import DatasetError, claim_workshop, queue_workshop
from overbae.services.datasets.notebook import events

logger = logging.getLogger(__name__)

LANDING_RECEIPT_VERSION = 1
EXECUTION_SECONDS = 60 * 60
LEASE_GRACE_SECONDS = 5 * 60
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
    run.lease_until = now + timedelta(seconds=EXECUTION_SECONDS + LEASE_GRACE_SECONDS)
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


def publish_handoffs(run_id):
    # The task imports this service, so task imports stay at dispatch boundaries.
    from overbae.tasks.datasets import diagnose

    with transaction.atomic():
        run = DatasetImport.objects.select_for_update().get(pk=run_id)
        now = timezone.now()
        if run.state != DatasetImport.State.COMPLETE or not run.handoff_pending:
            return
        if run.handoff_lease_until and run.handoff_lease_until > now:
            return
        published = set(run.result.get("diagnose_published", []))
        retired = set(run.result.get("diagnose_retired", []))
        pending = [
            target for target in run.result.get("diagnose", []) if target not in published | retired
        ]
        if run.handoff_attempts >= MAX_PUBLICATIONS:
            unstarted = set(pending) - set(run.result.get("diagnose_claimed", []))
            Dataset.objects.filter(pk__in=unstarted, state=Dataset.State.DIAGNOSING).update(
                state=Dataset.State.ERROR,
                error="The source landed, but the agent could not be queued. Send a message to retry.",
                updated_at=now,
            )
            run.handoff_pending = False
            run.save(update_fields=["handoff_pending", "updated_at"])
            return
        owner = uuid.uuid4()
        run.handoff_owner = owner
        run.handoff_lease_until = now + timedelta(seconds=PUBLISH_LEASE_SECONDS)
        run.handoff_attempts += 1
        run.save(
            update_fields=["handoff_owner", "handoff_lease_until", "handoff_attempts", "updated_at"]
        )
    for dataset_id in pending:
        task_id = str(uuid.uuid5(run.pk, f"diagnose:{dataset_id}"))
        try:
            with current_app.connection_for_write(
                connect_timeout=5,
                transport_options={"socket_connect_timeout": 5, "socket_timeout": 5},
            ) as connection:
                diagnose.apply_async(
                    kwargs={"dataset_id": dataset_id, "user_id": run.inputs.get("user_id")},
                    task_id=task_id,
                    connection=connection,
                    retry=False,
                )
        except Exception:  # noqa: BLE001 — the completed import retains its handoff
            logger.exception("could not publish diagnosis for dataset import %s", run_id)
            return
        with transaction.atomic():
            locked = DatasetImport.objects.select_for_update().get(pk=run_id)
            if locked.handoff_owner != owner:
                return
            acknowledged = list(locked.result.get("diagnose_published", []))
            if dataset_id not in acknowledged:
                acknowledged.append(dataset_id)
            locked.result = {**locked.result, "diagnose_published": acknowledged}
            locked.save(update_fields=["result", "updated_at"])
    DatasetImport.objects.filter(pk=run_id, handoff_owner=owner).update(
        handoff_pending=False,
        handoff_owner=None,
        handoff_lease_until=None,
        updated_at=timezone.now(),
    )


@contextmanager
def explicit_operation(dataset_id):
    with transaction.atomic():
        receipt = (
            DatasetImport.objects.select_for_update()
            .filter(Q(dataset_id=dataset_id) | Q(evaluation_id=dataset_id))
            .first()
        )
        dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
        previous_state = dataset.state
        yield dataset
        if (
            receipt is None
            or receipt.state != DatasetImport.State.COMPLETE
            or previous_state not in (Dataset.State.IDLE, Dataset.State.ERROR)
            or dataset.state not in (Dataset.State.DIAGNOSING, Dataset.State.RUNNING)
            or dataset.source is None
        ):
            return
        target = str(dataset_id)
        retired = list(receipt.result.get("diagnose_retired", []))
        if (
            target in receipt.result.get("diagnose", [])
            and target not in receipt.result.get("diagnose_claimed", [])
            and target not in retired
        ):
            retired.append(target)
            receipt.result = {**receipt.result, "diagnose_retired": retired}
            receipt.save(update_fields=["result", "updated_at"])


@transaction.atomic
def claim_diagnosis(dataset_id, task_id):
    run = (
        DatasetImport.objects.select_for_update()
        .filter(Q(dataset_id=dataset_id) | Q(evaluation_id=dataset_id))
        .first()
    )
    if run is None:
        return claim_workshop(dataset_id, task_id, state=Dataset.State.DIAGNOSING)
    target = str(dataset_id)
    if (
        run.state != DatasetImport.State.COMPLETE
        or target not in run.result.get("diagnose", [])
        or target in run.result.get("diagnose_retired", [])
        or str(task_id) != str(uuid.uuid5(run.pk, f"diagnose:{target}"))
    ):
        return False
    claimed = list(run.result.get("diagnose_claimed", []))
    if target in claimed:
        return False
    dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
    if dataset.state != Dataset.State.DIAGNOSING or dataset.source is None:
        return False
    if not claim_workshop(dataset_id, task_id, state=Dataset.State.DIAGNOSING):
        return False
    claimed.append(target)
    run.result = {**run.result, "diagnose_claimed": claimed}
    run.save(update_fields=["result", "updated_at"])
    return True


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
            | (
                Q(state=DatasetImport.State.COMPLETE, handoff_pending=True)
                & (Q(handoff_lease_until__isnull=True) | Q(handoff_lease_until__lte=now))
            )
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
                _block(
                    run,
                    "worker_timeout",
                    "The import worker stopped before publication. Its source is retained.",
                )
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
            elif run.handoff_pending:
                transaction.on_commit(lambda run_id=run_id: publish_handoffs(run_id))
        processed += 1
    return {"processed": processed}


def _read_parts(run, targets):
    source = run.inputs["source"]
    split = run.inputs.get("split")
    if source.get("uploads"):
        read = landing.read_uploads(source["uploads"])
        if source.get("pasted"):
            read = replace(read, spec={"pasted": True})
    elif source.get("upload_id"):
        read = landing.read_file(
            files.upload_data_path(source["upload_id"]),
            filename=source.get("filename") or files.upload_filename(source["upload_id"]),
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
        return [(targets[0], read)], Dataset.State.DIAGNOSING
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
    ], Dataset.State.DIAGNOSING


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
    claimed = claim(run.pk)
    if claimed is None:
        run.refresh_from_db()
        return {"status": run.state}
    stage = paths.dataset_dir(dataset.pk) / "imports" / str(run.pk) / str(claimed.owner)
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
        if state == Dataset.State.IDLE:
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
                if state == Dataset.State.DIAGNOSING:
                    queue_workshop(target.pk, str(uuid.uuid5(run.pk, f"diagnose:{target.pk}")))
                rows += result.cell_fields["rows"]
            locked.state = DatasetImport.State.COMPLETE
            locked.result = {
                "rows": rows,
                "diagnose": [str(target.pk) for target in targets]
                if state == Dataset.State.DIAGNOSING
                else [],
            }
            locked.handoff_pending = bool(locked.result["diagnose"])
            locked.owner = locked.lease_until = None
            locked.save(
                update_fields=[
                    "state",
                    "result",
                    "handoff_pending",
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
        publish_handoffs(run.pk)
        return {"status": "ok", "rows": rows}
    except Exception as exc:  # noqa: BLE001 — a failed attempt retains its exact source
        logger.exception("dataset import %s failed", run.pk)
        fail(claimed, str(exc), code=getattr(exc, "code", "import_failed"))
        return {"status": "failed", "error": str(exc)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)
