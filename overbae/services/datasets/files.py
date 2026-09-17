"""Files → rows, and the chunked upload that stages them.

Parsing yields plain dict rows: JSON stays nested (the store makes it a JSON
column); CSV/TSV cells are strings, numbers when every cell spells one exactly.
No row is skipped and no value is altered: a bad line, a row wider than the
header or a repeated column name is a ``FileError`` the caller shows.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
import re
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

ALLOWED_SUFFIXES = (".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".parquet")
CHUNK_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
JSON_ARRAY_MAX_BYTES = 256 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
UNSUPPORTED = "Use a CSV, TSV, JSON, JSONL or Parquet file."

# A transcript cell is far longer than the csv module's 128 KB default.
csv.field_size_limit(MAX_UPLOAD_BYTES)


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


def _iter_json(fh: io.TextIOBase) -> Iterator[dict[str, Any]]:
    """One JSON document, or JSON Lines under a ``.json`` name."""
    text = fh.read()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        if exc.msg != "Extra data":
            raise FileError(f"Not valid JSON: {exc}") from exc
        yield from _iter_jsonl(io.StringIO(text))
        return
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
    reader = csv.reader(fh, delimiter=_sniff_delimiter(head, delimiter))
    try:
        header = next(reader, None)
        if not header:
            raise FileError("The file has no header row.")
        header = _clean_names(header)
        for cells in reader:
            if not cells:
                continue
            if len(cells) > len(header):
                raise FileError(
                    f"Row {reader.line_num} has {len(cells)} cells; the header has "
                    f"{len(header)}. Quote a cell that holds the delimiter."
                )
            yield dict(zip(header, cells + [None] * (len(header) - len(cells)), strict=True))
    except csv.Error as exc:
        raise FileError(f"Row {reader.line_num}: {exc}") from exc


def _clean_names(names: list[Any]) -> list[str]:
    """Names are stripped, a blank name becomes ``column_N``, and two names
    that differ only by case are refused: the frame store matches names
    case-insensitively, so one of them would be lost."""
    clean = [str(name).strip() or f"column_{i}" for i, name in enumerate(names, start=1)]
    seen: set[str] = set()
    for name in clean:
        if name.lower() in seen:
            raise FileError(f"Column {name!r} appears twice. Rename one of them.")
        seen.add(name.lower())
    return clean


def _normalise_names(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw = list(dict.fromkeys(name for row in rows for name in row))
    names = dict(zip(raw, _clean_names(raw), strict=True))
    if all(k == v for k, v in names.items()):
        return rows
    return [{names[k]: v for k, v in row.items()} for row in rows]


_INT = re.compile(r"-?(0|[1-9]\d{0,17})")


def _number(text: str) -> int | float | None:
    """The number ``text`` spells exactly, or None. ``007``, ``1_000``, ``nan``
    and a 19-digit id stay text because a number would not print back the same."""
    if _INT.fullmatch(text):
        return int(text)
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and repr(value) == text else None


def _coerce_numeric_columns(rows: list[dict[str, Any]]) -> None:
    """CSV cells are strings; a column becomes numeric only when every filled
    cell is the same kind of number and none would change when written back."""
    for name in {k for row in rows for k in row}:
        filled = [row[name] for row in rows if row.get(name) not in (None, "")]
        parsed = [_number(v) for v in filled]
        if not parsed or None in parsed or len({type(v) for v in parsed}) > 1:
            continue
        it = iter(parsed)
        for row in rows:
            if name in row:
                row[name] = next(it) if row[name] not in (None, "") else None


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
        yield from _iter_json(fh)
    elif name.endswith(".tsv"):
        yield from _iter_delimited(fh, "\t")
    elif name.endswith(".csv"):
        yield from _iter_delimited(fh, ",")
    else:
        raise FileError(UNSUPPORTED)


def _text_rows(fh: io.TextIOBase, name: str) -> list[dict[str, Any]]:
    rows = _normalise_names(list(iter_stream_rows(fh, filename=name)))
    if name.endswith((".csv", ".tsv")):
        _coerce_numeric_columns(rows)
    return rows


def read_file_rows(path: Path, *, filename: str) -> list[dict[str, Any]]:
    name = (filename or "").lower()
    bare = name.removesuffix(".gz")
    if bare.endswith(".parquet"):
        try:
            return _normalise_names(pq.read_table(path).to_pylist())
        except (pa.ArrowException, OSError) as exc:
            raise FileError("The file is not readable Parquet.") from exc
    if bare.endswith(".json") and os.path.getsize(path) > JSON_ARRAY_MAX_BYTES:
        raise FileError(
            f"A .json file is read whole and capped at {JSON_ARRAY_MAX_BYTES // 1024**2} MB "
            "— use JSONL for larger files."
        )
    try:
        with _open_text(path, name) as fh:
            return _text_rows(fh, bare)
    except UnicodeDecodeError as exc:
        raise FileError("The file is not UTF-8. Save it as UTF-8 and upload it again.") from exc
    except (gzip.BadGzipFile, EOFError) as exc:
        raise FileError("The file is not readable gzip.") from exc


def parse_text(text: str, *, filename: str = "") -> list[dict[str, Any]]:
    """Pasted rows: JSON or JSON Lines when the text opens with a bracket, else CSV."""
    body = text.strip()
    if not body:
        raise FileError("Nothing to read.")
    if not filename:
        filename = "paste.json" if body[:1] in "[{" else "paste.csv"
    return _text_rows(io.StringIO(body), filename.lower())


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
    lowered = safe.lower()
    if lowered.endswith(".parquet.gz"):
        raise FileError("Parquet is already compressed. Upload the .parquet file.")
    if not lowered.removesuffix(".gz").endswith(ALLOWED_SUFFIXES):
        raise FileError(UNSUPPORTED)
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
