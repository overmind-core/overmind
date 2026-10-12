"""Synthetic examples: rows the caller wrote, appended to a parent version as a new
child version. No code runs; the rows are checked against the parent's columns and the
dataset's intent."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

from overbae.models import Cell, Dataset
from overbae.services.datasets import contract, paths, review, store
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.datasets.partition import content_key

MAX_ROWS = 50
_HIDDEN = {store.SOURCE_ROW, review.PROVENANCE_COLUMN}


def script(rows: list[dict[str, Any]], instruction: str) -> str:
    return json.dumps(
        {"instruction": instruction, "rows": rows},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def check(dataset: Dataset, parent: Cell, rows: Any, instruction: str) -> str:
    if dataset.intent not in (Dataset.Intent.TRAIN, Dataset.Intent.EVAL):
        raise DatasetError("Choose train or eval before adding synthetic examples.", code="intent")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise DatasetError(f"Give between 1 and {MAX_ROWS} rows.", code="synthetic", field="rows")
    instruction = instruction.strip()
    if not instruction:
        raise DatasetError(
            "Describe what the rows are for in instruction.", code="synthetic", field="instruction"
        )
    columns = {c["name"] for c in parent.columns or []} - _HIDDEN
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or not row:
            raise DatasetError(f"Row {number} is not an object.", code="synthetic", field="rows")
        unknown = sorted(set(row) - columns)
        if unknown:
            raise DatasetError(
                f"Row {number} has columns {parent.label} does not have: {', '.join(unknown)}.",
                code="synthetic",
                field="rows",
            )
    return script(rows, instruction[:4000])


def write(dataset: Dataset, parent: Cell, text: str, output: Path) -> None:
    payload = json.loads(text)
    rows, instruction = payload["rows"], payload["instruction"]
    check(dataset, parent, rows, instruction)
    source = paths.cell_path(dataset.pk, parent.pk)
    frame = store.Frame(source)
    seen = {content_key(row, include_output=True) for row in store.records(source)}
    identities = (
        [k for k in frame.values(store.SOURCE_ROW) if k is not None]
        if (store.SOURCE_ROW in frame)
        else None
    )
    next_id = max(identities, default=-1) + 1 if identities is not None else None
    generated = []
    for offset, original in enumerate(rows):
        row = dict(original)
        digest = content_key(row, include_output=True)
        if digest in seen:
            raise DatasetError(
                f"Row {offset + 1} duplicates a row of {parent.label} or another new row.",
                code="synthetic",
            )
        seen.add(digest)
        if next_id is not None:
            row[store.SOURCE_ROW] = next_id + offset
        row[review.PROVENANCE_COLUMN] = {
            "kind": "synthetic",
            "synthetic": True,
            "id": str(uuid.uuid4()),
            "instruction": instruction,
            "parent": str(parent.pk),
        }
        generated.append(row)
    with tempfile.TemporaryDirectory(prefix="synthetic-") as scratch:
        added = Path(scratch) / "added.parquet"
        store.write_rows(added, generated)
        report = contract.measure(store.Frame(added))[dataset.intent]
        if not report["ok"]:
            raise DatasetError(
                f"The rows do not fit a {dataset.intent} dataset: {report['reason']}",
                code="synthetic",
            )
    store.write_extended(source, generated, output)
