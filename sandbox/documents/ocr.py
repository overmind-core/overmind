from __future__ import annotations

import csv
import hashlib
import io
import math
import multiprocessing
import os
import re
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

import pypdfium2 as pdfium
from docling_core.types.doc import BoundingBox, DoclingDocument
from PIL import Image, ImageDraw, ImageOps

from sandbox.documents import checkpoints
from sandbox.documents.languages import (
    LANGUAGES,
    POLICY_VERSION,
    SCRIPT_LANGUAGES,
    validate_languages,
)

PAGE_TIMEOUT = 60
DOCUMENT_TIMEOUT = 1200
MAX_PIXELS = 16_000_000
MAX_EDGE = 8000
MAX_INPUT_PIXELS = 64_000_000
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


class OcrError(ValueError):
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
            "OCR is unavailable on this worker. Install Tesseract with the supported language and orientation data. "
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


def _engine(languages):
    languages = validate_languages(languages or [])
    listing = _tesseract(["--list-langs"], timeout=10)
    available = set(listing.splitlines()[1:])
    required = languages or ["eng", "osd"]
    if any(language not in available for language in required):
        raise OcrError("The requested OCR language data is unavailable on this worker.")
    match = re.search(r'"([^"\n]+)"', listing.splitlines()[0])
    if not match:
        raise OcrError("The OCR language data identity could not be verified.")
    traineddata = {}
    for language in sorted(available & (set(LANGUAGES.values()) | {"osd"})):
        with (Path(match[1]) / f"{language}.traineddata").open("rb") as source:
            traineddata[language] = hashlib.file_digest(source, "sha256").hexdigest()
    return {
        "engine": "tesseract",
        "version": _tesseract(["--version"], timeout=10).splitlines()[0].removeprefix("tesseract "),
        "requested_languages": languages,
        "traineddata": traineddata,
    }


def _recipe(path, engine, kind):
    with path.open("rb") as source:
        document = hashlib.file_digest(source, "sha256").hexdigest()
    return checkpoints.digest(
        {
            "document": document,
            "engine": engine,
            "kind": kind,
            "extraction_version": 2,
            "language_policy": POLICY_VERSION,
            "langdetect": version("langdetect"),
            "docling": version("docling-slim"),
            "pdfium": version("pypdfium2"),
            "pillow": version("pillow"),
            "max_pixels": MAX_PIXELS,
            "max_edge": MAX_EDGE,
            "scale": 3,
            "psm": 1,
            "mask_padding": 1,
        }
    )


def _recognize(path, engine, timeout):
    from langdetect import DetectorFactory, LangDetectException, detect_langs

    deadline = time.monotonic() + timeout
    selected = engine["requested_languages"]
    detection = {
        "mode": "explicit" if selected else "automatic",
        "language": None,
        "confidence": None,
        "status": "explicit" if selected else "uncertain",
    }
    available = set(engine["traineddata"])
    if not selected:
        try:
            osd = _tesseract([str(path), "stdout", "--psm", "0"], timeout=min(10, timeout))
        except OcrError:
            osd = ""
        script = re.search(r"^Script: (.+)$", osd, re.MULTILINE)
        script = script[1].strip() if script else None
        confidence = re.search(r"^Script confidence: ([0-9.]+)$", osd, re.MULTILINE)
        confidence = float(confidence[1]) if confidence else None
        detection.update(script=script, script_confidence=confidence)
        # Tesseract's ScriptDetector accepts script evidence only above this threshold.
        if confidence is None or confidence <= 1:
            script = None
        if script and script not in SCRIPT_LANGUAGES:
            detection["fallback_reason"] = "unsupported_script"
        selected = [x for x in SCRIPT_LANGUAGES.get(script, ["eng"]) if x in available]
        if not selected:
            raise OcrError("Language data for the detected script is unavailable on this worker.")

    def recognize(languages):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OcrError(
                "OCR timed out. Completed pages remain reusable; no partial source was landed."
            )
        return _tesseract(
            [str(path), "stdout", "-l", "+".join(languages), "--psm", "1", "tsv"], timeout=remaining
        )

    tsv = recognize(selected)
    if not engine["requested_languages"]:
        text = " ".join(
            word["text"]
            for word in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
            if word.get("level") == "5" and float(word["conf"]) >= 40
        )
        if len(text.strip()) >= 80:
            try:
                DetectorFactory.seed = 0
                detected = detect_langs(text)[0]
                detection.update(language=detected.lang, confidence=round(detected.prob, 4))
                language = LANGUAGES.get(detected.lang)
                if detected.prob >= 0.9 and language not in available:
                    detection["status"] = (
                        "unsupported_language" if language is None else "language_data_unavailable"
                    )
                if detected.prob >= 0.9 and language in available:
                    detection["status"] = "detected"
                    if language not in selected:
                        selected = [language, "eng"] if language != "eng" else [language]
                        tsv = recognize(selected)
            except LangDetectException:
                pass
    return tsv, {
        **engine,
        "traineddata": {
            key: value for key, value in engine["traineddata"].items() if key in {*selected, "osd"}
        },
        "languages": selected,
        "language_detection": detection,
    }


