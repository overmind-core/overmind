"""Hard refuse when a training row would be truncated."""

from __future__ import annotations


def refuse_truncation(n_tokens: int, max_length: int, *, row_index: int) -> None:
    """Raise when a row would be silently truncated. Never drop user tokens."""
    if n_tokens > max_length:
        raise RuntimeError(
            f"row {row_index} has {n_tokens:,} tokens > MAX_LENGTH={max_length:,} — "
            "refusing to truncate; pick a longer-context model or shorten the row"
        )
