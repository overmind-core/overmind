import hashlib
import json
import logging
import uuid

from django.db import transaction
from django.utils import timezone

from overbae.models import Capability, Dataset, DatasetImport, DatasetTransfer
from overbae.services.datasets import dispatch, files, imports
from overbae.services.datasets.lifecycle import DatasetError

logger = logging.getLogger(__name__)
PROTOCOL_VERSION = 1


def transfer_files(transfer):
    return [
        {
            **item,
            "upload_id": str(
                transfer.upload_id if index == 0 else uuid.uuid5(transfer.upload_id, str(index))
            ),
        }
        for index, item in enumerate(
            transfer.specification.get("files") or [transfer.specification]
        )
    ]


def describe(transfer):
    spec = transfer.specification
    members = [
        {
            **{key: item[key] for key in ("filename", "sha256", "size", "upload_id")},
            **({"json_rows_field": item["json_rows_field"]} if item.get("json_rows_field") else {}),
            "index": index,
            "received": item["size"]
            if transfer.state == "published"
            else files.upload_received(item["upload_id"]),
            "staging_available": files.upload_data_path(item["upload_id"]).is_file(),
            "max_bytes": files.upload_byte_limit(item["filename"]),
        }
        for index, item in enumerate(transfer_files(transfer))
    ]
    staged = all(item["staging_available"] for item in members)
    return {
        "id": str(transfer.pk),
        "project_id": str(transfer.project_id),
        "request_key": transfer.request_key,
        "upload_id": str(transfer.upload_id),
        "filename": spec.get("filename", f"{len(members)} files"),
        "sha256": spec.get("sha256"),
        "size": sum(item["size"] for item in members),
        "files": members,
        "received": sum(item["received"] for item in members),
        "state": transfer.state,
        "staging_available": staged,
        "next_action": "inspect_dataset"
        if transfer.state == "published" and transfer.dispatched_at
        else "resume_same_command"
        if transfer.state == "published" or staged
        else "start_new_transfer_with_new_request_key",
        "result": transfer.result,
        "chunk_bytes": files.CHUNK_BYTES,
        "max_bytes": files.MAX_UPLOAD_BYTES if "files" in spec else members[0]["max_bytes"],
        "dispatch": "acknowledged"
        if transfer.dispatched_at
        else "pending"
        if transfer.task
        else "not_requested",
        "updated_at": transfer.updated_at,
        "published_at": transfer.published_at,
        "resource_uri": f"overmind://jobs/dataset_transfer/{transfer.pk}?project_id={transfer.project_id}",
    }