def _cached(cached, recipe, page):
    value = (cached or {}).get(page)
    return value["result"] if value and value["recipe_sha256"] == recipe else None


def _checkpoint(callback, recipe, page, result):
    if callback:
        callback({"ocr_checkpoint": checkpoints.record(recipe, page, result)})


def extract_image(
    path: Path, *, languages=None, cached=None, on_progress=None, checkpointing=True
) -> tuple[list[dict], dict]:
    engine = _engine(languages)
    recipe = _recipe(path, engine, "image")
    saved = _cached(cached, recipe, 1)
    if on_progress:
        on_progress(
            {
                "stage": "ocr",
                "pages_total": 1,
                "ocr_pages_total": 1,
                "ocr_pages_completed": int(saved is not None),
                "ocr_pages_reused": int(saved is not None),
            }
        )
    if saved is not None:
        return saved["rows"], {
            **saved["engine"],
            "pages": [1],
            "pages_reused": 1,
            "recipe_sha256": recipe,
        }
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
            **engine,
            "image": {
                "width": width,
                "height": height,
                "exif_orientation": orientation,
                "coordinate_space": "exif-oriented-pixels",
                "ocr_width": rendered_width,
                "ocr_height": rendered_height,
            },
        }
        tsv, engine = _recognize(image_path, engine, PAGE_TIMEOUT)
        rows = _lines(
            tsv,
            page=1,
            scale_x=width / rendered_width,
            scale_y=height / rendered_height,
            engine=engine,
        )
    if checkpointing:
        _checkpoint(on_progress, recipe, 1, {"rows": rows, "engine": engine})
    if on_progress:
        on_progress(
            {
                "stage": "ocr",
                "pages_total": 1,
                "ocr_pages_total": 1,
                "ocr_pages_completed": 1,
                "ocr_pages_reused": 0,
            }
        )
    return rows, {**engine, "pages": [1], "pages_reused": 0, "recipe_sha256": recipe}


def _pdf_page(path, number, dimensions, boxes, engine, deadline):
    with pdfium.PdfDocument(path) as pdf, TemporaryDirectory(prefix="workshop-ocr-") as tmp:
        page = pdf[number - 1]
        try:
            width, height = page.get_size()
            scale = min(3, math.sqrt(MAX_PIXELS / (width * height)), MAX_EDGE / max(width, height))
            bitmap = page.render(scale=scale)
            try:
                image = bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
        finally:
            page.close()
        try:
            scale_x, scale_y = dimensions[0] / image.width, dimensions[1] / image.height
            draw = ImageDraw.Draw(image)
            # Native glyphs are masked so OCR cannot duplicate or replace their text.
            for left, top, right, bottom in boxes:
                draw.rectangle(
                    (
                        (left - 1) / scale_x,
                        (top - 1) / scale_y,
                        (right + 1) / scale_x,
                        (bottom + 1) / scale_y,
                    ),
                    fill="white",
                )
            image_path = Path(tmp) / "page.png"
            image.save(image_path)
        finally:
            image.close()
        remaining = min(PAGE_TIMEOUT, deadline - time.monotonic())
        if remaining <= 0:
            raise OcrError("OCR timed out. No partial source was landed.")
        tsv, selected = _recognize(image_path, engine, remaining)
        return {
            "rows": _lines(tsv, page=number, scale_x=scale_x, scale_y=scale_y, engine=selected),
            "engine": selected,
        }


