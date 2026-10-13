from __future__ import annotations

import io
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path

import pypdfium2 as pdfium
from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
from docling.datamodel.pipeline_options import NativePdfPipelineOptions
from docling.document_converter import DocumentConverter, NativePdfFormatOption

from overbae.core.errors import InputValidationError
from overbae.services.datasets import ocr, pdf_text, store

SUFFIXES = (".pdf", ".docx", ".md", ".txt", *ocr.IMAGE_SUFFIXES)
MAX_BYTES = 100 * 1024 * 1024
MAX_PAGES = 2000


class DocumentError(InputValidationError):
    pass


def extract(
    path: Path, *, filename: str, on_progress: Callable[[dict], None] | None = None
) -> tuple[list[dict], dict]:
    if path.stat().st_size > MAX_BYTES:
        raise DocumentError("Documents are capped at 100 MiB (104857600 bytes).")
    identity = store.file_sha256(path)
    suffix = Path(filename).suffix.lower()
    rows = []
    limitations = []
    ocr_metadata = None
    native_recovery = None
    if suffix == ".pdf":
        try:
            with pdfium.PdfDocument(path) as pdf:
                page_count = len(pdf)
        except pdfium.PdfiumError as exc:
            raise DocumentError(
                "The PDF could not be read. Check that it is readable and not password protected."
            ) from exc
        if page_count > MAX_PAGES:
            raise DocumentError(
                f"The PDF has {page_count} pages; the limit is {MAX_PAGES}. Split it into smaller PDFs."
            )
        if on_progress:
            on_progress({"stage": "extracting_native_text", "pages_total": page_count})
    if suffix in ocr.IMAGE_SUFFIXES:
        try:
            rows, ocr_metadata = ocr.extract_image(path)
        except ocr.OcrError as exc:
            raise DocumentError(str(exc)) from exc
        method, parser_version, pages = "tesseract-ocr", ocr_metadata["version"], 1
        limitations.extend(
            [
                "OCR uses English language data and may misread text.",
                "Reading order and visual table structure are not reconstructed.",
            ]
        )
    elif suffix == ".txt":
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentError(
                "The document is not UTF-8. Save it as UTF-8 and upload it again."
            ) from exc
        for number, paragraph in enumerate(text.split("\n\n")):
            if paragraph.strip():
                rows.append({"text": paragraph, "element": f"paragraph:{number + 1}", "page": None})
        method, parser_version, pages = "utf8-paragraphs", "1", None
    else:
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.MD],
            format_options={
                InputFormat.PDF: NativePdfFormatOption(
                    pipeline_options=NativePdfPipelineOptions(
                        document_timeout=300,
                        parser_threads=2,
                        generate_page_images=False,
                        generate_picture_images=False,
                    )
                )
            },
        )
        try:
            converted = converter.convert(
                DocumentStream(name=filename, stream=io.BytesIO(path.read_bytes())),
                max_num_pages=MAX_PAGES,
                max_file_size=MAX_BYTES,
            )
        except Exception as exc:
            raise DocumentError(
                "The document could not be extracted. Check that it is readable and not password protected."
            ) from exc
        if converted.status != ConversionStatus.SUCCESS:
            raise DocumentError("Document extraction did not finish. No partial source was landed.")
        document = converted.document
        for item, _level in document.iterate_items():
            text = getattr(item, "text", "")
            if not text and getattr(item, "label", "") == "table":
                text = item.export_to_markdown(doc=document)
            if not text.strip():
                continue
            refs = [
                {"page": ref.page_no, "bbox": ref.bbox.model_dump(mode="json")} for ref in item.prov
            ]
            rows.append(
                {
                    "text": text,
                    "element": item.self_ref,
                    "page": refs[0]["page"] if refs else None,
                    "regions": refs,
                }
            )
        pages = len(document.pages) or None
        method = "docling-native-pdf" if suffix == ".pdf" else "docling"
        parser_version = version("docling-slim")
        if suffix == ".pdf":
            for row in rows:
                row["extraction"] = {"method": method, "version": parser_version}
            missing_native = sorted(set(document.pages) - {row["page"] for row in rows})
            if missing_native:
                if on_progress:
                    on_progress({"stage": "recovering_native_text", "pages_total": pages})
                try:
                    recovered, native_recovery = pdf_text.recover_missing_pages(
                        path, missing_native
                    )
                except (pdfium.PdfiumError, ValueError) as exc:
                    raise DocumentError(
                        "Native PDF text recovery failed. No partial source was landed."
                    ) from exc
                rows.extend(recovered)
                if native_recovery:
                    method += "+pdfium-native-text"
                    limitations.append(
                        "Recovered PDF text layers are retained without visual or reading-order verification."
                    )
                    if native_recovery["control_characters"]:
                        limitations.append(
                            "Recovered text contains non-whitespace control characters; review font-encoding artifacts before use."
                        )
            try:
                ocr_rows, ocr_metadata = ocr.extract_pdf(
                    path, document, rows, on_progress=on_progress
                )
            except ocr.OcrError as exc:
                raise DocumentError(str(exc)) from exc
            rows.extend(ocr_rows)
            rows.sort(key=lambda row: row["page"] or 0)
            limitations.append("Reading order and visual table structure are not reconstructed.")
            if ocr_metadata:
                method += "+tesseract-ocr"
                limitations.append("OCR uses English language data and may misread text.")
            missing = sorted(set(document.pages) - {row["page"] for row in rows})
            if missing:
                limitations.append("Pages without extracted text: " + ", ".join(map(str, missing)))
    if not rows:
        if ocr_metadata:
            raise DocumentError(
                "No text was found after OCR. Check that the document contains legible text."
            )
        raise DocumentError(
            "No text was extracted. Check that the document contains readable text."
        )
    for row in rows:
        evidence = {
            "document_id": identity,
            "filename": filename,
            "element": row.pop("element"),
            "page": row["page"],
            "regions": row.pop("regions", []),
        }
        row.update(_overmind_document_id=identity, source_name=filename)
        row["_overmind_provenance"] = {
            "evidence": [evidence],
            "extraction": row.pop("extraction", {"method": method, "version": parser_version}),
        }
    return rows, {
        "method": method,
        "version": parser_version,
        "pages": pages,
        "limitations": limitations,
        **({"ocr": ocr_metadata} if ocr_metadata else {}),
        **({"native_text_recovery": native_recovery} if native_recovery else {}),
    }
