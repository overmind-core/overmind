"""Stream a Parquet table out as JSONL or CSV."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from pathlib import Path

from overbae.services.datasets import store


def iter_jsonl(path: Path) -> Iterator[str]:
    for row in store.iter_rows(path):
        yield json.dumps(row, ensure_ascii=False, default=str) + "\n"


def iter_csv(path: Path) -> Iterator[str]:
    names = [c["name"] for c in store.read_manifest(path)]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(names)
    yield buf.getvalue()
    for row in store.iter_rows(path):
        buf.seek(0)
        buf.truncate(0)
        writer.writerow(
            [
                v
                if isinstance(v, str) or v is None
                else json.dumps(v, ensure_ascii=False, default=str)
                for v in (row.get(n) for n in names)
            ]
        )
        yield buf.getvalue()


def stream(path: Path, fmt: str) -> tuple[Iterator[str], str, str]:
    """``(chunks, content_type, extension)``."""
    if fmt == "csv":
        return iter_csv(path), "text/csv", "csv"
    return iter_jsonl(path), "application/x-ndjson", "jsonl"
