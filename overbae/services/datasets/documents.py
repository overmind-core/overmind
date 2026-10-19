"""Transfer retained source bytes to the isolated document parser."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryFile

import httpx
from django.conf import settings

from overbae.core.errors import InputValidationError
from overbae.services.datasets import ocr_cache
from sandbox.documents.languages import validate_languages
from sandbox.protocol import FramingError, HeaderReader, header_bytes

SUFFIXES = (".pdf", ".docx", ".md", ".txt", ".png", ".jpg", ".jpeg", ".webp")
MAX_BYTES = 100 * 1024 * 1024
MAX_PAGES = 2000
MAX_OUTPUT_BYTES = 256 * 1024 * 1024


class DocumentError(InputValidationError):
    pass


def extract(
    path: Path,
    *,
    filename: str,
    on_progress: Callable[[dict], None] | None = None,
    project_id=None,
    ocr_languages=None,
) -> tuple[list[dict], dict]:
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise DocumentError("Documents are capped at 100 MiB (104857600 bytes).")
    if not settings.SANDBOX_URL or not settings.SANDBOX_TOKEN:
        raise DocumentError("The isolated document parser is not configured. No source was landed.")

    languages = validate_languages(ocr_languages or [])
    with path.open("rb") as source:
        identity = hashlib.file_digest(source, "sha256").hexdigest()

    def body(cache):
        yield header_bytes(
            {
                "mode": "extract",
                "filename": filename,
                "input_bytes": size,
                "ocr_languages": languages,
                "ocr_checkpoints": True,
                "cache_bytes": cache.tell(),
            }
        )
        cache.seek(0)
        while chunk := cache.read(64 * 1024):
            yield chunk
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                yield chunk

    reader = HeaderReader()
    try:
        with (
            TemporaryFile() as output,
            TemporaryFile() as cache,
        ):
            ocr_cache.export(project_id, identity, languages, cache)
            with (
                httpx.stream(
                    "POST",
                    f"{settings.SANDBOX_URL.rstrip('/')}/v1/run",
                    headers={"Authorization": f"Bearer {settings.SANDBOX_TOKEN}"},
                    content=body(cache),
                    timeout=httpx.Timeout(1500, connect=5),
                    follow_redirects=False,
                ) as response,
            ):
                if response.status_code != 200:
                    raise DocumentError(
                        f"The isolated document parser returned HTTP {response.status_code}. No source was landed."
                    )
                for chunk in response.iter_bytes():
                    pending = reader.feed(chunk)
                    while reader.header is not None and "progress" in reader.header:
                        progress = reader.header["progress"]
                        if isinstance(progress, dict) and "ocr_checkpoint" in progress:
                            ocr_cache.save(
                                project_id, identity, languages, progress["ocr_checkpoint"]
                            )
                        elif on_progress and isinstance(progress, dict):
                            on_progress(progress)
                        reader = HeaderReader()
                        pending = reader.feed(pending)
                    if output.tell() + len(pending) > MAX_OUTPUT_BYTES:
                        raise DocumentError("Extracted text exceeds the document output limit.")
                    output.write(pending)
                result = reader.finish()
                if not result.get("ok"):
                    raise DocumentError(result.get("error") or "Document extraction failed.")
                output.seek(0)
                rows = json.load(output)
                metadata = result.get("extraction")
                if (
                    not isinstance(rows, list)
                    or not rows
                    or not all(isinstance(row, dict) for row in rows)
                    or not isinstance(metadata, dict)
                ):
                    raise DocumentError("The document parser returned an invalid result.")
                return rows, metadata
    except DocumentError:
        raise
    except (httpx.TransportError, FramingError, ValueError) as exc:
        raise DocumentError(
            "The isolated document parser did not return a complete result. No source was landed."
        ) from exc
