from __future__ import annotations

import shutil
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING

from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import measure, operations, paths, store
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.datasets.partition import preserve_lineage

if TYPE_CHECKING:
    from overbae.services.datasets.land import Landing


def merge(dataset: Dataset, cell: Cell, previous: Cell, output: Path) -> Path:
    batch_path = paths.attachment_path(dataset.id, cell.id)
    if not batch_path.exists() or store.file_sha256(batch_path) != cell.review["batch_fingerprint"]:
        raise DatasetError("The saved attachment is unavailable. Upload the files again.")
    before = paths.cell_path(dataset.id, previous.id)
    collision = store.query(
        'SELECT 1 AS collision FROM before_rows JOIN added_rows USING ("source_row") LIMIT 1',
        before_rows=before,
        added_rows=batch_path,
    )["rows"]
    if collision:
        raise DatasetError("Row identities changed before this import. Add the files again.")

    def rows():
        for index, row in enumerate(chain(store.iter_rows(before), store.iter_rows(batch_path))):
            if index % 1000 == 0:
                operations.check_cancelled(dataset.id)
            yield row

    store.write_rows(output, rows())
    operations.check_cancelled(dataset.id)
    return output


def commit(dataset: Dataset, landing: Landing, *, user=None) -> Cell:
    # The landing worker holds the dataset row lock through this entire commit.
    chain = dataset.chain
    previous = next((cell for cell in reversed(chain) if cell.ran), None)
    if previous is None:
        raise DatasetError("No readable source is available. Upload a new dataset.")
    next_row = 0
    for cell in chain:
        if cell.ran:
            maximum = store.query(
                'SELECT max("source_row") AS last FROM t', t=paths.cell_path(dataset.id, cell.id)
            )["rows"][0]["last"]
            next_row = max(next_row, int(maximum or 0) + 1)

    def rows():
        for offset, original in enumerate(landing.rows):
            if offset % 1000 == 0:
                operations.check_cancelled(dataset.id)
            row = dict(original)
            row[store.SOURCE_ROW] = next_row + offset
            row["_overmind_provenance"] = preserve_lineage(row)
            yield row

    sources = {item["id"]: item for item in dataset.source_spec.get("sources", [])}
    for artifact in landing.spec.get("sources", []):
        destination = paths.source_path(dataset.id, artifact["id"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact["staged_path"], destination)
        sources[artifact["id"]] = {k: v for k, v in artifact.items() if k != "staged_path"}
    filenames = [item["filename"] for item in landing.spec.get("sources", [])]
    cell = Cell.objects.create(
        dataset=dataset,
        position=chain[-1].position + 1,
        title="Added files" if filenames else "Added data",
        note=(", ".join(filenames) or "Attached rows")[:512],
        script="",
        state=Cell.State.QUEUED,
        created_by=user if getattr(user, "pk", None) else None,
    )
    batch_path = paths.attachment_path(dataset.id, cell.id)
    store.write_rows(batch_path, rows())
    cell.review = {
        "kind": "attachment",
        "added_rows": store.row_count(batch_path),
        "batch_fingerprint": store.file_sha256(batch_path),
        "sources": [item["id"] for item in landing.spec.get("sources", [])],
    }
    cell.save(update_fields=["review"])
    output = paths.cell_path(dataset.id, cell.id)
    merge(dataset, cell, previous, output)
    spec = {**dataset.source_spec, "sources": list(sources.values())}
    spec.pop("attachment_request", None)
    spec.pop("landing_progress", None)
    Dataset.objects.filter(pk=dataset.pk).update(
        source_spec=spec,
        active=cell,
        state=Dataset.State.IDLE,
        error="",
        updated_at=timezone.now(),
    )
    dataset.refresh_from_db()
    measure.frame(dataset, cell, output, input_fingerprint=previous.fingerprint, seconds=0.0)
    return cell
