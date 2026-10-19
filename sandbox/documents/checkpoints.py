from __future__ import annotations

import hashlib
import json
from pathlib import Path

MAX_PAGE_BYTES = 900 * 1024
MAX_CACHE_BYTES = 128 * 1024**2


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read(path: Path | None) -> dict[int, dict]:
    if path is None or not path.exists():
        return {}
    if path.stat().st_size > MAX_CACHE_BYTES:
        raise ValueError("OCR checkpoints exceed the cache transfer limit.")
    pages = {}
    with path.open() as source:
        for line in source:
            if len(line.rstrip("\n").encode()) > MAX_PAGE_BYTES:
                raise ValueError("An OCR checkpoint exceeds the page limit.")
            page = json.loads(line)
            if page.get("result_sha256") == digest(page.get("result")):
                pages[page["page"]] = page
    return pages


def record(recipe: str, page: int, result: dict) -> dict:
    value = {
        "recipe_sha256": recipe,
        "page": page,
        "result": result,
        "result_sha256": digest(result),
    }
    if len(json.dumps(value).encode()) > MAX_PAGE_BYTES:
        raise ValueError(
            "Extracted OCR page exceeds the 900 KiB checkpoint limit. Split or resize the source."
        )
    return value
