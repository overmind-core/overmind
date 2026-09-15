"""Files → rows, and the chunked upload that stages them.

Parsing yields plain dict rows: JSON stays nested (the store makes it a JSON
column); CSV/TSV cells are strings, numbers when every cell parses as one.
Nothing is rejected — a bad line is an error the caller shows, not a skipped row.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import re
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

ALLOWED_SUFFIXES = (".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".parquet")
CHUNK_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
JSON_ARRAY_MAX_BYTES = 256 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class FileError(ValueError):
    """A parse or upload problem the user can act on."""


def _iter_jsonl(fh: io.TextIOBase) -> Iterator[dict[str, Any]]:
    for lineno, line in enumerate(fh, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise FileError(f"Line {lineno} is not valid JSON: {exc}") from exc
        yield value if isinstance(value, dict) else {"value": value}


def _iter_json_array(fh: io.TextIOBase) -> Iterator[dict[str, Any]]:
    try:
        value = json.load(fh)
    except ValueError as exc:
        raise FileError(f"Not valid JSON: {exc}") from exc
    if isinstance(value, dict):
        # {"data": [...]} / {"rows": [...]} wrappers are common exports.
        for key in ("data", "rows", "items", "examples"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
        else:
            value = [value]
    if not isinstance(value, list):
        raise FileError("A JSON file must hold an object or an array of objects.")
    for item in value:
        yield item if isinstance(item, dict) else {"value": item}


def _sniff_delimiter(sample: str, fallback: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        return fallback


def _iter_delimited(fh: io.TextIOBase, delimiter: str) -> Iterator[dict[str, Any]]:
    head = fh.read(64 * 1024)
    fh.seek(0)
    delimiter = _sniff_delimiter(head, delimiter)
    reader = csv.DictReader(fh, delimiter=delimiter)
    if not reader.fieldnames:
        raise FileError("The file has no header row.")
    for row in reader:
        yield {k: v for k, v in row.items() if k is not None}


def _coerce_numeric_columns(rows: list[dict[str, Any]]) -> None:
    """CSV cells are strings; a column that parses as a number on every filled
    cell becomes numeric, so the grid sorts and the agent sees the type."""
    if not rows:
        return
    names = {k for row in rows for k in row}
    for name in names:
        values = [row.get(name) for row in rows]
        filled = [v for v in values if v not in (None, "")]
        if not filled or not all(isinstance(v, str) for v in filled):
            continue
        parsed: list[Any] = []
        try:
            for v in filled:
                text = v.strip()
                parsed.append(int(text) if re.fullmatch(r"-?\d{1,18}", text) else float(text))
        except ValueError:
            continue
        it = iter(parsed)
        for row in rows:
            if row.get(name) not in (None, ""):
                row[name] = next(it)
            elif name in row:
                row[name] = None


def _open_text(path: Path, name: str) -> io.TextIOBase:
    if name.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8-sig", newline="")
    return open(path, encoding="utf-8-sig", newline="")


def iter_stream_rows(fh: io.TextIOBase, *, filename: str) -> Iterator[dict[str, Any]]:
    """Rows from an open text stream, keyed off the file extension."""
    name = (filename or "").lower().removesuffix(".gz")
    if name.endswith((".jsonl", ".ndjson")):
        yield from _iter_jsonl(fh)
    elif name.endswith(".json"):
        yield from _iter_json_array(fh)
    elif name.endswith(".tsv"):
        yield from _iter_delimited(fh, "\t")
    elif name.endswith(".csv"):
        yield from _iter_delimited(fh, ",")
    else:
        raise FileError("Use a CSV, TSV, JSON, JSONL or Parquet file.")


def read_file_rows(path: Path, *, filename: str) -> list[dict[str, Any]]:
    name = (filename or "").lower()
    bare = name.removesuffix(".gz")
    if bare.endswith(".parquet"):
        table = pq.read_table(path)
        return table.to_pylist()
    if bare.endswith(".json") and os.path.getsize(path) > JSON_ARRAY_MAX_BYTES:
        raise FileError(
            f"A .json file is read whole and capped at {JSON_ARRAY_MAX_BYTES // 1024**2} MB "
            "— use JSONL for larger files."
        )
    with _open_text(path, name) as fh:
        rows = list(iter_stream_rows(fh, filename=bare))
    if bare.endswith((".csv", ".tsv")):
        _coerce_numeric_columns(rows)
    return rows


def parse_text(text: str, *, filename: str = "") -> list[dict[str, Any]]:
    """Pasted rows: JSONL when every line is JSON, else JSON, else CSV."""
    body = text.strip()
    if not body:
        raise FileError("Nothing to read.")
    if not filename:
        first = body.lstrip()[:1]
        if first == "[" or (first == "{" and "\n" not in body.strip()):
            filename = "paste.json"
        elif first == "{":
            filename = "paste.jsonl"
        else:
            filename = "paste.csv"
    rows = list(iter_stream_rows(io.StringIO(body), filename=filename))
    if filename.endswith((".csv", ".tsv")):
        _coerce_numeric_columns(rows)
    return rows


def upload_dir(upload_id: Any) -> Path:
    from django.conf import settings

    return Path(settings.MEDIA_ROOT) / "uploads" / str(upload_id)


def upload_data_path(upload_id: Any) -> Path:
    return upload_dir(upload_id) / "data"


def safe_filename(name: str) -> str:
    base = os.path.basename(name or "").strip() or "upload"
    return _SAFE_NAME.sub("_", base)[:200]


def begin_upload(filename: str) -> tuple[str, str]:
    safe = safe_filename(filename)
    if not safe.lower().removesuffix(".gz").endswith(ALLOWED_SUFFIXES):
        raise FileError("Use a CSV, TSV, JSON, JSONL or Parquet file.")
    upload_id = str(uuid.uuid4())
    directory = upload_dir(upload_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "name").write_text(safe, encoding="utf-8")
    upload_data_path(upload_id).touch()
    return upload_id, safe


def upload_filename(upload_id: Any) -> str:
    try:
        return (upload_dir(upload_id) / "name").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def upload_received(upload_id: Any) -> int:
    try:
        return upload_data_path(upload_id).stat().st_size
    except OSError:
        return 0


def append_chunk(upload_id: Any, offset: int, chunk: bytes) -> int:
    """Idempotent on retry: a chunk whose range is already stored returns the size."""
    path = upload_data_path(upload_id)
    if not path.exists():
        raise FileError("This upload has expired. Start it again.")
    if offset + len(chunk) > MAX_UPLOAD_BYTES:
        raise FileError(f"Files are capped at {MAX_UPLOAD_BYTES // 1024**3} GB.")
    size = path.stat().st_size
    if offset == size:
        with path.open("ab") as fh:
            fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        return path.stat().st_size
    if offset < size and offset + len(chunk) <= size:
        return size
    raise FileError(f"Chunk starts at {offset} but {size} bytes are stored.")


def discard_upload(upload_id: Any) -> None:
    if upload_id:
        shutil.rmtree(upload_dir(upload_id), ignore_errors=True)
