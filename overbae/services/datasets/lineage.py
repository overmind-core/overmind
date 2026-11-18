"""Row lineage keys. Beside each frame, a key file holds every row's ``source_row`` and
its contamination keys: the row's own and those of the rows it came from. Leakage
counts, partitions and validation splits compare these keys across cells."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from overbae.services.datasets import paths, store
from overbae.services.datasets.partition import IDENTITY_COLUMNS, contamination_keys, group_keys

Keys = frozenset[tuple[str, str]]
NONE: Keys = frozenset()
# Kinds and canonical JSON values never hold a raw tab.
_SEP = "\t"
_SCHEMA = pa.schema([("source_row", pa.int64()), ("keys", pa.list_(pa.string()))])


def keys_path(frame: Path) -> Path:
    return frame.with_suffix(".keys.parquet")


def own_keys(row: dict) -> Keys:
    return frozenset(contamination_keys(row))


class Writer:
    """A frame's key file, written as rows arrive: a measured frame holds no key set in
    memory, so a million rows leave nothing for the garbage collector to walk."""

    def __init__(self, frame: Path):
        self.destination = keys_path(frame)
        # Its own name: two readers that derive one legacy key file never share it.
        self.temporary = self.destination.with_suffix(f".{uuid.uuid4().hex}.tmp")
        self.writer = pq.ParquetWriter(self.temporary, _SCHEMA, compression="zstd")
        self.batch = []

    def add(self, source_row: int, keys: Keys) -> None:
        self.batch.append((source_row, sorted(f"{kind}{_SEP}{value}" for kind, value in keys)))
        if len(self.batch) == store.ROW_GROUP_SIZE:
            self.writer.write_table(_table(self.batch))
            self.batch = []

    def close(self) -> None:
        try:
            if self.batch:
                self.writer.write_table(_table(self.batch))
            self.writer.close()
            self.temporary.replace(self.destination)
        except BaseException:
            self.temporary.unlink(missing_ok=True)
            raise

    def abort(self) -> None:
        self.writer.close()
        self.temporary.unlink(missing_ok=True)


def write(frame: Path, rows: Iterable[tuple[int, Keys]]) -> None:
    """``rows`` in frame order: each row's ``source_row`` and its full key set."""
    writer = Writer(frame)
    try:
        for source_row, keys in rows:
            writer.add(source_row, keys)
    except BaseException:
        writer.abort()
        raise
    writer.close()


def _table(batch) -> pa.Table:
    return pa.Table.from_arrays(
        [
            pa.array([row for row, _ in batch], pa.int64()),
            pa.array([keys for _, keys in batch], pa.list_(pa.string())),
        ],
        schema=_SCHEMA,
    )


def load(frame: Path) -> list[tuple[int, Keys]]:
    table = pq.read_table(keys_path(frame))
    return [
        (source_row, frozenset(tuple(key.split(_SEP, 1)) for key in keys))
        for source_row, keys in zip(
            table.column("source_row").to_pylist(), table.column("keys").to_pylist(), strict=True
        )
    ]


def _derived(cell) -> Path:
    """The cell's frame, with its key file. A frame written before key files carries its
    lineage in ``_overmind_provenance``; its key file is derived from that once."""
    frame = paths.cell_path(cell.dataset_id, cell.id)
    if not keys_path(frame).exists():
        write(
            frame,
            ((row.get(store.SOURCE_ROW), own_keys(row)) for row in store.iter_rows(frame)),
        )
    return frame


def read(cell) -> list[tuple[int, Keys]]:
    """The cell's rows in frame order."""
    return load(_derived(cell))


def stream(cell) -> Iterator[Keys]:
    """The cell's key sets in frame order, one row group at a time."""
    keys = pq.ParquetFile(keys_path(_derived(cell)))
    for batch in keys.iter_batches(columns=["keys"]):
        for row in batch.column(0).to_pylist():
            yield frozenset(tuple(key.split(_SEP, 1)) for key in row)


def grouped(frame: Path, keyed, group_by) -> Iterator[tuple[int, Keys]]:
    """``keyed`` plus the keys of declared group columns outside the fixed identity set."""
    extra = set(group_by) - set(IDENTITY_COLUMNS)
    if not extra:
        yield from keyed
        return
    columns = sorted({*extra, *(column.split(".", 1)[0] for column in extra)})
    for (source_row, keys), row in zip(keyed, store.iter_rows(frame, columns=columns), strict=True):
        yield source_row, keys | group_keys(row, extra)


def with_groups(cell, group_by) -> Iterator[tuple[int, Keys]]:
    return grouped(paths.cell_path(cell.dataset_id, cell.id), read(cell), group_by)
