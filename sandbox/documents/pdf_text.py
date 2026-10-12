from __future__ import annotations

import unicodedata
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

_LINE_BREAKS = {"\r", "\n", " ", " "}


def _runs(boxes: list[tuple[float, float, float, float]]) -> list[list[float]]:
    """Adjacent glyph boxes merged into one box per run, so columns stay apart."""
    merged: list[list[float]] = []
    for left, bottom, right, top in boxes:
        height = top - bottom
        if merged:
            last = merged[-1]
            gap = left - last[2]
            if abs(bottom - last[1]) <= height / 2 and 0 <= gap <= height:
                last[1], last[2], last[3] = (
                    min(last[1], bottom),
                    max(last[2], right),
                    max(last[3], top),
                )
                continue
        merged.append([left, bottom, right, top])
    return merged


def _top_left(box: list[float], page) -> dict[str, Any]:
    origin_x, origin_y, end_x, end_y = page.get_bbox()
    width, height = end_x - origin_x, end_y - origin_y
    rotation = page.get_rotation()

    def turn(x: float, y: float) -> tuple[float, float]:
        x, y = x - origin_x, y - origin_y
        if rotation == 90:
            return y, x
        if rotation == 180:
            return width - x, y
        if rotation == 270:
            return height - y, width - x
        return x, height - y

    corners = [turn(box[0], box[1]), turn(box[2], box[3])]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return {"l": min(xs), "t": min(ys), "r": max(xs), "b": max(ys), "coord_origin": "TOPLEFT"}


def _page_lines(page, number: int, method: dict[str, str]) -> list[dict[str, Any]]:
    textpage = page.get_textpage()
    try:
        rows: list[dict[str, Any]] = []
        chars: list[str] = []
        boxes: list[tuple[float, float, float, float]] = []

        def emit() -> None:
            text = "".join(chars)
            if text.strip():
                rows.append(
                    {
                        "page": number,
                        "text": text,
                        "element": f"pdfium:{number}:{len(rows)}",
                        "regions": [
                            {"page": number, "bbox": _top_left(box, page)} for box in _runs(boxes)
                        ],
                        "extraction": dict(method),
                    }
                )
            chars.clear()
            boxes.clear()

        for index in range(textpage.count_chars()):
            code = pdfium_c.FPDFText_GetUnicode(textpage.raw, index)
            if code == 0:
                continue
            char = chr(code)
            if char in _LINE_BREAKS:
                emit()
                continue
            chars.append(char)
            if not char.isspace():
                left, bottom, right, top = textpage.get_charbox(index)
                if right > left and top > bottom:
                    boxes.append((left, bottom, right, top))
        emit()
        return rows
    finally:
        textpage.close()


def recover_missing_pages(path: Path, pages: list[int]) -> tuple[list[dict], dict | None]:
    """Text a PDF's own text layer holds on pages the primary parser returned empty."""
    method = {"method": "pdfium-native-text", "version": version("pypdfium2")}
    document = pdfium.PdfDocument(str(path))
    rows: list[dict[str, Any]] = []
    recovered: list[int] = []
    try:
        for number in pages:
            page = document[number - 1]
            try:
                found = _page_lines(page, number, method)
            finally:
                page.close()
            if found:
                recovered.append(number)
                rows.extend(found)
    finally:
        document.close()
    if not rows:
        return [], None
    text = "".join(row["text"] for row in rows)
    return rows, {
        **method,
        "pages": recovered,
        "reason": "primary_parser_returned_no_text",
        "rows": len(rows),
        "characters": len(text),
        "control_characters": sum(
            1 for c in text if unicodedata.category(c) == "Cc" and not c.isspace()
        ),
    }
