"""Token estimates shared by the dataset contract, finetuning and recommendations."""

from __future__ import annotations

import json
from typing import Any


def approx_tokens(obj: Any) -> int:
    """~3 chars per token, deliberately conservative so long rows are over-counted."""
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return max(1, len(text) // 3) if text else 0


def approx_tokens_from_chars(chars: int) -> int:
    return max(1, int(chars) // 3) if chars else 0
