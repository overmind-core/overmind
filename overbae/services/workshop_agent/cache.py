import asyncio
import gzip
import hashlib
import io
import json
import logging
import shlex
import zipfile
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from daytona import FileUpload
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection, connections, transaction
from django.utils import timezone

from overbae.models import WorkshopTask, WorkshopWorkspace
from overbae.services.workshop_agent import cache_files, workspace

logger = logging.getLogger(__name__)


def remote(sandbox, task, operation, **arguments):
    script = Path(cache_files.__file__).read_text()
    script += (
        "\nimport sys\nfrom workshop_runtime import mailbox, session\n"
        "r=json.loads(sys.argv[1]); print(json.dumps(dispatch(r.pop('root'), r, "
        "inspect_agent=mailbox.inspect, request_kernel=session.request)))"
    )
    request = {"root": workspace.task_root(task), "operation": operation, **arguments}
    response = sandbox.process.exec(
        "/opt/overmind-runtime/venv/bin/python -c "
        + shlex.quote(script)
        + " "
        + shlex.quote(json.dumps(request)),
        timeout=300,
    )
    if response.exit_code:
        raise ValueError(f"Workspace retention failed: {response.result[-1500:]}")
    return json.loads(response.result)


def eligible(task):
    if task.state not in {"completed", "cancelled", "blocked"}:
        return False
    if task.actions.filter(state__in=["intent", "unknown"]).exists():
        return False
    if task.state == "blocked":
        events = task.result.get("events", [])
        return bool(events and events[-1].get("tool") == "finish" and not events[-1].get("error"))
    return True


def verify_chunk(stream, chunk):
    checksum = hashlib.sha256()
    size = 0
    with gzip.GzipFile(fileobj=stream) as incoming:
        while block := incoming.read(min(1024**2, chunk["bytes"] - size + 1)):
            size += len(block)
            if size > chunk["bytes"]:
                raise ValueError("Retained chunk exceeds pinned size")
            checksum.update(block)
    if size != chunk["bytes"] or checksum.hexdigest() != chunk["sha256"]:
        raise ValueError("Retained chunk checksum mismatch")


def object_key(task, checksum):
    return f"workshop-retained/{task.project_id}/chunks/{checksum}.gz"


def verify_archive(task):
    receipt = task.retention
    with default_storage.open(receipt["manifest"], "rb") as stream:
        entries = json.load(stream)
    if cache_files.fingerprint(entries) != receipt["sha256"]:
        raise ValueError("Retained workspace manifest changed")
    cache_files.validate(entries, workspace.task_root(task))
    seen = set()
    for entry in entries:
        for chunk in entry.get("chunks", []):
            if chunk["sha256"] not in seen:
                with default_storage.open(object_key(task, chunk["sha256"]), "rb") as stream:
                    verify_chunk(stream, chunk)
                seen.add(chunk["sha256"])
    return entries


