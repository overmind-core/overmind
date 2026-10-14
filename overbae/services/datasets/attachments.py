"""A table uploaded beside a script: the script reads it as the frame ``attachment``.
The upload is staged through ``/api/uploads/``; a version that read it keeps a copy
under the dataset, so the version stays reproducible after the upload expires."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from overbae.models import Execution
from overbae.services.datasets import files, paths
from overbae.services.datasets.lifecycle import DatasetError
from sandbox import tables


def describe(upload_id: str, *, project: Any, user: Any = None) -> dict[str, Any]:
    """The checked upload as a run records it; another project's upload does not exist."""
    try:
        upload_id = str(uuid.UUID(str(upload_id)))
    except ValueError:
        raise DatasetError("attachment is an upload id.", field="attachment") from None
    filename = files.upload_filename(upload_id)
    record = files.inspection(upload_id)
    owned = files.belongs(upload_id, project=project, user=user)
    if not filename or record is None or not record.get("sha256") or not owned:
        raise DatasetError(
            "The attachment is missing or not inspected. Upload it with "
            "`overmind dataset attach FILE --json`.",
            code="attachment_missing",
            field="attachment",
        )
    if files.is_document(filename):
        raise DatasetError(
            "Attach a table: CSV, TSV, JSON, JSONL or Parquet.",
            code="attachment_invalid",
            field="attachment",
        )
    return {
        "upload_id": upload_id,
        "filename": filename,
        "bytes": record["bytes"],
        "rows": record["rows"],
        "sha256": record["sha256"],
    }


def identity(script: str, attachment: dict[str, Any] | None) -> str:
    """What makes a version unique under its parent: the script, and the file it read."""
    if not attachment:
        return script
    return f"{script}\n# attachment sha256:{attachment['sha256']}"


def _source(dataset_id: Any, attachment: dict[str, Any]) -> Path:
    retained = paths.attachment_path(dataset_id, attachment["sha256"])
    if retained.exists():
        return retained
    upload = files.upload_data_path(attachment["upload_id"])
    record = files.inspection(attachment["upload_id"])
    if record is None or record.get("sha256") != attachment["sha256"]:
        raise DatasetError(
            "The attachment upload expired or changed. Upload it again.",
            code="attachment_missing",
            field="attachment",
        )
    return upload


def write_frame(dataset_id: Any, attachment: dict[str, Any], output: Path) -> None:
    """The attachment as a frame, columns and values as the file holds them."""
    source = _source(dataset_id, attachment)
    try:
        rows = files.FileRows(source, filename=attachment["filename"])
    except files.FileError as exc:
        raise DatasetError(exc.detail, code="attachment_invalid") from exc
    manifest = tables.ManifestBuilder()
    for row in rows:
        manifest.add(row)
    with tables.TableWriter(output, manifest.manifest()) as writer:
        writer.write_rows(rows)


def retain(dataset_id: Any, attachment: dict[str, Any]) -> Path | None:
    """Keep the file with the dataset; returns the path it created, if any."""
    destination = paths.attachment_path(dataset_id, attachment["sha256"])
    if destination.exists():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_source(dataset_id, attachment), destination)
    return destination


def retained_uploads() -> set[str]:
    """Uploads a queued or running script still has to read."""
    return {
        attachment["upload_id"]
        for attachment in Execution.objects.filter(
            state__in=[Execution.State.QUEUED, Execution.State.RUNNING]
        )
        .exclude(attachment={})
        .values_list("attachment", flat=True)
    }
