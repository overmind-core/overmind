from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pandas as pd
from django.db.models import F
from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import measure, paths, store
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.datasets.partition import preserve_lineage

if TYPE_CHECKING:
    from overbae.services.datasets.land import Landing


def merge_frame(dataset: Dataset, cell: Cell, previous: Cell) -> pd.DataFrame:
    batch_path = paths.attachment_path(dataset.id, cell.id)
    if not batch_path.exists() or store.file_sha256(batch_path) != cell.review["batch_fingerprint"]:
        raise DatasetError("The saved attachment is unavailable. Upload the files again.")
    before = store.read_frame(paths.cell_path(dataset.id, previous.id))
    added = store.read_frame(batch_path)
    if set(before[store.SOURCE_ROW]) & set(added[store.SOURCE_ROW]):
        raise DatasetError("Row identities changed before this import. Add the files again.")
    return pd.concat([before, added], ignore_index=True, sort=False)


def commit(dataset: Dataset, landing: Landing, *, user=None) -> Cell:
    # The landing worker holds the dataset row lock through this entire commit.
    chain = dataset.chain
    previous = next(cell for cell in reversed(chain) if cell.state != Cell.State.PROPOSED)
    if previous.state != Cell.State.OK:
        raise DatasetError("Run or remove unfinished cells before adding files.")
    next_row = 0
    for cell in chain:
        if cell.ran:
            maximum = store.query(
                'SELECT max("source_row") AS last FROM t', t=paths.cell_path(dataset.id, cell.id)
            )["rows"][0]["last"]
            next_row = max(next_row, int(maximum or 0) + 1)
    rows = [dict(row) for row in landing.rows]
    for offset, row in enumerate(rows):
        row[store.SOURCE_ROW] = next_row + offset
        row["_overmind_provenance"] = preserve_lineage(row)
    sources = {item["id"]: item for item in dataset.source_spec.get("sources", [])}
    for artifact in landing.spec.get("sources", []):
        destination = paths.source_path(dataset.id, artifact["id"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact["staged_path"], destination)
        sources[artifact["id"]] = {k: v for k, v in artifact.items() if k != "staged_path"}
    for proposed in reversed([cell for cell in chain if cell.state == Cell.State.PROPOSED]):
        Cell.objects.filter(pk=proposed.pk).update(position=F("position") + 1)
    filenames = [item["filename"] for item in landing.spec.get("sources", [])]
    cell = Cell.objects.create(
        dataset=dataset,
        position=previous.position + 1,
        title="Added files" if filenames else "Added data",
        note=(", ".join(filenames) or "Attached rows")[:512],
        script="",
        state=Cell.State.QUEUED,
        created_by=user if getattr(user, "pk", None) else None,
    )
    batch_path = paths.attachment_path(dataset.id, cell.id)
    store.write_rows(batch_path, rows)
    cell.review = {
        "kind": "attachment",
        "added_rows": len(rows),
        "batch_fingerprint": store.file_sha256(batch_path),
        "sources": [item["id"] for item in landing.spec.get("sources", [])],
    }
    cell.save(update_fields=["review"])
    output = paths.cell_path(dataset.id, cell.id)
    store.write_frame(output, merge_frame(dataset, cell, previous))
    spec = {**dataset.source_spec, "sources": list(sources.values())}
    spec.pop("attachment_request", None)
    spec.pop("landing_progress", None)
    Dataset.objects.filter(pk=dataset.pk).update(
        source_spec=spec,
        active=cell,
        state=Dataset.State.DIAGNOSING,
        error="",
        updated_at=timezone.now(),
    )
    dataset.refresh_from_db()
    measure.frame(dataset, cell, output, input_fingerprint=previous.fingerprint, seconds=0.0)
    return cell
