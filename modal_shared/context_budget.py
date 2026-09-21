from __future__ import annotations

import json
from typing import Any

DEFAULT_OUTPUT_TOKENS = 8192
CONTEXT_HEADROOM = 512
INCOMPLETE_FINISH_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})


def reserve_output(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    if payload.get("truncate_prompt_tokens") is not None:
        raise ValueError(
            "Prompt truncation is not supported. Reduce the input or increase serving context."
        )
    limit = payload.get("max_completion_tokens")
    if limit is None:
        limit = payload.get("max_tokens")
    if limit is None:
        limit = DEFAULT_OUTPUT_TOKENS
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("The output token budget must be a positive integer.")
    payload.pop("max_tokens", None)
    payload.pop("max_completion_tokens", None)
    # vLLM validates the complete chat-template token count plus this reservation.
    # Omitting it lets the server silently shrink output to whatever context remains.
    payload["max_tokens"] = limit
    return payload


def completion_body(path: str, body: bytes) -> bytes:
    if path not in ("/v1/chat/completions", "/v1/completions"):
        return body
    return json.dumps(reserve_output(json.loads(body))).encode()
