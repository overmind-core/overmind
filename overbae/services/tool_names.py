"""Canonical tool-name form shared by scanning, scoring, and eval binding."""

from __future__ import annotations

import re
from typing import Any

_TOOL_NAME_CANON = re.compile(r"[^a-z0-9]+")


def canonical_tool_name(name: Any) -> str:
    """``language_model.infer`` → ``language_model_infer``; ``Chunk-Text`` → ``chunk_text``.

    Every producer and consumer of a tool name MUST go through this, so
    separator-only differences join. Genuinely different names still will not
    join — that mismatch is a real capability bug to surface, not paper over.
    """
    return _TOOL_NAME_CANON.sub("_", str(name or "").lower()).strip("_") or "unknown"
