"""Parquet row store with DuckDB queries."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import tempfile
import threading
from collections.abc import Iterable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from sandbox.tables import (
    ARROW_TYPES,
    ManifestBuilder,
    TableWriter,
    batches,
    coerce,
    infer_manifest,
    is_missing,
    read_manifest,
    union_kind,
)
from sandbox.tables import SOURCE_ROW as SOURCE_ROW
from sandbox.tables import json_dumps as json_dumps
from sandbox.tables import write_frame as write_frame

# DuckDB defaults to 80% of the machine for each connection.
DUCKDB_MEMORY = "512MB"
DUCKDB_THREADS = 2
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


def write_rows(
    path: Path, rows: Iterable[dict[str, Any]], manifest: list[dict[str, str]] | None = None
) -> list[dict[str, str]]:
    if manifest is None:
        rows = list(rows)
        manifest = infer_manifest(rows)
    with TableWriter(path, manifest) as writer:
        writer.write_rows(rows)
    return manifest


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_count(path: Path) -> int:
    return pq.read_metadata(path).num_rows


def _records(cur: Any) -> list[dict[str, Any]]:
    """Rows as dicts via Arrow: DuckDB's Python fetch needs pytz for tz-aware
    timestamps, Arrow's does not."""
    return cur.to_arrow_table().to_pylist()


def _decode(value: Any, kind: str) -> Any:
    if is_missing(value):
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


def value(raw: Any, kind: str) -> Any:
    if raw is None or kind != "json":
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def records(path: Path, columns: list[str] | None = None) -> Iterator[dict[str, Any]]:
    kinds = {spec["name"]: spec["type"] for spec in read_manifest(path)}
    for batch in batches(path, columns):
        for record in batch.to_pylist():
            yield {k: value(v, kinds.get(k, "string")) for k, v in record.items()}


class Frame:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.manifest = read_manifest(self.path)
        self.kinds = {spec["name"]: spec["type"] for spec in self.manifest}
        self.columns = [spec["name"] for spec in self.manifest]
        self.rows = row_count(self.path)

    def __len__(self) -> int:
        return self.rows

    def __contains__(self, column: object) -> bool:
        return column in self.kinds

    def values(self, column: str) -> Iterator[Any]:
        kind = self.kinds[column]
        for batch in batches(self.path, [column]):
            for item in batch.column(0).to_pylist():
                yield value(item, kind)

    def records(self, columns: list[str] | None = None) -> Iterator[dict[str, Any]]:
        wanted = None if columns is None else [c for c in columns if c in self.kinds]
        return records(self.path, wanted)

    def head(self, n: int, columns: list[str] | None = None) -> list[dict[str, Any]]:
        out = []
        for record in self.records(columns):
            if len(out) >= n:
                break
            out.append(record)
        return out


def iter_rows(path: Path) -> Iterator[dict[str, Any]]:
    kinds = {spec["name"]: spec["type"] for spec in read_manifest(path)}
    for batch in batches(path):
        for record in batch.to_pylist():
            yield {k: _decode(v, kinds.get(k, "string")) for k, v in record.items()}


def _take(path: Path, indices: list[int], columns: list[str] | None = None) -> pa.Table:
    source = pq.ParquetFile(path, pre_buffer=False)
    meta = source.metadata
    starts = [0]
    for g in range(meta.num_row_groups):
        starts.append(starts[-1] + meta.row_group(g).num_rows)
    wanted = [i for i in indices if 0 <= i < starts[-1]]
    if not wanted:
        empty = source.schema_arrow.empty_table()
        return empty.select(columns) if columns is not None else empty
    by_group: dict[int, list[int]] = {}
    for i in wanted:
        by_group.setdefault(bisect.bisect_right(starts, i) - 1, []).append(i)
    rows: dict[int, pa.Table] = {}
    for group, members in by_group.items():
        table = source.read_row_group(group, columns=columns, use_threads=False)
        picked = table.take(pa.array([i - starts[group] for i in members], type=pa.int64()))
        for offset, i in enumerate(members):
            rows[i] = picked.slice(offset, 1)
    return pa.concat_tables([rows[i] for i in wanted])


