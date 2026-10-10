"""Parquet row store with DuckDB queries.

Every table carries a column manifest ``[{name, type}]`` where ``type`` is one of
``string | integer | number | boolean | datetime | json``. Nested values are
and heterogeneous scalar values are stored as JSON text without coercion; readers
decode them back to Python objects. Sandboxed SQL views restore their declared JSON type.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import pickle
import tempfile
import threading
from collections.abc import Iterable
from itertools import batched
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from django.conf import settings

from overbae.services.datasets.statistics import StatisticsUnavailableError, profile

SOURCE_ROW = "source_row"
ROW_GROUP_SIZE = 10_000
_MANIFEST_KEY = b"overmind.columns"
_ARROW_TYPES = {
    "string": pa.string(),
    "integer": pa.int64(),
    "number": pa.float64(),
    "boolean": pa.bool_(),
    "datetime": pa.timestamp("us", tz="UTC"),
    "json": pa.string(),
}
_FILTER_OPS = (
    "contains",
    "not_contains",
    "equals",
    "not_equals",
    "empty",
    "not_empty",
    "gt",
    "gte",
    "lt",
    "lte",
)


class StoreError(ValueError):
    """A read or write the caller can act on (bad column, bad filter, missing file)."""


class QueryTimeoutError(StoreError):
    pass


class QuerySizeError(StoreError):
    pass


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return value is pd.NaT


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _coerce(value: Any, kind: str) -> Any:
    if _is_missing(value):
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
        if isinstance(value, dt.datetime):
            return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
        if isinstance(value, pd.Timestamp):
            ts = value.to_pydatetime()
            return ts if ts.tzinfo else ts.replace(tzinfo=dt.UTC)
        try:
            return pd.Timestamp(value).to_pydatetime().replace(tzinfo=dt.UTC)
        except (TypeError, ValueError):
            return None
    return value


def _infer_kind(values: list[Any]) -> str:
    builder = ManifestBuilder()
    for value in values:
        builder.add({"value": value})
    return builder.manifest()[0]["type"] if values else "string"


def infer_manifest(rows: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    builder = ManifestBuilder()
    for row in rows:
        builder.add(row)
    return builder.manifest()


class ManifestBuilder:
    def __init__(self):
        self.kinds = {}
        self.inexact_floats = set()

    def add(self, row):
        for name, value in row.items():
            name = str(name)
            kinds = self.kinds.setdefault(name, set())
            if _is_missing(value):
                continue
            if isinstance(value, (dict, list, tuple)):
                kinds.add("json")
            elif isinstance(value, bool):
                kinds.add("boolean")
            elif isinstance(value, int):
                if not -(2**63) <= value < 2**63:
                    kinds.add("json")
                else:
                    kinds.add("integer")
                    if int(float(value)) != value:
                        self.inexact_floats.add(name)
            elif isinstance(value, float):
                kinds.add("number")
            elif isinstance(value, (dt.datetime, pd.Timestamp)):
                kinds.add("datetime")
            else:
                kinds.add("string")

    def manifest(self):
        result = []
        for name, kinds in self.kinds.items():
            if "json" in kinds or ("number" in kinds and name in self.inexact_floats):
                kind = "json"
            elif kinds and kinds <= {"integer", "number"}:
                kind = "number" if "number" in kinds else "integer"
            else:
                kind = next(iter(kinds)) if len(kinds) == 1 else "json" if kinds else "string"
            result.append({"name": name, "type": kind})
        return result


def manifest_from_frame(df: pd.DataFrame) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for name in df.columns:
        series = df[name]
        if pd.api.types.is_bool_dtype(series):
            kind = "boolean"
        elif pd.api.types.is_integer_dtype(series):
            maximum = series.max()
            kind = "json" if pd.notna(maximum) and maximum >= 2**63 else "integer"
        elif pd.api.types.is_float_dtype(series):
            kind = "number"
        elif pd.api.types.is_datetime64_any_dtype(series):
            kind = "datetime"
        else:
            kind = _infer_kind(series.tolist())
        out.append({"name": str(name), "type": kind})
    return out


def _table_from_columns(columns: dict[str, list[Any]], manifest: list[dict[str, str]]) -> pa.Table:
    arrays = []
    fields = []
    for spec in manifest:
        name, kind = spec["name"], spec["type"]
        values = [_coerce(v, kind) for v in columns.get(name, [])]
        arrays.append(pa.array(values, type=_ARROW_TYPES[kind]))
        fields.append(pa.field(name, _ARROW_TYPES[kind]))
    schema = pa.schema(fields, metadata={_MANIFEST_KEY: json_dumps(manifest).encode()})
    return pa.Table.from_arrays(arrays, schema=schema)


def write_rows(
    path: Path, rows: Iterable[dict[str, Any]], manifest: list[dict[str, str]] | None = None
) -> list[dict[str, str]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.tmp")
    # Infer over every row before writing Arrow's fixed schema, without retaining the corpus.
    with tempfile.TemporaryFile(dir=path.parent) as spool:
        builder = ManifestBuilder()
        for batch in batched(rows, ROW_GROUP_SIZE, strict=False):
            if manifest is None:
                for row in batch:
                    builder.add(row)
            pickle.dump([dict(row) for row in batch], spool, protocol=pickle.HIGHEST_PROTOCOL)
        manifest = manifest if manifest is not None else builder.manifest()
        schema = _table_from_columns({c["name"]: [] for c in manifest}, manifest).schema
        spool.seek(0)
        try:
            with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
                while True:
                    try:
                        batch = pickle.load(spool)  # noqa: S301 — private spool written above
                    except EOFError:
                        break
                    columns = {c["name"]: [r.get(c["name"]) for r in batch] for c in manifest}
                    writer.write_table(_table_from_columns(columns, manifest))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return manifest


def write_frame(path: Path, df: pd.DataFrame) -> list[dict[str, str]]:
    manifest = manifest_from_frame(df)
    columns = {spec["name"]: df[spec["name"]].tolist() for spec in manifest}
    _write_table(path, _table_from_columns(columns, manifest))
    return manifest


def _write_table(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, row_group_size=ROW_GROUP_SIZE, compression="zstd")
    tmp.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(path: Path) -> list[dict[str, str]]:
    meta = pq.read_schema(path).metadata or {}
    raw = meta.get(_MANIFEST_KEY)
    if raw:
        return json.loads(raw)
    return [
        {"name": field.name, "type": _kind_of_arrow(field.type)} for field in pq.read_schema(path)
    ]


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


def row_count(path: Path) -> int:
    return pq.read_metadata(path).num_rows


def _records(cur: Any) -> list[dict[str, Any]]:
    """Rows as dicts via Arrow: DuckDB's Python fetch needs pytz for tz-aware
    timestamps, Arrow's does not."""
    return cur.to_arrow_table().to_pylist()