def retain(task, sandbox):
    if not eligible(task):
        return None
    prior = task.retention
    if prior.get("task_updated_at") == task.updated_at.isoformat():
        return prior
    snapshot = remote(sandbox, task, "inventory")
    if not snapshot["exists"]:
        if prior.get("manifest"):
            return prior
        raise ValueError("Unretained task files are absent; refusing cleanup")
    entries = snapshot["entries"]
    cache_files.validate(entries, workspace.task_root(task))
    logger.info(
        "Retaining workshop %s: %s entries, %s bytes",
        task.pk,
        len(entries),
        sum(entry.get("bytes", 0) for entry in entries),
    )
    pending, batch_bytes, seen = [], 0, set()

    def transfer():
        if not pending:
            return
        temporary = f"/tmp/workshop-cache-{uuid4()}.zip"
        try:
            exported = remote(sandbox, task, "export_archive", items=pending, temporary=temporary)
            downloaded = sandbox.fs.download_file(temporary)
            if hashlib.sha256(downloaded).hexdigest() != exported["sha256"]:
                raise ValueError("Workspace download checksum mismatch")
            with zipfile.ZipFile(io.BytesIO(downloaded)) as archive:
                if archive.namelist() != [str(i) for i in range(len(pending))]:
                    raise ValueError("Retention archive members differ")
                for index, item in enumerate(pending):
                    chunk = item["entry"]["chunks"][item["index"]]
                    compressed = archive.read(str(index))
                    verify_chunk(io.BytesIO(compressed), chunk)
                    key = object_key(task, chunk["sha256"])
                    if not default_storage.exists(key):
                        saved = default_storage.save(key, ContentFile(compressed))
                        if saved != key:
                            default_storage.delete(saved)
        finally:
            with suppress(Exception):
                sandbox.fs.delete_file(temporary)
        pending.clear()

    for entry in entries:
        for index, chunk in enumerate(entry.get("chunks", [])):
            if chunk["sha256"] in seen:
                continue
            seen.add(chunk["sha256"])
            if default_storage.exists(object_key(task, chunk["sha256"])):
                continue
            if pending and batch_bytes + chunk["bytes"] > cache_files.CHUNK_BYTES:
                transfer()
                batch_bytes = 0
            if WorkshopWorkspace.objects.filter(
                project_id=task.project_id, task_owner__isnull=False
            ).exists():
                return None
            pending.append({"entry": entry, "index": index})
            batch_bytes += chunk["bytes"]
    transfer()
    verified = set()
    for entry in entries:
        for chunk in entry.get("chunks", []):
            if chunk["sha256"] not in verified:
                with default_storage.open(object_key(task, chunk["sha256"]), "rb") as stream:
                    verify_chunk(stream, chunk)
                verified.add(chunk["sha256"])
    manifest = f"workshop-retained/{task.project_id}/{task.pk}/{snapshot['sha256']}.json"
    if not default_storage.exists(manifest):
        manifest = default_storage.save(manifest, ContentFile(json.dumps(entries).encode()))
    with default_storage.open(manifest, "rb") as stream:
        if cache_files.fingerprint(json.load(stream)) != snapshot["sha256"]:
            raise ValueError("Retained manifest checksum mismatch")
    result = {
        "manifest": manifest,
        "sha256": snapshot["sha256"],
        "bytes": sum(entry.get("bytes", 0) for entry in entries),
        "retained_at": timezone.now().isoformat(),
        "task_updated_at": task.updated_at.isoformat(),
        "accessed_at": task.updated_at.isoformat(),
        "resident": True,
    }
    with transaction.atomic():
        current = WorkshopTask.objects.select_for_update().get(pk=task.pk)
        if not eligible(current) or current.updated_at != task.updated_at:
            return None
        current.retention = result
        current.save(update_fields=["retention"])
    logger.info("Retained workshop %s: %s bytes", task.pk, result["bytes"])
    return result


def restore(task):
    sandbox = workspace.attach(task, reconcile=True)
    with transaction.atomic():
        current = WorkshopTask.objects.select_for_update().get(pk=task.pk)
        if remote(sandbox, current, "exists")["exists"]:
            if current.retention:
                current.retention = {
                    **current.retention,
                    "resident": True,
                    "accessed_at": timezone.now().isoformat(),
                }
                current.save(update_fields=["retention"])
            return False
        receipt = current.retention
        if not receipt.get("manifest"):
            raise ValueError("No verified retained workspace is available for recovery")
        with default_storage.open(receipt["manifest"], "rb") as stream:
            entries = json.load(stream)
        if cache_files.fingerprint(entries) != receipt["sha256"]:
            raise ValueError("Retained workspace manifest changed")
        destination = workspace.task_root(task)
        cache_files.validate(entries, destination)
        staging = destination + ".restore"
        remote(sandbox, task, "prepare_restore", root=staging, bytes=receipt["bytes"])
        items, uploads, batch_bytes = [], [], 0

        def install():
            if not items:
                return
            temporary = f"/tmp/workshop-cache-{uuid4()}.zip"
            try:
                contents = io.BytesIO()
                with zipfile.ZipFile(contents, "w") as archive:
                    for index, compressed in enumerate(uploads):
                        archive.writestr(str(index), compressed)
                sandbox.fs.upload_files(
                    [FileUpload(source=contents.getvalue(), destination=temporary)]
                )
                remote(
                    sandbox, task, "install_archive", root=staging, items=items, temporary=temporary
                )
            except Exception:
                with suppress(Exception):
                    sandbox.fs.delete_file(temporary)
                raise
            items.clear()
            uploads.clear()

        for entry in entries:
            for index, chunk in enumerate(entry.get("chunks", [])):
                if items and batch_bytes + chunk["bytes"] > cache_files.CHUNK_BYTES:
                    install()
                    batch_bytes = 0
                with default_storage.open(object_key(task, chunk["sha256"]), "rb") as incoming:
                    compressed = incoming.read(chunk["bytes"] + 1024**2 + 1)
                    if len(compressed) > chunk["bytes"] + 1024**2:
                        raise ValueError("Retained compressed chunk exceeds size limit")
                verify_chunk(io.BytesIO(compressed), chunk)
                uploads.append(compressed)
                items.append({"entry": entry, "index": index})
                batch_bytes += chunk["bytes"]
        install()
        remote(
            sandbox,
            task,
            "restore",
            root=staging,
            destination=destination,
            entries=entries,
            sha256=receipt["sha256"],
        )
        current.retention = {**receipt, "resident": True, "accessed_at": timezone.now().isoformat()}
        current.save(update_fields=["retention"])
    return True


