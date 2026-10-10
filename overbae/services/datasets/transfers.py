import hashlib
import json
import logging

from django.db import transaction
from django.utils import timezone

from overbae.models import Capability, Dataset, DatasetTransfer
from overbae.services.datasets import dispatch, files
from overbae.services.datasets.lifecycle import DatasetError

logger = logging.getLogger(__name__)
PROTOCOL_VERSION = 1


def describe(transfer):
    spec = transfer.specification
    staged = files.upload_data_path(transfer.upload_id).is_file()
    return {
        "id": str(transfer.pk),
        "project_id": str(transfer.project_id),
        "request_key": transfer.request_key,
        "upload_id": str(transfer.upload_id),
        "filename": spec["filename"],
        "sha256": spec["sha256"],
        "size": spec["size"],
        "received": transfer.received
        if transfer.state == "published"
        else files.upload_received(transfer.upload_id),
        "state": transfer.state,
        "staging_available": staged,
        "next_action": "inspect_dataset"
        if transfer.state == "published" and transfer.dispatched_at
        else "resume_same_command"
        if transfer.state == "published" or staged
        else "start_new_transfer_with_new_request_key",
        "result": transfer.result,
        "chunk_bytes": files.CHUNK_BYTES,
        "max_bytes": files.upload_byte_limit(spec["filename"]),
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
        files.begin_upload(specification["filename"], upload_id=transfer.upload_id)
    return transfer


def staged_path(transfer):
    path = files.upload_data_path(transfer.upload_id)
    if not path.is_file():
        raise DatasetError(
            "The transfer's staged bytes are unavailable. Start an explicitly new transfer.",
            code="transfer_bytes_missing",
        )
    return path


@transaction.atomic
def append(transfer, offset, content):
    transfer = DatasetTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.state != "uploading":
        raise DatasetError("This transfer is already published.", code="transfer_published")
    path = staged_path(transfer)
    size = path.stat().st_size
    if (
        offset < 0
        or not content
        or len(content) > files.CHUNK_BYTES
        or offset + len(content) > transfer.specification["size"]
    ):
        raise DatasetError("The chunk is outside the declared file bounds.", code="invalid_chunk")
    if offset < size:
        with path.open("rb") as stream:
            stream.seek(offset)
            matches = stream.read(len(content)) == content
        if not matches:
            raise DatasetError("These bytes differ from the stored chunk.", code="chunk_conflict")
    transfer.received = files.append_chunk(transfer.upload_id, offset, content)
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
    path = staged_path(transfer)
    if path.stat().st_size != spec["size"]:
        raise DatasetError("The file transfer is incomplete.", code="transfer_incomplete")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(files.CHUNK_BYTES), b""):
            digest.update(block)
    if digest.hexdigest() != spec["sha256"]:
        raise DatasetError(
            "The received file does not match its declared SHA-256.", code="file_hash_mismatch"
        )
    source = {"upload_id": str(transfer.upload_id), "filename": spec["filename"]}
    if spec.get("json_rows_field"):
        source["json_rows_field"] = spec["json_rows_field"]
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
            spec["filename"] + (" train" if spec.get("split") else ""),
            source,
            "train" if spec.get("split") else spec.get("intent"),
            capability,
            spec.get("brief", ""),
        )
        if spec.get("split"):
            evaluation = dispatch.stage_dataset(
                transfer.project,
                transfer.user,
                spec["filename"] + " eval",
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
    transfer.state = "published"
    transfer.received = spec["size"]
    transfer.published_at = timezone.now()
    transfer.save(
        update_fields=["state", "result", "task", "received", "published_at", "updated_at"]
    )
    transaction.on_commit(lambda: dispatch_landing(transfer.pk))
    return transfer
