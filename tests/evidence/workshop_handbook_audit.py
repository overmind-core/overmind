import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium


def words(text):
    return Counter(re.findall(r"[a-z0-9]+", text.lower()))


def main(options):
    with options.rows.open() as source:
        rows = [json.loads(line) for line in source]
    identity = hashlib.file_digest(options.pdf.open("rb"), "sha256").hexdigest()
    report = {"pdf": str(options.pdf), "sha256": identity, "rows": len(rows), "pages": []}
    with pdfium.PdfDocument(options.pdf) as document:
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            try:
                native = textpage.get_text_range()
                bounds = page.get_size()
            finally:
                textpage.close()
                page.close()
            observed = [row for row in rows if row["page"] == index + 1]
            methods = Counter(
                row["_overmind_provenance"]["extraction"]["method"] for row in observed
            )
            extracted = "\n".join(row["text"] for row in observed)
            for row in observed:
                assert row["_overmind_document_id"] == identity
                for evidence in row["_overmind_provenance"]["evidence"]:
                    assert evidence["page"] == index + 1 and evidence["document_id"] == identity
                    for region in evidence["regions"]:
                        box = region["bbox"]
                        assert -1 <= box["l"] < box["r"] <= bounds[0] + 1, region
                        assert (
                            -1 <= min(box["t"], box["b"]) < max(box["t"], box["b"]) <= bounds[1] + 1
                        ), region
            expected_words, actual_words = words(native), words(extracted)
            missing = expected_words - actual_words
            report["pages"].append(
                {
                    "page": index + 1,
                    "size": bounds,
                    "native_chars": len(native),
                    "extracted_chars": len(extracted),
                    "rows": len(observed),
                    "methods": dict(methods),
                    "native_token_coverage": round(
                        sum((expected_words & actual_words).values())
                        / max(1, sum(expected_words.values())),
                        4,
                    ),
                    "missing_native_tokens": dict(missing),
                    "ocr_confidence": [
                        row["_overmind_provenance"]["extraction"]["confidence"]
                        for row in observed
                        if row["_overmind_provenance"]["extraction"]["method"] == "tesseract-ocr"
                    ],
                }
            )
    report["scope"] = (
        "All extracted row/page evidence and bounding boxes; native token comparison is diagnostic, not reading-order or OCR semantic certification. Visually inspect selected pages separately."
    )
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    main(parser.parse_args())