def worker_count(memory_bytes=None):
    memory = memory_bytes or 2 * 1024**3
    cpu = os.cpu_count() or 1
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            cpu = min(cpu, max(1, math.ceil(int(quota) / int(period))))
    except (OSError, ValueError):
        pass
    # Reserve the parser's working set; each worker owns its PDFium handle and raster.
    capacity = max(1, (memory - 768 * 1024**2) // (512 * 1024**2))
    return max(1, min(4, int(os.environ.get("SANDBOX_OCR_WORKERS", "2")), cpu, capacity))


def extract_pdf(
    path: Path,
    document: DoclingDocument,
    native_rows: list[dict],
    *,
    on_progress: Callable[[dict], None] | None = None,
    languages=None,
    cached=None,
    memory_bytes=None,
    checkpointing=True,
) -> tuple[list[dict], dict | None]:
    text_pages = {row["page"] for row in native_rows}
    image_pages = {ref.page_no for picture in document.pictures for ref in picture.prov}
    pages = sorted((set(document.pages) - text_pages) | image_pages)
    if not pages:
        return [], None
    started = time.monotonic()
    engine = _engine(languages)
    recipe = _recipe(path, engine, "pdf")
    results = {
        number: saved for number in pages if (saved := _cached(cached, recipe, number)) is not None
    }
    reused = len(results)
    workers = worker_count(memory_bytes)

    def progress():
        if on_progress:
            on_progress(
                {
                    "stage": "ocr",
                    "pages_total": len(document.pages),
                    "ocr_pages_total": len(pages),
                    "ocr_pages_completed": len(results),
                    "ocr_pages_reused": reused,
                    "ocr_workers": workers,
                }
            )

    progress()
    native_boxes = defaultdict(list)
    for row in native_rows:
        for ref in row["regions"]:
            box = BoundingBox.model_validate(ref["bbox"]).to_top_left_origin(
                page_height=document.pages[ref["page"]].size.height
            )
            native_boxes[ref["page"]].append((box.l, box.t, box.r, box.b))
    remaining = iter(number for number in pages if number not in results)
    deadline = time.monotonic() + DOCUMENT_TIMEOUT
    try:
        # PDFium is not thread-safe; each spawned process owns every native handle it uses.
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            pending = {}

            def submit():
                number = next(remaining, None)
                if number is None:
                    return
                size = document.pages[number].size
                future = pool.submit(
                    _pdf_page,
                    path,
                    number,
                    (size.width, size.height),
                    native_boxes[number],
                    engine,
                    deadline,
                )
                pending[future] = number

            for _ in range(workers):
                submit()
            while pending:
                done, _ = wait(
                    pending,
                    timeout=max(0, deadline - time.monotonic()),
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    raise OcrError("OCR timed out. No partial source was landed.")
                for future in done:
                    number = pending.pop(future)
                    result = future.result()
                    if checkpointing:
                        _checkpoint(on_progress, recipe, number, result)
                    results[number] = result
                    progress()
                    submit()
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError(
            "The PDF could not be read for OCR. Completed pages remain reusable; no partial source was landed."
        ) from exc
    languages_used = sorted(
        {lang for result in results.values() for lang in result["engine"]["languages"]}
    )
    return [row for number in pages for row in results[number]["rows"]], {
        **engine,
        "languages": languages_used,
        "pages": pages,
        "pages_reused": reused,
        "workers": workers,
        "seconds": round(time.monotonic() - started, 3),
        "pages_processed": len(pages) - reused,
        "recipe_sha256": recipe,
        "language_detection": {
            str(number): results[number]["engine"]["language_detection"] for number in pages
        },
    }
