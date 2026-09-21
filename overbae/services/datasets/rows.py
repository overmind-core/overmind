"""How consumers read a cell's rows.

A row is addressed by ``(cell, index)``; ``DatasetRow`` is the in-process
shape eval, training and the optimiser work with, so none of them touch
Parquet directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from overbae.services.datasets import paths, store
from overbae.services.datasets.examples import missing, normalize_record
from overbae.services.datasets.partition import contamination_keys


class RowStoreError(RuntimeError):
    """The frame is missing or no longer matches the cell's fingerprint."""


@dataclass
class DatasetRow:
    index: int
    input: Any = None
    expected_output: Any = None
    extra: dict[str, Any] = field(default_factory=dict)
    source_trace_id: str = ""
    behaviour_key: str = ""

    @property
    def id(self) -> str:
        return str(self.index)

    @property
    def order(self) -> int:
        return self.index


def row_from_record(index: int, record: dict[str, Any]) -> DatasetRow:
    """Canonical columns become fields; everything else rides in ``extra``,
    which is where ``behaviour_key`` and ``model_expected_output`` live."""
    record = normalize_record(record)
    extra = {k: v for k, v in record.items() if k not in ("input", "expected_output")}
    inp = record.get("input")
    if inp is None and isinstance(record.get("messages"), list):
        inp = {"messages": record["messages"]}
        if not missing(record.get("tools")):
            inp["tools"] = record["tools"]
    trace_id = record.get("source_trace_id") or record.get("trace_id") or ""
    return DatasetRow(
        index=index,
        input=inp,
        expected_output=record.get("expected_output"),
        extra=extra,
        source_trace_id=str(trace_id or ""),
        behaviour_key=str(record.get("behaviour_key") or ""),
    )


def frame_path(cell: Any) -> Path:
    path = paths.cell_path(cell.dataset_id, cell.id)
    if not cell.fingerprint or not path.exists():
        raise RowStoreError(f"{cell.title} has no frame on disk.")
    return path


def verify(cell: Any) -> None:
    """Hash the file once and compare; called at run start so a run never
    grades rows that are not the ones it pinned."""
    path = frame_path(cell)
    actual = store.file_sha256(path)
    if actual != cell.fingerprint:
        raise RowStoreError(
            f"{cell.title}'s frame has changed (expected {cell.fingerprint[:12]}, "
            f"found {actual[:12]})."
        )


def iter_rows(cell: Any) -> Iterator[DatasetRow]:
    path = frame_path(cell)
    for index, record in enumerate(store.iter_rows(path)):
        yield row_from_record(index, record)


def rows(cell: Any, indices: list[int]) -> list[DatasetRow]:
    path = frame_path(cell)
    return [
        row_from_record(i, rec)
        for i, rec in zip(indices, store.read_rows(path, indices), strict=False)
    ]


def row(cell: Any, index: int) -> DatasetRow | None:
    found = rows(cell, [index])
    return found[0] if found else None


def count(cell: Any) -> int:
    return int(cell.rows or 0)


def trace_ids(cell: Any) -> set[str]:
    if not cell.fingerprint:
        return set()
    path = frame_path(cell)
    manifest = {c["name"] for c in store.read_manifest(path)}
    column = (
        "source_trace_id"
        if "source_trace_id" in manifest
        else "trace_id"
        if "trace_id" in manifest
        else None
    )
    if column is None:
        return set()
    result = store.query(
        f'SELECT DISTINCT "{column}" AS t FROM t WHERE "{column}" IS NOT NULL', limit=None, t=path
    )
    return {str(r["t"]) for r in result["rows"] if r["t"]}


def contamination(train, evaluation) -> dict:
    group_by = set(train.dataset.source_spec.get("split", {}).get("group_by", []))
    group_by.update(evaluation.dataset.source_spec.get("split", {}).get("group_by", []))
    keys = set()
    for record in store.iter_rows(frame_path(evaluation)):
        keys.update(contamination_keys(record, group_by))
    count = 0
    examples = []
    for index, record in enumerate(store.iter_rows(frame_path(train))):
        shared = contamination_keys(record, group_by) & keys
        if shared:
            count += 1
            if len(examples) < 20:
                examples.append(
                    {
                        "row": record.get(store.SOURCE_ROW, index),
                        "matches": sorted({key[0] for key in shared}),
                    }
                )
    return {
        "overlap_count": count,
        "train_total": train.rows,
        "basis": "exact input content, trace/conversation/group identity and synthetic seed lineage",
        "examples": examples,
        "near_duplicate_check": "not_checked",
    }


def sample_records(dataset: Any, limit: int) -> list[dict[str, Any]]:
    """The first ``limit`` rows of the dataset's active cell; [] when there is none."""
    cell = dataset.active_cell
    if cell is None:
        return []
    try:
        return store.head(frame_path(cell), limit)
    except (RowStoreError, OSError):
        return []


def sample_rows(dataset: Any, limit: int) -> list[DatasetRow]:
    return [row_from_record(i, rec) for i, rec in enumerate(sample_records(dataset, limit))]


def sample_expected_outputs(dataset: Any, limit: int) -> list[Any]:
    out = [rec.get("expected_output") for rec in sample_records(dataset, limit * 2)]
    return [v for v in out if v not in (None, "")][:limit]


EMPTY_STATS = {
    "num_examples": 0,
    "has_tool_calling": False,
    "has_multi_turn_tool_calls": False,
    "max_token_length": 0,
    "avg_input_chars": 0,
    "avg_output_chars": 0,
}


def dataset_stats(dataset: Any, cell: Any = None) -> dict[str, Any]:
    """The training stats of ``cell``, or of the active cell; zeros when nothing ran."""
    cell = cell or dataset.active_cell
    if cell is None or not cell.stats:
        return dict(EMPTY_STATS)
    return dict(cell.stats)


def capability_dataset_rows(capability: Any) -> int:
    """Rows of the newest active cell among the capability's datasets."""
    from overbae.models import Dataset

    for dataset in Dataset.objects.filter(capability=capability).order_by("-updated_at")[:5]:
        cell = dataset.active_cell
        if cell is not None:
            return int(cell.rows)
    return 0
