"""The frame file format.

Every table carries a column manifest ``[{name, type}]`` where ``type`` is one of
``string | integer | number | boolean | datetime | json``. Nested values are
stored as JSON text so a messy upload never fails schema unification.

The cell sandbox loads this module by path: no Django or DuckDB imports.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

SOURCE_ROW = "source_row"
MANIFEST_KEY = b"overmind.columns"
ROW_GROUP_ROWS = 10_000
ROW_GROUP_BYTES = 16 * 1024**2
BATCH_BYTES = 8 * 1024**2
ARROW_TYPES = {
    "string": pa.string(),
    "integer": pa.int64(),
    "number": pa.float64(),
    "boolean": pa.bool_(),
    "datetime": pa.timestamp("us", tz="UTC"),
    "json": pa.string(),
}


def is_missing(value: Any) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    return isinstance(value, float) and math.isnan(value)


def _finite(value: Any) -> Any:
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def json_dumps(value: Any, *, sort_keys: bool = False) -> str:
    # JSON has no NaN token.
    return json.dumps(_finite(value), ensure_ascii=False, default=str, sort_keys=sort_keys)


def coerce(value: Any, kind: str) -> Any:
    if is_missing(value):
        return None
    if kind == "json":
        if hasattr(value, "tolist"):
            value = value.tolist()
        return json_dumps(value)
    if kind == "string":
        if isinstance(value, (dict, list)):
            return json_dumps(value)
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return str(value)
    if kind == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if kind == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if kind == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)
    if kind == "datetime":
        try:
            ts = pd.Timestamp(value).to_pydatetime()
        except (TypeError, ValueError):
            return None
        return ts.astimezone(dt.UTC) if ts.tzinfo else ts.replace(tzinfo=dt.UTC)
    return value


def kind_of(value: Any) -> str | None:
    if is_missing(value):
        return None
    if isinstance(value, (dict, list, tuple)):
        return "json"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, (dt.datetime, pd.Timestamp)):
        return "datetime"
    return "string"


def union_kind(kinds: Iterable[str]) -> str:
    kinds = set(kinds)
    if not kinds:
        return "string"
    if "json" in kinds:
        return "json"
    if kinds <= {"integer", "number"}:
        return "number" if "number" in kinds else "integer"
    if len(kinds) == 1:
        return kinds.pop()
    return "string"


def infer_kind(values: Iterable[Any]) -> str:
    return union_kind(k for k in map(kind_of, values) if k is not None)


class ManifestBuilder:
    def __init__(self) -> None:
        self.kinds: dict[str, set[str]] = {}

    def add(self, row: dict[str, Any]) -> None:
        for key, value in row.items():
            kinds = self.kinds.setdefault(str(key), set())
            if "json" not in kinds:
                kind = kind_of(value)
                if kind is not None:
                    kinds.add(kind)

    def manifest(self) -> list[dict[str, str]]:
        return [{"name": name, "type": union_kind(kinds)} for name, kinds in self.kinds.items()]


def infer_manifest(rows: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    builder = ManifestBuilder()
    for row in rows:
        builder.add(row)
    return builder.manifest()


def kind_of_series(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    return infer_kind(series.tolist())


def manifest_from_frame(df: pd.DataFrame) -> list[dict[str, str]]:
    return [{"name": str(name), "type": kind_of_series(df[name])} for name in df.columns]


def schema(manifest: list[dict[str, str]]) -> pa.Schema:
    return pa.schema(
        [pa.field(spec["name"], ARROW_TYPES[spec["type"]]) for spec in manifest],
        metadata={MANIFEST_KEY: json_dumps(manifest, sort_keys=True).encode()},
    )


def table_from_columns(columns: dict[str, list[Any]], manifest: list[dict[str, str]]) -> pa.Table:
    arrays = [
        pa.array(
            [coerce(v, spec["type"]) for v in columns.get(spec["name"], [])],
            type=ARROW_TYPES[spec["type"]],
        )
        for spec in manifest
    ]
    return pa.Table.from_arrays(arrays, schema=schema(manifest))


def _kind_of_arrow(arrow_type: pa.DataType) -> str:
    if pa.types.is_boolean(arrow_type):
        return "boolean"
    if pa.types.is_integer(arrow_type):
        return "integer"
    if pa.types.is_floating(arrow_type):
        return "number"
    if pa.types.is_timestamp(arrow_type):
        return "datetime"
    return "string"


def read_manifest(path: Path) -> list[dict[str, str]]:
    found = pq.read_schema(path)
    raw = (found.metadata or {}).get(MANIFEST_KEY)
    if raw:
        return json.loads(raw)
    return [{"name": field.name, "type": _kind_of_arrow(field.type)} for field in found]


class TableWriter:
    def __init__(self, path: Path, manifest: list[dict[str, str]]) -> None:
        self.path = Path(path)
        self.manifest = manifest
        self.schema = schema(manifest)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._tmp = self.path.with_suffix(".parquet.tmp")
        self._writer = pq.ParquetWriter(self._tmp, self.schema, compression="zstd")
        self._pending: list[pa.Table] = []
        self._pending_rows = self._pending_bytes = 0
        self._rows: list[dict[str, Any]] = []
        self._row_target = 256
        self.rows = 0

    def __enter__(self) -> TableWriter:
        return self

    def __exit__(self, kind, _exc, _tb) -> None:
        if kind is None:
            self.close()
        else:
            self.abort()

    def write_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        for row in rows:
            self._rows.append(row)
            if len(self._rows) >= self._row_target:
                self._flush_rows()

    def _flush_rows(self) -> None:
        if not self._rows:
            return
        names = [spec["name"] for spec in self.manifest]
        table = table_from_columns(
            {n: [r.get(n) for r in self._rows] for n in names}, self.manifest
        )
        per_row = max(table.nbytes // max(table.num_rows, 1), 1)
        self._row_target = max(1, min(ROW_GROUP_ROWS, ROW_GROUP_BYTES // per_row))
        self._rows = []
        self.write_table(table)

    def write_table(self, table: pa.Table) -> None:
        if table.schema != self.schema:
            table = table.cast(self.schema)
        offset = 0
        while offset < table.num_rows:
            per_row = max(table.nbytes // max(table.num_rows, 1), 1)
            room_rows = ROW_GROUP_ROWS - self._pending_rows
            room_bytes = max(ROW_GROUP_BYTES - self._pending_bytes, per_row)
            take = max(1, min(table.num_rows - offset, room_rows, room_bytes // per_row))
            piece = table.slice(offset, take)
            self._pending.append(piece)
            self._pending_rows += take
            self._pending_bytes += take * per_row
            offset += take
            if self._pending_rows >= ROW_GROUP_ROWS or self._pending_bytes >= ROW_GROUP_BYTES:
                self._flush_group()

    def _flush_group(self) -> None:
        if not self._pending:
            return
        group = pa.concat_tables(self._pending).combine_chunks()
        self._writer.write_table(group, row_group_size=group.num_rows)
        self.rows += group.num_rows
        self._pending = []
        self._pending_rows = self._pending_bytes = 0

    def close(self) -> None:
        self._flush_rows()
        self._flush_group()
        self._writer.close()
        self._tmp.replace(self.path)

    def abort(self) -> None:
        self._writer.close()
        self._tmp.unlink(missing_ok=True)


def write_frame(path: Path, df: pd.DataFrame) -> list[dict[str, str]]:
    manifest = manifest_from_frame(df)
    with TableWriter(path, manifest) as writer:
        step = 1_000
        for start in range(0, len(df), step):
            chunk = df.iloc[start : start + step]
            names = [spec["name"] for spec in manifest]
            writer.write_table(table_from_columns({n: chunk[n].tolist() for n in names}, manifest))
    return manifest


def uncompressed_bytes(path: Path, columns: Iterable[str] | None = None) -> int:
    meta = pq.read_metadata(path)
    wanted = set(columns) if columns is not None else None
    total = 0
    for g in range(meta.num_row_groups):
        group = meta.row_group(g)
        for c in range(group.num_columns):
            chunk = group.column(c)
            if wanted is None or chunk.path_in_schema in wanted:
                total += chunk.total_uncompressed_size
    return total


def batch_rows(path: Path, columns: Iterable[str] | None = None) -> int:
    rows = pq.read_metadata(path).num_rows
    per_row = max(uncompressed_bytes(path, columns) // max(rows, 1), 1)
    return max(1, min(ROW_GROUP_ROWS, BATCH_BYTES // per_row))


def batches(path: Path, columns: list[str] | None = None) -> Iterator[pa.RecordBatch]:
    source = pq.ParquetFile(path, pre_buffer=False)
    yield from source.iter_batches(
        batch_size=batch_rows(path, columns), columns=columns, use_threads=False
    )
