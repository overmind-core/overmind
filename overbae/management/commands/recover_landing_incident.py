"""Adopt and relocate only the verified 2026-10-06 Titanic import incident."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from overbae.celery import app
from overbae.models import Dataset, DatasetImport
from overbae.services.datasets import files, imports
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.landing_queue_transfer import assert_task_absent, move_exact_envelope

TARGET_DATASET = "8fd9cc11-831f-4090-a0d5-1f4029f762e0"
TARGET_PROJECT = "df764b4b-b269-475b-b0f8-5df6402ded2d"
TARGET_TASK = "47322726-fdb2-4447-96f3-73d27beae0e3"
TARGET_UPLOAD = "b80e24be-5642-4839-ae1a-2fb7c271040e"
SOURCE_SHA256 = "4a437fde05fe5264e1701a7387ac6fb75393772ba38bb2c9c566405af5af4bd7"
WATCHDOG_ERROR = "The worker stopped before this finished."
PRESERVED_ERROR = "The source import stopped before publication. Retry the import."
MAX_SCAN = 100_000
ORIGINAL_INPUTS = {
    "dataset_id": TARGET_DATASET,
    "source": {"uploads": [TARGET_UPLOAD]},
    "user_id": "3",
    "infer_capability": False,
}


def validate_dataset(dataset, *, expected_error=WATCHDOG_ERROR):
    if str(dataset.pk) != TARGET_DATASET or str(dataset.project_id) != TARGET_PROJECT:
        raise CommandError("The dataset/project identity differs from the verified incident")
    if dataset.state == Dataset.State.ERROR:
        if dataset.error != expected_error:
            raise CommandError("Only the exact reviewed import error may be repaired")
    elif dataset.state != Dataset.State.LANDING or dataset.error:
        raise CommandError("The dataset has entered another operation")
    if (
        dataset.active_id
        or dataset.cells.exists()
        or dataset.chat
        or dataset.agent_id
        or dataset.agent_messages
        or dataset.agent_turn_key
    ):
        raise CommandError("Dataset content or agent activity changed; refuse automatic repair")


def decode_target_envelope(raw):
    try:
        message = json.loads(raw)
        if (
            message["headers"]["id"] != TARGET_TASK
            or message["headers"]["task"] != "overbae.tasks.datasets.land"
        ):
            raise ValueError("task identity")
        if message["properties"]["body_encoding"] != "base64":
            raise ValueError("body encoding")
        args, inputs, _embed = json.loads(base64.b64decode(message["body"], validate=True))
        source = inputs["source"]
        upload_ids = list(source.get("uploads") or []) + (
            [source["upload_id"]] if source.get("upload_id") else []
        )
        if args or str(inputs.get("dataset_id")) != TARGET_DATASET or upload_ids != [TARGET_UPLOAD]:
            raise ValueError("source identity")
        if inputs.get("split") or set(source) - {"uploads", "upload_id", "filename"}:
            raise ValueError("unexpected source or split")
        if inputs != ORIGINAL_INPUTS:
            raise ValueError("original user or capability inference choice changed")
        return inputs
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise CommandError("The queued envelope differs from the verified import") from exc


def verify_source():
    try:
        body = files.upload_data_path(TARGET_UPLOAD).read_bytes()
    except OSError as exc:
        raise CommandError("The verified source is missing or unreadable") from exc
    if len(body) != 60_302 or hashlib.sha256(body).hexdigest() != SOURCE_SHA256:
        raise CommandError("The source size or SHA-256 changed")
    if files.upload_filename(TARGET_UPLOAD) != "titanic.csv":
        raise CommandError("The source filename changed")
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    if len(rows) != 892 or any(len(row) != 12 for row in rows):
        raise CommandError("Expected the verified 891-row, 12-column CSV")


def original_envelope(client, keys, *, allow_missing=False):
    if sum(client.llen(key) for key in keys) > MAX_SCAN:
        raise CommandError("Queue scan bound exceeded; inspect the broker before recovery")
    candidates = []
    for key in keys:
        for raw in client.lrange(key, 0, -1):
            message = json.loads(raw)
            if message.get("headers", {}).get("id") == TARGET_TASK:
                candidates.append(raw)
    if not candidates and allow_missing:
        return None
    if len(candidates) != 1:
        raise CommandError("Expected exactly one original task still queued in batch")
    decode_target_envelope(candidates[0])
    return candidates[0]


def require_landing_consumer():
    inspection = app.control.inspect(timeout=5)
    queues = inspection.active_queues() or {}
    if not any(
        name.startswith("landing@") and {q["name"] for q in owned} == {"landing"}
        for name, owned in queues.items()
    ):
        raise CommandError("No dedicated landing consumer replied; deploy consumers first")


def confirmed_noop(meta):
    return meta.get("status") == "SUCCESS" and meta.get("result") == {"status": "landed"}


def require_worker_absence():
    inspection = app.control.inspect(timeout=5)
    consumers = inspection.active_queues() or {}
    active = inspection.active() or {}
    reserved = inspection.reserved() or {}
    scheduled = inspection.scheduled() or {}
    if not consumers or any(set(consumers) - set(reply) for reply in (active, reserved, scheduled)):
        raise CommandError("A worker did not reply to absence checks; outcome is unknown")
    for reply in (active, reserved, scheduled):
        for tasks in reply.values():
            if any(str((task.get("request") or task).get("id")) == TARGET_TASK for task in tasks):
                raise CommandError("The original task is still owned by a worker")


def require_confirmed_noop(client, channel):
    if not confirmed_noop(app.backend.get_task_meta(TARGET_TASK)):
        raise CommandError("Only exact terminal SUCCESS/{status: landed} permits a no-op retry")
    require_worker_absence()
    queues = {settings.CELERY_TASK_DEFAULT_QUEUE} | {
        route["queue"]
        for route in settings.CELERY_TASK_ROUTES.values()
        if isinstance(route, dict) and route.get("queue")
    }
    keys = [
        queue if priority == 0 else f"{queue}{channel.sep}{priority}"
        for queue in sorted(queues)
        for priority in channel.priority_steps
    ]
    assert_task_absent(
        client, queue_keys=keys, unacked_key=channel.unacked_key, task_id=TARGET_TASK
    )


def retry_confirmed_noop(client, channel, *, apply):
    require_confirmed_noop(client, channel)
    with transaction.atomic():
        existing = (
            DatasetImport.objects.select_for_update().filter(dataset_id=TARGET_DATASET).first()
        )
        dataset = Dataset.objects.select_for_update().get(pk=TARGET_DATASET)
        validate_dataset(dataset)
        verify_source()
        if dataset.state != Dataset.State.ERROR or dataset.error != WATCHDOG_ERROR:
            raise CommandError("No-op retry requires the original unrepaired watchdog error")
        if existing is not None or DatasetImport.objects.filter(dataset_id=TARGET_DATASET).exists():
            raise CommandError(
                "A durable import already owns recovery; inspect it instead of republishing"
            )
        if not confirmed_noop(app.backend.get_task_meta(TARGET_TASK)):
            raise CommandError("The terminal result changed during inspection")
        if apply:
            # The publisher retries delivery failures through this one durable
            # outbox. A stable task ID plus per-attempt owner fences execution.
            imports.queue_landing_receipt(dataset, TARGET_TASK, ORIGINAL_INPUTS, published=False)
    if apply:
        imports.publish(TARGET_TASK)
    return {
        "task_id": TARGET_TASK,
        "applied": apply,
        "completed": False,
        "action": "retry exact acknowledged no-op through durable importer",
        "infer_capability": False,
        "next": "verify durable import and source cell",
    }


def validate_preserved_import(run, dataset):
    validate_dataset(dataset, expected_error=PRESERVED_ERROR)
    if (
        str(run.pk) != TARGET_TASK
        or str(run.dataset_id) != TARGET_DATASET
        or run.evaluation_id is not None
        or run.inputs != ORIGINAL_INPUTS
    ):
        raise CommandError("The preserved receipt differs from the verified task or choices")
    if (
        dataset.state != Dataset.State.ERROR
        or run.state != DatasetImport.State.BLOCKED
        or run.failure_code != "legacy_interrupted"
        or run.error != PRESERVED_ERROR
    ):
        raise CommandError("Only the exact preserved legacy import may be retried")
    if (
        run.owner is not None
        or run.lease_until is not None
        or run.started_at is not None
        or run.attempts
        or run.publish_owner is not None
        or run.publish_attempts
        or run.published_at is None
        or run.next_publish_at is not None
        or run.handoff_pending
        or run.handoff_owner is not None
        or run.handoff_lease_until is not None
        or run.handoff_attempts
        or run.result
    ):
        raise CommandError("An import attempt or publication already owns this receipt")
    manifest = run.source_manifest
    if not isinstance(manifest, list) or len(manifest) != 1 or not isinstance(manifest[0], dict):
        raise CommandError("The retained source manifest changed")
    source = manifest[0]
    if (
        set(source) != {"upload_id", "filename", "bytes", "mtime_ns"}
        or source["upload_id"] != TARGET_UPLOAD
        or source["filename"] != "titanic.csv"
        or source["bytes"] != 60_302
        or not isinstance(source["mtime_ns"], int)
        or source["mtime_ns"] <= 0
    ):
        raise CommandError("The retained source manifest changed")


def retry_preserved_import(*, apply):
    with transaction.atomic():
        # Match the importer's lock order. Concurrent commands recheck BLOCKED
        # after waiting; only one can register the on-commit durable publication.
        run = DatasetImport.objects.select_for_update().filter(pk=TARGET_TASK).first()
        if run is None:
            raise CommandError("The original task has no preserved durable import")
        dataset = Dataset.objects.select_for_update().get(pk=TARGET_DATASET)
        validate_preserved_import(run, dataset)
        verify_source()
        try:
            imports.validate_sources(run)
            if apply:
                imports.resume(run.pk)
        except DatasetError as exc:
            raise CommandError(str(exc)) from exc
    return {
        "task_id": TARGET_TASK,
        "applied": apply,
        "completed": False,
        "action": "resume exact preserved legacy import through durable importer",
        "infer_capability": False,
        "next": "verify durable import and source cell",
    }


class Command(BaseCommand):
    help = (
        "Review the single verified Titanic import; --apply adopts its durable receipt and "
        "relocates the original queued envelope. Run only after the complete reviewed release "
        "(all workers and watchdog/beat) is deployed. No other dataset is accepted."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        recovery_mode = parser.add_mutually_exclusive_group()
        recovery_mode.add_argument(
            "--retry-acknowledged-noop",
            action="store_true",
            help=(
                "Only if the original was acknowledged as the exact legacy no-op; refuses "
                "PENDING/unknown, any queued/owned copy, changed source or an existing receipt."
            ),
        )

        recovery_mode.add_argument(
            "--retry-preserved-import",
            action="store_true",
            help=(
                "Only after the new worker retained the exact blocked legacy_interrupted "
                "receipt; validates ownership, original choices and source before resuming."
            ),
        )

    def handle(self, *args, **options):
        if imports.LANDING_RECEIPT_VERSION != 1:
            raise CommandError("The reviewed durable import/watchdog guard is not installed")
        require_landing_consumer()
        if options["retry_preserved_import"]:
            result = retry_preserved_import(apply=options["apply"])
            self.stdout.write(json.dumps(result))
            return
        with app.connection_for_read() as connection, connection.channel() as channel:
            if not hasattr(channel, "priority_steps") or not hasattr(channel, "unacked_key"):
                raise CommandError("Recovery supports the reviewed Redis transport only")
            # Kombu's prefix wrapper does not prefix LRANGE or EVAL keys.
            # Refuse a different broker layout rather than inspect another namespace.
            if getattr(channel, "global_keyprefix", ""):
                raise CommandError("Recovery requires the reviewed unprefixed Redis layout")
            # The channel client retains configured TLS/auth handling.
            client = channel.client
            source_keys = [
                "batch" if priority == 0 else f"batch{channel.sep}{priority}"
                for priority in channel.priority_steps
            ]
            destination_keys = [
                "landing" if priority == 0 else f"landing{channel.sep}{priority}"
                for priority in channel.priority_steps
            ]
            raw = original_envelope(
                client, source_keys, allow_missing=options["retry_acknowledged_noop"]
            )
            if raw is None:
                result = retry_confirmed_noop(client, channel, apply=options["apply"])
                self.stdout.write(json.dumps(result))
                return
            if options["retry_acknowledged_noop"]:
                raise CommandError("The original is still queued; use exact relocation instead")
            inputs = decode_target_envelope(raw)
            validate_dataset(Dataset.objects.get(pk=TARGET_DATASET))
            verify_source()
            if not options["apply"]:
                self.stdout.write(
                    json.dumps(
                        {
                            "dataset_id": TARGET_DATASET,
                            "task_id": TARGET_TASK,
                            "upload_id": TARGET_UPLOAD,
                            "source_sha256": SOURCE_SHA256,
                            "action": "adopt receipt, then move one original envelope",
                            "applied": False,
                        }
                    )
                )
                return
            # Commit receipt adoption before touching Redis. Existing receipts
            # are locked before Dataset, matching the worker's ownership order.
            with transaction.atomic():
                existing = (
                    DatasetImport.objects.select_for_update()
                    .filter(dataset_id=TARGET_DATASET)
                    .first()
                )
                dataset = Dataset.objects.select_for_update().get(pk=TARGET_DATASET)
                validate_dataset(dataset)
                verify_source()
                if (
                    existing is None
                    and DatasetImport.objects.filter(dataset_id=TARGET_DATASET).exists()
                ):
                    raise CommandError("A concurrent receipt appeared; retry inspection")
                if existing is not None and (
                    str(existing.pk) != TARGET_TASK
                    or existing.inputs != inputs
                    or existing.state != "queued"
                    or existing.owner
                ):
                    raise CommandError("An existing receipt has another identity or attempt")
                receipt = imports.queue_landing_receipt(
                    dataset, TARGET_TASK, inputs, published=True
                )
                receipt.published_at = receipt.published_at or timezone.now()
                receipt.next_publish_at = None
                receipt.save(update_fields=["published_at", "next_publish_at", "updated_at"])
            # This is a separate transaction. Failure retains the durable
            # queued receipt and does not ask the publisher to send a duplicate.
            with transaction.atomic():
                receipt = DatasetImport.objects.select_for_update().get(pk=TARGET_TASK)
                dataset = Dataset.objects.select_for_update().get(pk=TARGET_DATASET)
                validate_dataset(dataset)
                verify_source()
                if (
                    dataset.state != Dataset.State.LANDING
                    or receipt.state != "queued"
                    or receipt.owner
                    or receipt.inputs != inputs
                ):
                    raise CommandError("An import attempt started before relocation")
                require_landing_consumer()
                moved = move_exact_envelope(
                    client,
                    source_keys=source_keys,
                    destination_keys=destination_keys,
                    unacked_key=channel.unacked_key,
                    task_id=TARGET_TASK,
                    raw=raw,
                )
            self.stdout.write(
                json.dumps(
                    {
                        "task_id": TARGET_TASK,
                        "moved": bool(moved),
                        "completed": False,
                        "next": "verify source cell and durable import completion",
                    }
                )
            )
