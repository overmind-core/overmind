"""Normalize and accumulate Cursor SDK ``TokenUsage``.

SDK 0.1.9+ exposes ``result.usage`` / ``run.usage`` as ``TokenUsage``; older builds
omit it or only surface turn-ended dicts, so everything here duck-types rather than
depending on an SDK export.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "total_tokens",
)


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _get(obj: Any, *names: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
        return None
    for name in names:
        if hasattr(obj, name):
            val = getattr(obj, name)
            if val is not None:
                return val
    return None


def token_usage_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None

    # The SDK helper handles camelCase turn payloads.
    from cursor_sdk import to_token_usage

    normalized = to_token_usage(usage)
    if normalized is not None:
        usage = normalized

    input_tokens = _as_int(_get(usage, "input_tokens", "inputTokens"))
    output_tokens = _as_int(_get(usage, "output_tokens", "outputTokens"))
    cache_read = _as_int(_get(usage, "cache_read_tokens", "cacheReadTokens"))
    cache_write = _as_int(_get(usage, "cache_write_tokens", "cacheWriteTokens"))
    total = _as_int(_get(usage, "total_tokens", "totalTokens"))
    reasoning = _as_int(_get(usage, "reasoning_tokens", "reasoningTokens"))

    if all(v is None for v in (input_tokens, output_tokens, cache_read, cache_write, total)):
        return None

    input_tokens = input_tokens or 0
    output_tokens = output_tokens or 0
    cache_read = cache_read or 0
    cache_write = cache_write or 0
    if total is None:
        total = input_tokens + output_tokens + cache_read + cache_write

    out: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "total_tokens": total,
    }
    if reasoning is not None:
        out["reasoning_tokens"] = reasoning
    return out


def sum_token_usage_dicts(
    usages: Sequence[Mapping[str, Any] | None],
) -> dict[str, Any] | None:
    totals = dict.fromkeys(_TOKEN_KEYS, 0)
    reasoning = 0
    has_usage = False
    has_reasoning = False

    for entry in usages:
        parsed = token_usage_dict(entry)
        if parsed is None:
            continue
        has_usage = True
        for key in _TOKEN_KEYS:
            totals[key] += int(parsed.get(key) or 0)
        if "reasoning_tokens" in parsed and parsed["reasoning_tokens"] is not None:
            has_reasoning = True
            reasoning += int(parsed["reasoning_tokens"])

    if not has_usage:
        return None

    # Recompute total so partial dicts can't drift.
    totals["total_tokens"] = (
        totals["input_tokens"]
        + totals["output_tokens"]
        + totals["cache_read_tokens"]
        + totals["cache_write_tokens"]
    )
    if has_reasoning:
        totals["reasoning_tokens"] = reasoning
    return totals


def accumulate_cursor_usage(existing: Mapping[str, Any] | None, usage: Any) -> dict[str, Any]:
    added = token_usage_dict(usage)
    if added is None:
        return dict(existing or {})
    merged = sum_token_usage_dicts([existing or None, added])
    return merged or {}


def usage_delta(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Field-wise delta, clamped at zero; ``None`` when nothing changed."""
    before = before or {}
    after = after or {}
    if not after:
        return None
    out = {key: max(0, int(after.get(key) or 0) - int(before.get(key) or 0)) for key in _TOKEN_KEYS}
    out["total_tokens"] = (
        out["input_tokens"]
        + out["output_tokens"]
        + out["cache_read_tokens"]
        + out["cache_write_tokens"]
    )
    if out["input_tokens"] == 0 and out["output_tokens"] == 0 and out["total_tokens"] == 0:
        return None
    return out


def _self_check() -> None:
    assert token_usage_dict(None) is None
    one = token_usage_dict(
        {
            "inputTokens": 10,
            "outputTokens": 5,
            "cacheReadTokens": 2,
            "cacheWriteTokens": 1,
        }
    )
    assert one == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 2,
        "cache_write_tokens": 1,
        "total_tokens": 18,
    }
    two = token_usage_dict(
        {
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 2,
            "reasoning_tokens": 1,
        }
    )
    summed = sum_token_usage_dicts([one, two])
    assert summed == {
        "input_tokens": 11,
        "output_tokens": 6,
        "cache_read_tokens": 2,
        "cache_write_tokens": 1,
        "total_tokens": 20,
        "reasoning_tokens": 1,
    }
    assert accumulate_cursor_usage({}, None) == {}
    assert accumulate_cursor_usage(one, two)["total_tokens"] == 20
    assert usage_delta(one, summed) == {
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 2,
    }
    assert usage_delta(summed, one) is None
    assert usage_delta({}, None) is None


if __name__ == "__main__":
    _self_check()
    print("cursor_usage ok")