def _decode(value: Any, kind: str) -> Any:
    if _is_missing(value):
        return None
    if kind == "json":
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    if kind == "datetime":
        return value.isoformat() if hasattr(value, "isoformat") else value
    return value


def decode(value: Any, kind: str) -> Any:
    return _decode(value, kind)


def read_frame(path: Path) -> pd.DataFrame:
    """Pandas frame with JSON columns decoded to Python objects."""
    manifest = read_manifest(path)
    table = pq.read_table(path)
    df = table.to_pandas(integer_object_nulls=True)
    for spec in manifest:
        if spec["type"] == "json" and spec["name"] in df.columns:
            df[spec["name"]] = pd.Series(
                [_decode(v, "json") for v in df[spec["name"]]], dtype=object
            )
    return df


def iter_rows(path: Path, batch_size: int = ROW_GROUP_SIZE):
    """Decoded dict rows, streaming by row group."""
    manifest = read_manifest(path)
    kinds = {spec["name"]: spec["type"] for spec in manifest}
    pf = pq.ParquetFile(path)
    for batch in pf.iter_batches(batch_size=batch_size):
        for record in batch.to_pylist():
            yield {k: _decode(v, kinds.get(k, "string")) for k, v in record.items()}


def iter_frames(path: Path, batch_size: int = ROW_GROUP_SIZE):
    manifest = read_manifest(path)
    for batch in batched(iter_rows(path, batch_size), batch_size, strict=False):
        columns = {}
        for spec in manifest:
            values = [row.get(spec["name"]) for row in batch]
            preserve_scalars = spec["type"] == "json" or (
                spec["type"] == "integer" and any(value is None for value in values)
            )
            columns[spec["name"]] = pd.Series(values, dtype=object if preserve_scalars else None)
        yield pd.DataFrame(columns)


