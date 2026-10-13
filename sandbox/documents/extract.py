from __future__ import annotations

import hashlib
import io
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
from docling.document_converter import (
    DocumentConverter,
    NativePdfFormatOption,
    NativePdfPipelineOptions,
)
from docling_core.types.doc import DocItemLabel

from sandbox.documents import ocr, pdf_text

SUFFIXES = (".pdf", ".docx", ".md", ".txt", ".png", ".jpg", ".jpeg", ".webp")
IMAGES = (".png", ".jpg", ".jpeg", ".webp")
MAX_BYTES = 100 * 1024**2
MAX_PAGES = 2000

RECOVERED = (
    "Some pages were read from the PDF's hidden text layer; their order may differ from the page."
)
CONTROL = "Some text holds stray control characters from the PDF's fonts; check it before use."
READING_ORDER = "Tables and multi-column layouts may lose their structure."
OCR_READS = "Text read from images can contain errors. Only English is read."


class DocumentError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1024**2):
            digest.update(chunk)
    return digest.hexdigest()


def _paragraphs(path: Path) -> tuple[list[dict], dict]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentError(
            "The document is not UTF-8. Save it as UTF-8 and upload it again."
        ) from exc
    rows = [
        {"text": piece.strip(), "page": None, "element": f"paragraph:{index}"}
        for index, piece in enumerate(text.split("\n\n"), start=1)
        if piece.strip()
    ]
    return rows, {"method": "utf8-paragraphs", "version": "1", "pages": None, "limitations": []}


def _converter():
    options = NativePdfPipelineOptions(
        document_timeout=300,
        parser_threads=2,
        generate_page_images=False,
        generate_picture_images=False,
    )
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.MD],
        format_options={InputFormat.PDF: NativePdfFormatOption(pipeline_options=options)},
    )


def _docling(path: Path, filename: str):
    try:
        result = _converter().convert(
            DocumentStream(name=filename, stream=io.BytesIO(path.read_bytes())),
            max_num_pages=MAX_PAGES,
            max_file_size=MAX_BYTES,
        )
    except Exception as exc:  # noqa: BLE001 — every parser failure is one refusal
        raise DocumentError(
            "The document could not be extracted. Check that it is readable and not password "
            "protected."
        ) from exc
    if result.status != ConversionStatus.SUCCESS:
        raise DocumentError("Reading the document did not finish. Retry the import.")
    document = result.document
    rows = []
    for item, _level in document.iterate_items():
        text = getattr(item, "text", "") or ""
        if not text and item.label == DocItemLabel.TABLE:
            text = item.export_to_markdown(doc=document)
        if not text.strip():
            continue
        regions = [
            {"page": prov.page_no, "bbox": prov.bbox.model_dump(mode="json")}
            for prov in getattr(item, "prov", [])
        ]
        rows.append(
            {
                "text": text,
                "element": item.self_ref,
                "page": regions[0]["page"] if regions else None,
                "regions": regions,
            }
        )
    return document, rows


def _pdf(path: Path, filename: str) -> tuple[list[dict], dict]:
    try:
        with pdfium.PdfDocument(str(path)) as pdf:
            pages = len(pdf)
    except pdfium.PdfiumError as exc:
        raise DocumentError(
            "The PDF could not be read. Check that it is readable and not password protected."
        ) from exc
    if pages > MAX_PAGES:
        raise DocumentError(
            f"The PDF has {pages} pages; the limit is {MAX_PAGES}. Split it into smaller PDFs."
        )
    document, rows = _docling(path, filename)
    docling_version = version("docling-slim")
    for row in rows:
        row["extraction"] = {"method": "docling-native-pdf", "version": docling_version}
    method = ["docling-native-pdf"]
    limitations = []
    metadata: dict[str, Any] = {"version": docling_version, "pages": len(document.pages)}
    missing = sorted(set(document.pages) - {row["page"] for row in rows if row["page"]})
    if missing:
        try:
            recovered, recovery = pdf_text.recover_missing_pages(path, missing)
        except (pdfium.PdfiumError, ValueError) as exc:
            raise DocumentError(
                "The PDF's text layer could not be read. Retry the import."
            ) from exc
        if recovery is not None:
            rows.extend(recovered)
            method.append("pdfium-native-text")
            limitations.append(RECOVERED)
            if recovery["control_characters"]:
                limitations.append(CONTROL)
            metadata["native_text_recovery"] = recovery
    try:
        read, engine = ocr.extract_pdf(path, document, rows)
    except ocr.OcrError as exc:
        raise DocumentError(str(exc)) from exc
    rows.extend(read)
    rows.sort(key=lambda row: row["page"] or 0)
    limitations.append(READING_ORDER)
    if engine is not None:
        method.append("tesseract-ocr")
        limitations.append(OCR_READS)
        metadata["ocr"] = engine
    empty = sorted(set(document.pages) - {row["page"] for row in rows})
    if empty:
        limitations.append("Pages without extracted text: " + ", ".join(map(str, empty)))
    return rows, {"method": "+".join(method), **metadata, "limitations": limitations}


def _image(path: Path) -> tuple[list[dict], dict]:
    try:
        rows, engine = ocr.extract_image(path)
    except ocr.OcrError as exc:
        raise DocumentError(str(exc)) from exc
    return rows, {
        "method": "tesseract-ocr",
        "version": engine["version"],
        "pages": 1,
        "limitations": [OCR_READS, READING_ORDER],
        "ocr": engine,
    }


def extract(path: Path, filename: str) -> tuple[list[dict], dict]:
    """One row per text element, each carrying the file's sha256 and where it came from."""
    if path.stat().st_size > MAX_BYTES:
        raise DocumentError(f"Documents are capped at 100 MiB ({MAX_BYTES} bytes).")
    suffix = Path(filename).suffix.lower()
    if suffix not in SUFFIXES:
        raise DocumentError("Use a PDF, DOCX, Markdown, text, PNG, JPEG or WebP file.")
    document_id = _sha256(path)
    if suffix == ".txt":
        rows, metadata = _paragraphs(path)
    elif suffix in IMAGES:
        rows, metadata = _image(path)
    elif suffix == ".pdf":
        rows, metadata = _pdf(path, filename)
    else:
        document, rows = _docling(path, filename)
        metadata = {
            "method": "docling",
            "version": version("docling-slim"),
            "pages": len(document.pages) or None,
            "limitations": [],
        }
    if not rows:
        if "ocr" in metadata:
            raise DocumentError(
                "No text was found after OCR. Check that the document contains legible text."
            )
        raise DocumentError(
            "No text was extracted. Check that the document contains readable text."
        )
    fallback = {"method": metadata["method"], "version": metadata["version"]}
    finished = []
    for row in rows:
        evidence = {
            "document_id": document_id,
            "filename": filename,
            "element": row["element"],
            "page": row["page"],
            "regions": row.get("regions", []),
        }
        finished.append(
            {
                "text": row["text"],
                "page": row["page"],
                "source_name": filename,
                "_overmind_document_id": document_id,
                "_overmind_provenance": {
                    "evidence": [evidence],
                    "extraction": row.get("extraction", fallback),
                },
            }
        )
    return finished, {**metadata, "sha256": document_id}