def evict(task, sandbox):
    binding = WorkshopWorkspace.objects.get(project_id=task.project_id, enabled=True)
    verify_archive(task)
    with transaction.atomic():
        current = WorkshopTask.objects.select_for_update().get(pk=task.pk)
        slot = WorkshopWorkspace.objects.select_for_update().get(project_id=task.project_id)
        if (
            slot.task_owner
            or slot.sandbox_id != sandbox.id
            or slot.generation != binding.generation
            or not slot.enabled
        ):
            return False
        if (
            not eligible(current)
            or current.retention != task.retention
            or current.updated_at.isoformat() != current.retention["task_updated_at"]
        ):
            return False
        remote(sandbox, current, "evict", sha256=current.retention["sha256"])
        current.retention = {**current.retention, "resident": False}
        current.save(update_fields=["retention"])
    logger.info("Evicted retained workshop %s: %s bytes", task.pk, current.retention["bytes"])
    return True


def sweep(project_id):
    # The advisory lock serializes maintainers without holding up new task admission.
    key = int.from_bytes(hashlib.sha256(str(project_id).encode()).digest()[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        if not cursor.fetchone()[0]:
            return
    try:
        binding = WorkshopWorkspace.objects.get(project_id=project_id, enabled=True)
        if binding.task_owner or not binding.sandbox_id:
            return
        tasks = list(
            WorkshopTask.objects.filter(
                project_id=project_id, state__in=["completed", "cancelled", "blocked"]
            ).order_by("updated_at")
        )
        if not tasks:
            return
        sandbox = workspace.attach(tasks[0], reconcile=True)
        catalogue = remote(sandbox, tasks[0], "catalogue")
        roots = set(catalogue["roots"])
        for task in sorted(
            tasks, key=lambda item: item.retention.get("accessed_at", item.updated_at.isoformat())
        ):
            if str(task.pk) + ".evicted" in roots and task.retention:
                evict(task, sandbox)
                task.refresh_from_db()
            if str(task.pk) not in roots:
                continue
            if WorkshopWorkspace.objects.filter(
                project_id=project_id, task_owner__isnull=False
            ).exists():
                return
            if not task.actions.filter(sequence=0, state="completed").exists():
                continue
            sandbox = sandbox or workspace.attach(task, reconcile=True)
            try:
                retained = retain(task, sandbox)
                if retained and catalogue["free"] < catalogue["reserve"]:
                    task.refresh_from_db()
                    if evict(task, sandbox):
                        catalogue = remote(sandbox, task, "catalogue")
            except Exception:
                logger.exception("Workspace retention failed for %s; files remain pinned", task.pk)
        tasks = list(WorkshopTask.objects.filter(project_id=project_id).exclude(retention={}))
        cached = [task for task in tasks if task.retention.get("resident")]
        total = sum(task.retention["bytes"] for task in cached)
        for task in sorted(cached, key=lambda item: item.retention["accessed_at"]):
            if total <= settings.WORKSHOP_CACHE_BYTES:
                break
            if WorkshopWorkspace.objects.filter(
                project_id=project_id, task_owner__isnull=False
            ).exists():
                return
            sandbox = sandbox or workspace.attach(task, reconcile=True)
            try:
                if evict(task, sandbox):
                    total -= task.retention["bytes"]
            except Exception:
                logger.exception("Workspace eviction failed for %s; files remain pinned", task.pk)
        if total > settings.WORKSHOP_CACHE_BYTES:
            logger.warning(
                "Workspace %s remains above cache budget: %s bytes, budget %s",
                project_id,
                total,
                settings.WORKSHOP_CACHE_BYTES,
            )
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def maintain():
    try:
        for project in WorkshopWorkspace.objects.filter(
            enabled=True, task_owner__isnull=True
        ).values_list("project_id", flat=True):
            try:
                sweep(project)
            except Exception:
                logger.exception("Workspace cache maintenance failed for %s", project)
    finally:
        connections.close_all()


async def maintain_forever():
    while True:
        await asyncio.to_thread(maintain)
        await asyncio.sleep(30)