@transaction.atomic
def reserve(project, user, request_key, specification):
    fingerprint = hashlib.sha256(
        json.dumps(specification, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    transfer, created = DatasetTransfer.objects.get_or_create(
        project=project,
        request_key=request_key,
        defaults={"user": user, "specification": specification, "fingerprint": fingerprint},
    )
    transfer = DatasetTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.fingerprint != fingerprint:
        raise DatasetError(
            "This request key already binds a different file or destination recipe.",
            code="request_key_conflict",
        )
    if created:
        for item in transfer_files(transfer):
            files.begin_upload(item["filename"], upload_id=item["upload_id"], managed=True)
    return transfer


def staged_path(transfer, upload_id=None):
    path = files.upload_data_path(upload_id or transfer.upload_id)
    if not path.is_file():
        raise DatasetError(
            "The transfer's staged bytes are unavailable. Start an explicitly new transfer.",
            code="transfer_bytes_missing",
        )
    return path


@transaction.atomic
def append(transfer, offset, content, *, file_index=0):
    transfer = DatasetTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.state != "uploading":
        raise DatasetError("This transfer is already published.", code="transfer_published")
    members = transfer_files(transfer)
    if not 0 <= file_index < len(members):
        raise DatasetError("The file index is outside this transfer.", code="invalid_chunk")
    item = members[file_index]
    path = staged_path(transfer, item["upload_id"])
    size = files.stored_stat(path).st_size
    if (
        offset < 0
        or not content
        or len(content) > files.CHUNK_BYTES
        or offset + len(content) > item["size"]
    ):
        raise DatasetError("The chunk is outside the declared file bounds.", code="invalid_chunk")
    if offset < size:
        with path.open("rb") as stream:
            stream.seek(offset)
            matches = stream.read(len(content)) == content
        if not matches:
            raise DatasetError("These bytes differ from the stored chunk.", code="chunk_conflict")
    files.append_chunk(item["upload_id"], offset, content)
    transfer.received = sum(files.upload_received(member["upload_id"]) for member in members)
    transfer.save(update_fields=["received", "updated_at"])
    return transfer


def dispatch_landing(transfer_id):
    # A lost broker acknowledgement can redeliver this same task; landing claims
    # the dataset and attachment identity atomically before publishing any cells.
    from overbae.tasks.datasets import land

    transfer = DatasetTransfer.objects.get(pk=transfer_id)
    if not transfer.task or transfer.dispatched_at:
        return
    try:
        land.apply_async(kwargs=transfer.task, task_id=str(transfer.pk))
    except Exception:
        logger.exception("Dataset transfer landing dispatch unacknowledged: %s", transfer.pk)
        return
    DatasetTransfer.objects.filter(pk=transfer.pk).update(dispatched_at=timezone.now())
    DatasetImport.objects.filter(pk=transfer.pk, state="queued").update(
        published_at=timezone.now(), next_publish_at=None
    )


@transaction.atomic
def complete(transfer):
    transfer = (
        DatasetTransfer.objects.select_for_update(of=("self",))
        .select_related("project", "user")
        .get(pk=transfer.pk)
    )
    if transfer.state == "published":
        if not transfer.dispatched_at:
            transaction.on_commit(lambda: dispatch_landing(transfer.pk))
        return transfer
    spec = transfer.specification
    members = transfer_files(transfer)
    for item in members:
        path = staged_path(transfer, item["upload_id"])
        if files.stored_stat(path).st_size != item["size"]:
            raise DatasetError("The file transfer is incomplete.", code="transfer_incomplete")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(files.CHUNK_BYTES), b""):
                digest.update(block)
        if digest.hexdigest() != item["sha256"]:
            raise DatasetError(
                "The received file does not match its declared SHA-256.", code="file_hash_mismatch"
            )
        files.inspect_upload(
            item["upload_id"], size=item["size"], json_rows_field=item.get("json_rows_field")
        )
    if "files" in spec:
        source = {"uploads": [item["upload_id"] for item in members]}
    else:
        source = {"upload_id": str(transfer.upload_id), "filename": spec["filename"]}
        if spec.get("json_rows_field"):
            source["json_rows_field"] = spec["json_rows_field"]
    name = spec.get("filename") or members[0]["filename"] + f" and {len(members) - 1} files"
    capability = None
    if spec.get("capability"):
        capability = Capability.objects.filter(
            project=transfer.project, pk=spec["capability"]
        ).first()
        if capability is None:
            raise DatasetError("The capability is no longer accessible.", code="target_unavailable")
    attachment_request = ""
    split = None
    if spec.get("dataset"):
        dataset = Dataset.objects.filter(project=transfer.project, pk=spec["dataset"]).first()
        if dataset is None:
            raise DatasetError(
                "The destination dataset is no longer accessible.", code="target_unavailable"
            )
        attachment_request = dispatch.stage_attachment(dataset, source)
    else:
        dataset = dispatch.stage_dataset(
            transfer.project,
            transfer.user,
            name + (" train" if spec.get("split") else ""),
            source,
            "train" if spec.get("split") else spec.get("intent"),
            capability,
            spec.get("brief", ""),
        )
        if spec.get("split"):
            evaluation = dispatch.stage_dataset(
                transfer.project,
                transfer.user,
                name + " eval",
                source,
                "eval",
                capability,
                spec.get("brief", ""),
            )
            split = {
                "eval_dataset_id": str(evaluation.pk),
                "eval_percent": spec["split"],
                "position": spec["split_position"],
                "deduplicate": False,
            }
    dataset.refresh_from_db(fields=["ocr_languages"])
    source = {**source, "ocr_languages": dataset.ocr_languages}
    transfer.result = {"id": str(dataset.pk), "state": "landing"}
    if split:
        transfer.result.update(eval_id=split["eval_dataset_id"], eval_state="landing")
    transfer.task = {
        "dataset_id": str(dataset.pk),
        "source": source,
        "user_id": str(transfer.user_id) if transfer.user_id else None,
        "infer_capability": False,
        "attachment_request": attachment_request,
        "split": split,
    }
    if not attachment_request or (
        not dataset.cells.exists() and not DatasetImport.objects.filter(dataset=dataset).exists()
    ):
        imports.queue_landing_receipt(dataset, transfer.pk, transfer.task)
    transfer.state = "published"
    transfer.received = sum(item["size"] for item in members)
    transfer.published_at = timezone.now()
    transfer.save(
        update_fields=["state", "result", "task", "received", "published_at", "updated_at"]
    )
    transaction.on_commit(lambda: dispatch_landing(transfer.pk))
    return transfer
