from __future__ import annotations

import csv
import io
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageOps

PAGE_SECONDS = 60
DOCUMENT_SECONDS = 1200
MAX_PIXELS = 16_000_000
MAX_EDGE = 8000
MAX_INPUT_PIXELS = 64_000_000
LANGUAGES = ["eng"]

UNAVAILABLE = (
    "Reading text from images is not available on this server. Install Tesseract with "
    "English and orientation data."
)
PAGE_TIMEOUT = "Reading text from the images took too long. Split the document into smaller files."
DOCUMENT_TIMEOUT = "Reading text from the images took too long. Retry the import."
FAILED = (
    "Text could not be read from this document's images. Check the server's Tesseract "
    "installation and language data."
)


class OcrError(ValueError):
    pass


def _tesseract(args: list[str], timeout: float) -> str:
    try:
        done = subprocess.run(
            ["tesseract", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=True,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        )
    except FileNotFoundError as exc:
        raise OcrError(UNAVAILABLE) from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrError(PAGE_TIMEOUT) from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OcrError(FAILED) from exc
    return done.stdout


def _engine() -> dict[str, Any]:
    first = _tesseract(["--version"], 10).splitlines()[0]
    return {
        "engine": "tesseract",
        "version": first.removeprefix("tesseract "),
        "languages": LANGUAGES,
    }


def _scaled(width: float, height: float, ceiling: float) -> float:
    return min(ceiling, math.sqrt(MAX_PIXELS / (width * height)), MAX_EDGE / max(width, height))


def _lines(
    png: Path, page: int, scale_x: float, scale_y: float, engine: dict, timeout: float
) -> list[dict[str, Any]]:
    tsv = _tesseract([str(png), "stdout", "-l", "+".join(LANGUAGES), "--psm", "1", "tsv"], timeout)
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for word in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if word.get("level") == "5" and (word.get("text") or "").strip():
            groups.setdefault((word["block_num"], word["par_num"], word["line_num"]), []).append(
                word
            )
    rows = []
    for (block, paragraph, line), words in groups.items():
        lefts = [int(w["left"]) for w in words]
        tops = [int(w["top"]) for w in words]
        rights = [int(w["left"]) + int(w["width"]) for w in words]
        bottoms = [int(w["top"]) + int(w["height"]) for w in words]
        bbox = {
            "l": min(lefts) * scale_x,
            "t": min(tops) * scale_y,
            "r": max(rights) * scale_x,
            "b": max(bottoms) * scale_y,
            "coord_origin": "TOPLEFT",
        }
        rows.append(
            {
                "text": " ".join(w["text"].strip() for w in words),
                "element": f"ocr:{page}:{block}:{paragraph}:{line}",
                "page": page,
                "regions": [{"page": page, "bbox": bbox}],
                "extraction": {
                    "method": "tesseract-ocr",
                    "version": engine["version"],
                    "confidence": round(sum(float(w["conf"]) for w in words) / len(words), 2),
                },
            }
        )
    return rows


def extract_image(path: Path) -> tuple[list[dict], dict]:
    try:
        with Image.open(path) as opened:
            if opened.format not in ("PNG", "JPEG", "WEBP"):
                raise OcrError("Use a PNG, JPEG or WebP image.")
            if opened.width * opened.height > MAX_INPUT_PIXELS:
                raise OcrError("Images are capped at 64 megapixels. Resize the image and retry.")
            if getattr(opened, "is_animated", False):
                raise OcrError("Animated images are not supported. Upload a still image.")
            image = ImageOps.exif_transpose(opened)
            image.load()
        width, height = image.size
        factor = min(1.0, _scaled(width, height, 1.0))
        if factor < 1:
            image.thumbnail((int(width * factor), int(height * factor)), Image.Resampling.LANCZOS)
        # Transparent pixels would otherwise turn black and hide dark text.
        canvas = Image.new("RGB", image.size, "white")
        rgba = image.convert("RGBA")
        canvas.paste(rgba, mask=rgba.getchannel("A"))
    except Image.DecompressionBombError as exc:
        raise OcrError("Images are capped at 64 megapixels. Resize the image and retry.") from exc
    except OcrError:
        raise
    except (OSError, ValueError, SyntaxError) as exc:
        raise OcrError("The image could not be read. Export it again and upload it.") from exc
    engine = _engine()
    with tempfile.TemporaryDirectory(prefix="ocr-") as scratch:
        png = Path(scratch) / "page.png"
        canvas.save(png)
        rows = _lines(png, 1, width / canvas.width, height / canvas.height, engine, PAGE_SECONDS)
    return rows, {**engine, "pages": [1]}


def _bottom_left_to_top(bbox: dict, height: float) -> tuple[float, float, float, float]:
    if bbox.get("coord_origin") == "TOPLEFT":
        return bbox["l"], bbox["t"], bbox["r"], bbox["b"]
    return bbox["l"], height - bbox["t"], bbox["r"], height - bbox["b"]


def extract_pdf(path: Path, document, native_rows: list[dict]) -> tuple[list[dict], dict | None]:
    """OCR for pages with no native text and pages that hold pictures. Native text is
    painted out first so it is not read twice."""
    pages = {number: page.size for number, page in document.pages.items()}
    with_text = {row["page"] for row in native_rows if row.get("page")}
    with_pictures = {prov.page_no for picture in document.pictures for prov in picture.prov}
    targets = sorted((set(pages) - with_text) | (with_pictures & set(pages)))
    if not targets:
        return [], None
    engine = _engine()
    deadline = time.monotonic() + DOCUMENT_SECONDS
    masks: dict[int, list[tuple[float, float, float, float]]] = {}
    for row in native_rows:
        for region in row.get("regions") or []:
            size = pages.get(region["page"])
            if size is not None:
                masks.setdefault(region["page"], []).append(
                    _bottom_left_to_top(region["bbox"], size.height)
                )
    rows: list[dict] = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        with tempfile.TemporaryDirectory(prefix="ocr-") as scratch:
            for number in targets:
                if time.monotonic() > deadline:
                    raise OcrError(DOCUMENT_TIMEOUT)
                size = pages[number]
                page = pdf[number - 1]
                try:
                    image = (
                        page.render(scale=_scaled(size.width, size.height, 3.0))
                        .to_pil()
                        .convert("RGB")
                    )
                finally:
                    page.close()
                scale_x, scale_y = size.width / image.width, size.height / image.height
                draw = ImageDraw.Draw(image)
                for left, top, right, bottom in masks.get(number, []):
                    draw.rectangle(
                        [
                            (left - 1) / scale_x,
                            (top - 1) / scale_y,
                            (right + 1) / scale_x,
                            (bottom + 1) / scale_y,
                        ],
                        fill="white",
                    )
                png = Path(scratch) / f"{number}.png"
                image.save(png)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OcrError(DOCUMENT_TIMEOUT)
                rows.extend(
                    _lines(png, number, scale_x, scale_y, engine, min(PAGE_SECONDS, remaining))
                )
    except OcrError:
        raise
    except Exception as exc:  # noqa: BLE001 — any renderer failure is one refusal
        raise OcrError("The PDF's pages could not be read as images.") from exc
    finally:
        pdf.close()
    return rows, {**engine, "pages": targets}