def read_rows(path: Path, indices: list[int]) -> list[dict[str, Any]]:
    """Rows by position; the store has no other row identity."""
    if not indices:
        return []
    manifest = read_manifest(path)
    kinds = {spec["name"]: spec["type"] for spec in manifest}
    parquet = pq.ParquetFile(path)
    wanted = {i for i in indices if 0 <= i < parquet.metadata.num_rows}
    picked = {}
    offset = 0
    for group in range(parquet.num_row_groups):
        count = parquet.metadata.row_group(group).num_rows
        selected = sorted(i for i in wanted if offset <= i < offset + count)
        if selected:
            table = parquet.read_row_group(group)
            records = table.take(
                pa.array([i - offset for i in selected], type=pa.int64())
            ).to_pylist()
            picked.update(zip(selected, records, strict=True))
        offset += count
    picked = [picked[i] for i in indices if i in picked]
    return [{k: _decode(v, kinds.get(k, "string")) for k, v in rec.items()} for rec in picked]


def read_row(path: Path, index: int) -> dict[str, Any] | None:
    rows = read_rows(path, [index])
    return rows[0] if rows else None


def head(path: Path, n: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in iter_rows(path, batch_size=min(n, ROW_GROUP_SIZE)):
        out.append(row)
        if len(out) >= n:
            break
    return out


def connect(**tables: Path) -> duckdb.DuckDBPyConnection:
    """An in-memory connection with each ``name=path`` registered as a view."""
    con = duckdb.connect(config={"memory_limit": "512MB", "threads": 2})
    for name, path in tables.items():
        literal = str(path).replace("'", "''")
        con.execute(f"CREATE VIEW \"{name}\" AS SELECT * FROM read_parquet('{literal}')")
    return con


def null_rates(path):
    names = [column["name"] for column in read_manifest(path)]
    if not names:
        return {}
    expressions = [
        f"avg(CASE WHEN {_quote(name)} IS NULL OR CAST({_quote(name)} AS VARCHAR) = '' "
        "THEN 1.0 ELSE 0.0 END)"
        for name in names
    ]
    with connect(t=path) as con:
        values = con.execute("SELECT " + ", ".join(expressions) + ' FROM "t"').fetchone()
    return dict(zip(names, (float(value or 0) for value in values), strict=True))


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _filter_sql(filters: list[dict[str, Any]], names: set[str]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for flt in filters or []:
        field = str(flt.get("field") or "")
        op = str(flt.get("op") or "contains")
        value = flt.get("value")
        if field not in names:
            raise StoreError(f"Unknown column {field!r}.")
        if op not in _FILTER_OPS:
            raise StoreError(f"Unknown filter {op!r}.")
        col = _quote(field)
        text = f"CAST({col} AS VARCHAR)"
        if op == "contains":
            clauses.append(f"{text} ILIKE ?")
            params.append(f"%{value}%")
        elif op == "not_contains":
            clauses.append(f"({col} IS NULL OR {text} NOT ILIKE ?)")
            params.append(f"%{value}%")
        elif op == "equals":
            clauses.append(f"{text} = ?")
            params.append(str(value))
        elif op == "not_equals":
            clauses.append(f"({col} IS NULL OR {text} <> ?)")
            params.append(str(value))
        elif op == "empty":
            clauses.append(f"({col} IS NULL OR {text} = '' OR {text} = '[]' OR {text} = '{{}}')")
        elif op == "not_empty":
            clauses.append(
                f"({col} IS NOT NULL AND {text} <> '' AND {text} <> '[]' AND {text} <> '{{}}')"
            )
        else:
            symbol = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
            clauses.append(f"TRY_CAST({col} AS DOUBLE) {symbol} ?")
            params.append(float(value))
    return (" AND ".join(clauses) if clauses else "TRUE"), params


def page(
    path: Path,
    *,
    offset: int = 0,
    limit: int = 50,
    sort: str | None = None,
    direction: str = "asc",
    filters: list[dict[str, Any]] | None = None,
    search: str = "",
) -> dict[str, Any]:
    """A page of decoded rows plus the matching total. ``_index`` is the row's
    position in the file — the identity consumers pin."""
    manifest = read_manifest(path)
    kinds = {spec["name"]: spec["type"] for spec in manifest}
    names = set(kinds)
    where, params = _filter_sql(filters or [], names)
    if search:
        columns = " || ' ' || ".join(f"COALESCE(CAST({_quote(n)} AS VARCHAR), '')" for n in kinds)
        where = f"({where}) AND ({columns}) ILIKE ?"
        params.append(f"%{search}%")
    order = '"_index" ASC'
    if sort:
        if sort not in names and sort != "_index":
            raise StoreError(f"Unknown column {sort!r}.")
        way = "DESC" if direction == "desc" else "ASC"
        if sort == "_index":
            order = f'"_index" {way}'
        elif kinds[sort] in ("integer", "number"):
            order = f"{_quote(sort)} {way} NULLS LAST"
        else:
            order = f"CAST({_quote(sort)} AS VARCHAR) {way} NULLS LAST"
    con = connect(t=path)
    try:
        base = 'SELECT (row_number() OVER ()) - 1 AS "_index", * FROM "t"'
        total = con.execute(f"SELECT count(*) FROM ({base}) WHERE {where}", params).fetchone()[0]
        cur = con.execute(
            f"SELECT * FROM ({base}) WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, int(limit), int(offset)],
        )
        rows = [
            {
                k: _decode(v, kinds.get(k, "integer" if k == "_index" else "string"))
                for k, v in row.items()
            }
            for row in _records(cur)
        ]
    finally:
        con.close()
    return {"rows": rows, "total": int(total), "columns": manifest}


def column_stats(path: Path, *, top: int = 5, fingerprint: str = "") -> list[dict[str, Any]]:
    try:
        return profile(path, read_manifest(path), fingerprint=fingerprint, top=top)
    except StatisticsUnavailableError as exc:
        raise StoreError(str(exc)) from exc


def connect_sandboxed(**tables: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(
        config={
            "memory_limit": "512MB",
            "threads": 2,
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "allow_community_extensions": False,
        }
    )
    con.execute("SET allowed_paths = ?", [[str(path.resolve()) for path in tables.values()]])
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    for name, path in tables.items():
        literal = str(path.resolve()).replace("'", "''")
        nested = [column["name"] for column in read_manifest(path) if column["type"] == "json"]
        projection = "*"
        if nested:
            replacements = ", ".join(
                f"CAST({_quote(column)} AS JSON) AS {_quote(column)}" for column in nested
            )
            projection += f" REPLACE ({replacements})"
        con.execute(
            f"CREATE VIEW {_quote(name)} AS SELECT {projection} FROM read_parquet('{literal}')"
        )
    return con


def query(
    sql: str,
    *,
    limit: int | None = 200,
    max_bytes: int | None = None,
    max_columns: int | None = None,
    **tables: Path,
) -> dict[str, Any]:
    """Read-only SQL over registered tables. Returns decoded rows and columns."""
    con = connect_sandboxed(**tables)
    sql = sql.strip().rstrip(";").strip()
    expired = threading.Event()

    def interrupt():
        expired.set()
        con.interrupt()

    timeout = settings.DATASET_QUERY_TIMEOUT_SECONDS
    timer = threading.Timer(timeout, interrupt)
    timer.daemon = True
    timer.start()
    try:
        statements = con.extract_statements(sql)
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            raise StoreError("Only one read-only SELECT query is allowed.")
        cur = con.execute(f"SELECT * FROM ({sql}) LIMIT {int(limit)}" if limit else sql)
        cols = [d[0] for d in cur.description]
        if max_columns is not None and len(cols) > max_columns:
            raise QuerySizeError(
                "The query exceeds its column budget. Select fewer columns or export the cell."
            )
        json_columns = {d[0] for d in cur.description if str(d[1]) == "JSON"}
        rows, size = [], 0
        for batch in cur.to_arrow_reader(batch_size=1):
            if expired.is_set():
                raise QueryTimeoutError(
                    f"The query exceeded {timeout:g} seconds. Narrow the query or export the cell."
                )
            for value in batch.to_pylist():
                row = _json_safe(
                    {
                        key: _decode(value, "json") if key in json_columns else value
                        for key, value in value.items()
                    }
                )
                size += len(json.dumps(row, default=str, ensure_ascii=False).encode())
                if max_bytes is not None and size > max_bytes:
                    raise QuerySizeError(
                        "The query exceeds its byte budget. Select fewer columns, aggregate or export the cell."
                    )
                rows.append(row)
    except duckdb.InterruptException as exc:
        raise QueryTimeoutError(
            f"The query exceeded {timeout:g} seconds. Narrow the query or export the cell."
        ) from exc
    finally:
        timer.cancel()
        timer.join()
        con.close()
    return {"rows": rows, "columns": cols}


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        elif isinstance(v, (bytes, bytearray)):
            v = v.decode("utf-8", "replace")
        elif isinstance(v, float) and math.isnan(v):
            v = None
        out[k] = v
    return out
