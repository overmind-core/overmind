from __future__ import annotations

import csv
import io
import math
import os
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

import pypdfium2 as pdfium
from docling_core.types.doc import BoundingBox, DoclingDocument
from PIL import Image, ImageDraw, ImageOps

from overbae.core.errors import InputValidationError

PAGE_TIMEOUT = 60
DOCUMENT_TIMEOUT = 1200
MAX_PIXELS = 16_000_000
MAX_EDGE = 8000
MAX_INPUT_PIXELS = 64_000_000
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


class OcrError(InputValidationError):
    pass


def _tesseract(arguments: list[str], *, timeout: float) -> str:
    try:
        return subprocess.run(
            ["tesseract", *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=timeout,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        ).stdout
    except FileNotFoundError as exc:
        raise OcrError(
            "OCR is unavailable on this worker. Install Tesseract with English and orientation data. "
            "No partial source was landed."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrError(
            "OCR timed out. Split the document into smaller files and retry. "
            "No partial source was landed."
        ) from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OcrError(
            "OCR could not process this document. Check the worker's Tesseract installation "
            "and language data. No partial source was landed."
        ) from exc


def _lines(tsv: str, *, page: int, scale_x: float, scale_y: float, engine: dict) -> list[dict]:
    lines = defaultdict(list)
    for word in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if word["level"] == "5" and word["text"].strip():
            lines[(word["block_num"], word["par_num"], word["line_num"])].append(word)
    rows = []
    for key, words in lines.items():
        bbox = {
            "l": min(int(word["left"]) for word in words) * scale_x,
            "t": min(int(word["top"]) for word in words) * scale_y,
            "r": max(int(word["left"]) + int(word["width"]) for word in words) * scale_x,
            "b": max(int(word["top"]) + int(word["height"]) for word in words) * scale_y,
            "coord_origin": "TOPLEFT",
        }
        rows.append(
            {
                "text": " ".join(word["text"] for word in words),
                "element": f"ocr:{page}:{':'.join(key)}",
                "page": page,
                "regions": [{"page": page, "bbox": bbox}],
                "extraction": {
                    **engine,
                    "method": "tesseract-ocr",
                    "confidence": round(sum(float(word["conf"]) for word in words) / len(words), 2),
                },
            }
        )
    return rows


def extract_image(path: Path) -> tuple[list[dict], dict]:
    with TemporaryDirectory(prefix="workshop-ocr-") as tmp:
        image_path = Path(tmp) / "image.png"
        try:
            with Image.open(path) as original:
                if original.format not in {"PNG", "JPEG", "WEBP"}:
                    raise OcrError("Use a PNG, JPEG or WebP image.")
                if original.width * original.height > MAX_INPUT_PIXELS:
                    raise OcrError(
                        "Images are capped at 64 megapixels. Resize the image and retry."
                    )
                if getattr(original, "is_animated", False):
                    raise OcrError("Animated images are not supported. Upload a still image.")
                orientation = original.getexif().get(274, 1)
                with ImageOps.exif_transpose(original) as image:
                    width, height = image.size
                    scale = min(
                        1, math.sqrt(MAX_PIXELS / (width * height)), MAX_EDGE / max(width, height)
                    )
                    image.thumbnail(
                        (max(1, int(width * scale)), max(1, int(height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                    rendered_width, rendered_height = image.size
                    # Transparent screenshot pixels must not turn black during OCR.
                    with (
                        image.convert("RGBA") as rgba,
                        Image.new("RGB", image.size, "white") as rgb,
                    ):
                        with rgba.getchannel("A") as alpha:
                            rgb.paste(rgba, mask=alpha)
                        rgb.save(image_path)
        except OcrError:
            raise
        except Image.DecompressionBombError as exc:
            raise OcrError(
                "Images are capped at 64 megapixels. Resize the image and retry."
            ) from exc
        except (OSError, ValueError, SyntaxError) as exc:
            raise OcrError(
                "The image could not be read. Export it again and retry. No partial source was landed."
            ) from exc
        engine = {
            "engine": "tesseract",
            "version": _tesseract(["--version"], timeout=10)
            .splitlines()[0]
            .removeprefix("tesseract "),
            "languages": ["eng"],
            "image": {
                "width": width,
                "height": height,
                "exif_orientation": orientation,
                "coordinate_space": "exif-oriented-pixels",
                "ocr_width": rendered_width,
                "ocr_height": rendered_height,
            },
        }
        tsv = _tesseract(
            [str(image_path), "stdout", "-l", "eng", "--psm", "1", "tsv"],
            timeout=PAGE_TIMEOUT,
        )
        rows = _lines(
            tsv,
            page=1,
            scale_x=width / rendered_width,
            scale_y=height / rendered_height,
            engine=engine,
        )
    return rows, {**engine, "pages": [1]}


def extract_pdf(
    path: Path,
    document: DoclingDocument,
    native_rows: list[dict],
    *,
    on_progress: Callable[[dict], None] | None = None,
) -> tuple[list[dict], dict | None]:
    text_pages = {row["page"] for row in native_rows}
    image_pages = {ref.page_no for picture in document.pictures for ref in picture.prov}
    pages = sorted((set(document.pages) - text_pages) | image_pages)
    if not pages:
        return [], None

    def progress(completed):
        if on_progress:
            on_progress(
                {
                    "stage": "ocr",
                    "pages_total": len(document.pages),
                    "ocr_pages_total": len(pages),
                    "ocr_pages_completed": completed,
                }
            )

    progress(0)
    engine = {
        "engine": "tesseract",
        "version": _tesseract(["--version"], timeout=10).splitlines()[0].removeprefix("tesseract "),
        "languages": ["eng"],
    }
    native_boxes = defaultdict(list)
    for row in native_rows:
        for ref in row["regions"]:
            native_boxes[ref["page"]].append(
                BoundingBox.model_validate(ref["bbox"]).to_top_left_origin(
                    page_height=document.pages[ref["page"]].size.height
                )
            )
    rows = []
    deadline = time.monotonic() + DOCUMENT_TIMEOUT
    try:
        # Landing runs in the prefork batch worker: PDFium must not be shared across threads.
        with pdfium.PdfDocument(path) as pdf, TemporaryDirectory(prefix="workshop-ocr-") as tmp:
            image_path = Path(tmp) / "page.png"
            for completed, number in enumerate(pages, start=1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OcrError("OCR timed out. No partial source was landed.")
                page = pdf[number - 1]
                try:
                    width, height = page.get_size()
                    scale = min(
                        3, math.sqrt(MAX_PIXELS / (width * height)), MAX_EDGE / max(width, height)
                    )
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil().convert("RGB")
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                try:
                    size = document.pages[number].size
                    scale_x, scale_y = size.width / image.width, size.height / image.height
                    draw = ImageDraw.Draw(image)
                    # Mask native glyphs before recognition so OCR cannot duplicate or replace them.
                    for box in native_boxes[number]:
                        draw.rectangle(
                            (
                                (box.l - 1) / scale_x,
                                (box.t - 1) / scale_y,
                                (box.r + 1) / scale_x,
                                (box.b + 1) / scale_y,
                            ),
                            fill="white",
                        )
                    image.save(image_path)
                finally:
                    image.close()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OcrError("OCR timed out. No partial source was landed.")
                tsv = _tesseract(
                    [str(image_path), "stdout", "-l", "eng", "--psm", "1", "tsv"],
                    timeout=min(PAGE_TIMEOUT, remaining),
                )
                rows.extend(
                    _lines(tsv, page=number, scale_x=scale_x, scale_y=scale_y, engine=engine)
                )
                progress(completed)
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError("The PDF could not be read for OCR. No partial source was landed.") from exc
    return rows, {**engine, "pages": pages}
