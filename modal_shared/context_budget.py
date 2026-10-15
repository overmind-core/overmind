from __future__ import annotations

import json
from typing import Any

DEFAULT_OUTPUT_TOKENS = 8192
CONTEXT_HEADROOM = 512
INCOMPLETE_FINISH_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})
CONTEXT_BUDGET_MESSAGE = (
    "The input and reserved output exceed the deployed context. "
    "Reduce the request or redeploy with a larger serving context."
)


def is_context_limit_error(status: int, body: bytes | str | dict) -> bool:
    """Whether a completion reply refuses the input plus reserved output: a 400, or
    an error object in a 200 body or stream event."""
    if status not in (200, 400):
        return False
    if isinstance(body, (bytes, str)):
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
        try:
            body = json.loads(text)
        except ValueError:
            body = {"message": text}
    if not isinstance(body, dict):
        return False
    error = body.get("error", body if status == 400 else None)
    if isinstance(error, str):
        error = {"message": error}
    if not isinstance(error, dict):
        return False
    if "context_length_exceeded" in (error.get("code"), error.get("type")):
        return True
    message = str(error.get("message") or "").lower()
    return any(marker in message for marker in ("context length", "max_model_len")) or (
        any(marker in message for marker in ("max_tokens", "max_completion_tokens"))
        and any(marker in message for marker in ("too large", "exceed", "at most"))
    )


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
