"""Stream a Parquet table out as JSONL or CSV."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import AsyncIterator, Iterator
from itertools import islice
from pathlib import Path

from asgiref.sync import sync_to_async

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


_LINES_PER_CHUNK = 500


async def _batched(lines: Iterator[str]) -> AsyncIterator[str]:
    """Under ASGI Django drains a sync iterator into memory before the first
    byte; an async one streams, a batch of lines per thread hop."""
    while chunk := await sync_to_async(lambda: "".join(islice(lines, _LINES_PER_CHUNK)))():
        yield chunk


def stream(path: Path, fmt: str) -> tuple[AsyncIterator[str], str, str]:
    """``(chunks, content_type, extension)``."""
    if fmt == "csv":
        return _batched(iter_csv(path)), "text/csv", "csv"
    return _batched(iter_jsonl(path)), "application/x-ndjson", "jsonl"
