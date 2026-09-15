"""Row → text helpers shared by grounding, the dataset card and stats."""

from __future__ import annotations

import hashlib
import json
from typing import Any

_CONTENT_KEYS = ("input", "expected_output", "messages")
_VOLATILE_KEYS = frozenset({"id", "source_row", "source_trace_id", "trace_id", "unit_span_id"})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def content_signature(row: Any) -> str:
    """Stable hash of what a row says, ignoring identity columns."""
    if isinstance(row, dict):
        if any(k in row for k in _CONTENT_KEYS):
            content: Any = {k: row.get(k) for k in _CONTENT_KEYS if k in row}
        else:
            content = {k: v for k, v in row.items() if k not in _VOLATILE_KEYS}
    else:
        content = row
    return hashlib.sha1(_canonical(content).encode("utf-8")).hexdigest()  # noqa: S324 — not security


def row_text(row: dict) -> str:
    """Every content value flattened into one string, for pattern and length checks."""
    parts: list[str] = []
    for key, value in row.items():
        if key in _VOLATILE_KEYS or value in (None, ""):
            continue
        parts.append(
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        )
    return " ".join(parts)


def approx_tokens(obj: Any) -> int:
    """~3 chars per token, deliberately conservative so long rows are over-counted."""
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return max(1, len(text) // 3) if text else 0


def approx_tokens_from_chars(chars: int) -> int:
    return max(1, int(chars) // 3) if chars else 0