def read_rows(path: Path, indices: list[int]) -> list[dict[str, Any]]:
    """Rows by position; the store has no other row identity."""
    if not indices:
        return []
    kinds = {spec["name"]: spec["type"] for spec in read_manifest(path)}
    picked = _take(path, indices).to_pylist()
    return [{k: _decode(v, kinds.get(k, "string")) for k, v in rec.items()} for rec in picked]


def read_row(path: Path, index: int) -> dict[str, Any] | None:
    rows = read_rows(path, [index])
    return rows[0] if rows else None


def head(path: Path, n: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in iter_rows(path):
        if len(out) >= n:
            break
        out.append(row)
    return out


def frame_rows(
    path: Path, indices: list[int] | range, columns: list[str] | None = None
) -> pd.DataFrame:
    df = _take(path, list(indices), columns).to_pandas()
    for spec in read_manifest(path):
        if spec["type"] == "json" and spec["name"] in df.columns:
            df[spec["name"]] = df[spec["name"]].map(lambda v: _decode(v, "json"))
    return df


def write_extended(source: Path, rows: list[dict[str, Any]], path: Path) -> None:
    old = read_manifest(source)
    added = ManifestBuilder()
    for row in rows:
        added.add(row)
    known = {c["name"]: c["type"] for c in old}
    manifest = [
        {"name": c["name"], "type": union_kind({c["type"], *added.kinds.get(c["name"], ())})}
        for c in old
    ] + [c for c in added.manifest() if c["name"] not in known]
    with TableWriter(path, manifest) as writer:
        for batch in batches(source):
            arrays = []
            for spec in manifest:
                name, kind = spec["name"], spec["type"]
                if known.get(name) == kind:
                    arrays.append(batch.column(name))
                elif name in known:
                    stored = batch.column(name).to_pylist()
                    arrays.append(
                        pa.array(
                            [coerce(value(v, known[name]), kind) for v in stored],
                            type=ARROW_TYPES[kind],
                        )
                    )
                else:
                    arrays.append(pa.nulls(batch.num_rows, type=ARROW_TYPES[kind]))
            writer.write_table(pa.Table.from_arrays(arrays, schema=writer.schema))
        writer.write_rows(rows)


def connect(**tables: Path) -> duckdb.DuckDBPyConnection:
    """An in-memory connection with each ``name=path`` registered as a view."""
    spill = Path(tempfile.gettempdir()) / "overmind-duckdb"
    spill.mkdir(exist_ok=True)
    con = duckdb.connect(
        config={
            "memory_limit": DUCKDB_MEMORY,
            "threads": DUCKDB_THREADS,
            "temp_directory": str(spill),
        }
    )
    for name, path in tables.items():
        con.execute(f"CREATE VIEW {_quote(name)} AS SELECT * FROM {_scan(path)}")
    return con


def aligned(left: Path, right: Path, columns: list[str]) -> Iterator[dict[str, Any] | None]:
    """For each row of ``right`` in order, ``columns`` of the first row of ``left``
    with the same ``source_row``, or None."""
    kinds = {spec["name"]: spec["type"] for spec in read_manifest(left)}
    keys = {kinds.get(SOURCE_ROW), Frame(right).kinds.get(SOURCE_ROW)}
    if None in keys or (len(keys) > 1 and not keys <= {"integer", "number"}):
        yield from (None for _ in range(row_count(right)))
        return
    picked = "".join(f", l.{_quote(c)}" for c in columns)
    con = connect()
    try:
        cur = con.execute(
            f"SELECT l.file_row_number IS NOT NULL{picked} FROM {_scan(right, numbered=True)} r "
            "LEFT JOIN (SELECT source_row, min(file_row_number) AS first "
            f"FROM {_scan(left, numbered=True)} GROUP BY source_row) f "
            "ON r.source_row IS NOT DISTINCT FROM f.source_row "
            f"LEFT JOIN {_scan(left, numbered=True)} l ON l.file_row_number = f.first "
            "ORDER BY r.file_row_number"
        )
        for batch in cur.to_arrow_reader(1024):
            for found, *values in zip(*(c.to_pylist() for c in batch.columns), strict=True):
                yield (
                    {c: value(v, kinds[c]) for c, v in zip(columns, values, strict=True)}
                    if found
                    else None
                )
    finally:
        con.close()


def _scan(path: Path, *, numbered: bool = False) -> str:
    literal = str(path).replace("'", "''")
    return f"read_parquet('{literal}'{', file_row_number = true' if numbered else ''})"


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


def column_stats(path: Path, *, top: int = 5) -> list[dict[str, Any]]:
    manifest = read_manifest(path)
    con = connect(t=path)
    out: list[dict[str, Any]] = []
    try:
        n = con.execute('SELECT count(*) FROM "t"').fetchone()[0]
        for spec in manifest:
            name, kind = spec["name"], spec["type"]
            col = _quote(name)
            text = f"CAST({col} AS VARCHAR)"
            nulls, distinct = con.execute(
                f"SELECT count(*) FILTER (WHERE {col} IS NULL OR {text} = ''), "
                f"count(DISTINCT hash({text})) FILTER (WHERE {col} IS NOT NULL AND {text} <> '') "
                'FROM "t"'
            ).fetchone()
            entry: dict[str, Any] = {
                "name": name,
                "type": kind,
                "null_rate": (nulls / n) if n else 0.0,
                "distinct": int(distinct),
            }
            if kind in ("integer", "number"):
                mn, mx, mean = con.execute(
                    f'SELECT min({col}), max({col}), avg({col}) FROM "t"'
                ).fetchone()
                entry.update({"min": mn, "max": mx, "mean": mean})
            elif kind in ("string", "json"):
                mean_len, max_len = con.execute(
                    f'SELECT avg(length({text})), max(length({text})) FROM "t"'
                ).fetchone()
                entry.update({"mean_len": mean_len, "max_len": max_len})
            if kind in ("string", "boolean", "integer") and 0 < distinct <= 200:
                rows = con.execute(
                    f'SELECT {text}, count(*) AS c FROM "t" WHERE {col} IS NOT NULL '
                    f"GROUP BY 1 ORDER BY c DESC LIMIT {int(top)}"
                ).fetchall()
                entry["top"] = [{"value": v, "count": int(c)} for v, c in rows]
            out.append(entry)
    finally:
        con.close()
    return out


def connect_sandboxed(**tables: Path) -> duckdb.DuckDBPyConnection:
    """Like ``connect``, with json columns typed JSON so a query's JSON results
    can be told from text."""
    con = connect()
    for name, path in tables.items():
        typed = ", ".join(
            f"CAST({_quote(spec['name'])} AS JSON) AS {_quote(spec['name'])}"
            for spec in read_manifest(path)
            if spec["type"] == "json"
        )
        replace = f" REPLACE ({typed})" if typed else ""
        con.execute(f"CREATE VIEW {_quote(name)} AS SELECT *{replace} FROM {_scan(path)}")
    allowed = ", ".join("'" + str(path).replace("'", "''") + "'" for path in tables.values())
    con.execute(f"SET allowed_paths = [{allowed}]")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


class QueryTimeoutError(StoreError):
    pass


def query(
    sql: str, *, limit: int | None = 200, timeout: float | None = None, **tables: Path
) -> dict[str, Any]:
    """Read-only SQL over registered tables. Returns rows, with JSON values decoded,
    and columns."""
    con = connect_sandboxed(**tables)
    sql = sql.strip().rstrip(";").strip()
    timer = threading.Timer(timeout, con.interrupt) if timeout else None
    try:
        if timer is not None:
            timer.start()
        cur = con.execute(f"SELECT * FROM ({sql}) LIMIT {int(limit)}" if limit else sql)
        cols = [d[0] for d in cur.description]
        kinds = {d[0]: "json" if str(d[1]) == "JSON" else "string" for d in cur.description}
        rows = [{k: value(v, kinds[k]) for k, v in row.items()} for row in _records(cur)]
    except duckdb.InterruptException as exc:
        raise QueryTimeoutError(f"The query ran longer than {timeout:g}s and was stopped.") from exc
    finally:
        if timer is not None:
            timer.cancel()
        con.close()
    return {"rows": [_json_safe(r) for r in rows], "columns": cols}


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        elif isinstance(v, (bytes, bytearray)):
            v = v.decode("utf-8", "replace")
        elif isinstance(v, float) and math.isnan(v):
            v = None
        elif isinstance(v, Decimal):
            # SUM over integers is a HUGEINT/DECIMAL; JSON would carry it as a string.
            v = int(v) if v == v.to_integral_value() else float(v)
        out[k] = v
    return out
