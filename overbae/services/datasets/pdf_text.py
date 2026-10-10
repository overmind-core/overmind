from importlib.metadata import version
from pathlib import Path
from unicodedata import category

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw


def recover_missing_pages(path: Path, pages: list[int]) -> tuple[list[dict], dict | None]:
    rows = []
    recovered = []
    engine = {"method": "pdfium-native-text", "version": version("pypdfium2")}
    with pdfium.PdfDocument(path) as document:
        for number in pages:
            page = document[number - 1]
            textpage = page.get_textpage()
            try:
                before = len(rows)
                for index, (text, boxes) in enumerate(text_lines(textpage)):
                    regions = [{"page": number, "bbox": display_box(page, box)} for box in boxes]
                    rows.append(
                        {
                            "page": number,
                            "text": text,
                            "element": f"pdfium:{number}:{index}",
                            "regions": regions,
                            "extraction": engine,
                        }
                    )
                if len(rows) > before:
                    recovered.append(number)
            finally:
                textpage.close()
                page.close()
    metadata = {
        **engine,
        "pages": recovered,
        "reason": "primary_parser_returned_no_text",
        "rows": len(rows),
        "characters": sum(len(row["text"]) for row in rows),
        "control_characters": sum(
            category(char) == "Cc" and not char.isspace() for row in rows for char in row["text"]
        ),
    }
    return rows, metadata if recovered else None


def text_lines(textpage):
    text, boxes = [], []
    for index in range(textpage.count_chars()):
        code = pdfium_raw.FPDFText_GetUnicode(textpage, index)
        if not code:
            continue
        char = chr(code)
        if char in "\r\n\u2028\u2029":
            if "".join(text).strip():
                yield "".join(text), merge_boxes(boxes)
            text, boxes = [], []
            continue
        text.append(char)
        if not char.isspace():
            box = textpage.get_charbox(index)
            if box[0] < box[2] and box[1] < box[3]:
                boxes.append(box)
    if "".join(text).strip():
        yield "".join(text), merge_boxes(boxes)


def merge_boxes(boxes):
    # Preserve separated columns/labels rather than masking the whitespace between them for OCR.
    merged = []
    for left, bottom, right, top in boxes:
        if merged:
            prior = merged[-1]
            if (
                abs(bottom - prior[1]) <= (top - bottom) / 2
                and 0 <= left - prior[2] <= top - bottom
            ):
                merged[-1] = (prior[0], min(bottom, prior[1]), right, max(top, prior[3]))
                continue
        merged.append((left, bottom, right, top))
    return merged


def display_box(page, box):
    left, bottom, right, top = page.get_bbox()
    width, height = right - left, top - bottom
    rotation = page.get_rotation()
    points = []
    for x, y in ((box[0], box[1]), (box[2], box[3])):
        x, y = x - left, y - bottom
        points.append(
            {
                0: (x, height - y),
                90: (y, x),
                180: (width - x, y),
                270: (height - y, width - x),
            }[rotation]
        )
    return {
        "l": min(point[0] for point in points),
        "t": min(point[1] for point in points),
        "r": max(point[0] for point in points),
        "b": max(point[1] for point in points),
        "coord_origin": "TOPLEFT",
    }
