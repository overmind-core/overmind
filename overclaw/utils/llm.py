"""Provider-specific adjustments for LiteLLM completion calls."""

from __future__ import annotations

import logging
import time
from typing import Any

import litellm

logger = logging.getLogger("overclaw.llm")


def completion_kwargs_for_model(model: str, **kwargs: object) -> dict:
    """Build kwargs for ``litellm.completion``, applying all provider-specific rules.

    Rules applied:
    - OpenAI newer chat models reject ``temperature``; it is removed.
    - Anthropic models receive ``cache_control`` for prompt caching.

    If the provider cannot be resolved (unknown model id), kwargs are returned unchanged.
    """
    out: dict = dict(kwargs)
    try:
        _, provider, _, _ = litellm.get_llm_provider(model=model)
    except Exception:
        return out
    if provider == "openai":
        out.pop("temperature", None)
    if provider == "anthropic":
        out["cache_control"] = {"type": "ephemeral"}
    return out


def _provider_for(model: str) -> str:
    try:
        _, provider, _, _ = litellm.get_llm_provider(model=model)
        return str(provider)
    except Exception:
        return "unknown"


def _summarize_messages(messages: list[dict]) -> tuple[int, int, str]:
    """Return ``(num_messages, total_chars, roles)`` for compact logging."""
    total_chars = 0
    roles: list[str] = []
    for msg in messages or []:
        role = msg.get("role", "?") if isinstance(msg, dict) else "?"
        roles.append(role)
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for chunk in content:
                if isinstance(chunk, dict):
                    text = chunk.get("text") or chunk.get("content") or ""
                    if isinstance(text, str):
                        total_chars += len(text)
    return len(messages or []), total_chars, ",".join(roles)


def _response_preview(response: Any, limit: int = 160) -> str:
    """Best-effort single-line preview of ``response.choices[0].message``."""
    try:
        choice = response.choices[0]
        msg = getattr(choice, "message", None) or {}
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if not isinstance(content, str):
            return ""
        flat = " ".join(content.split())
        if len(flat) <= limit:
            return flat
        return flat[: limit - 1] + "…"
    except Exception:
        return ""


def _usage_tuple(response: Any) -> tuple[int, int, int]:
    """Return ``(prompt_tokens, completion_tokens, total_tokens)`` or zeros."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0
    try:
        return (
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
            int(getattr(usage, "total_tokens", 0) or 0),
        )
    except Exception:
        return 0, 0, 0


def llm_completion(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    **kwargs: object,
) -> Any:
    """Drop-in wrapper around ``litellm.completion`` with all provider rules applied.

    Use this instead of calling ``litellm.completion`` directly so that every
    call site automatically benefits from provider-specific adjustments
    (temperature stripping for OpenAI, prompt caching for Anthropic, etc.).

    Every call is logged with model, provider, message shape, latency, token
    usage, and a truncated response preview so multi-thread pipelines remain
    debuggable from the log file alone.
    """
    provider = _provider_for(model)
    num_msgs, total_chars, roles = _summarize_messages(messages)
    num_tools = len(tools or [])
    kwarg_keys = ",".join(sorted(k for k in kwargs.keys() if k != "api_key"))

    logger.debug(
        "llm_completion BEGIN model=%s provider=%s messages=%d chars=%d roles=%s tools=%d kwargs=[%s]",
        model,
        provider,
        num_msgs,
        total_chars,
        roles,
        num_tools,
        kwarg_keys,
    )
    t0 = time.monotonic()
    try:
        response = litellm.completion(
            model=model,
            messages=messages,
            tools=tools or None,
            **completion_kwargs_for_model(model, **kwargs),
        )
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.exception(
            "llm_completion FAIL  model=%s provider=%s elapsed=%.2fs error=%s: %s",
            model,
            provider,
            elapsed,
            type(exc).__name__,
            exc,
        )
        raise

    elapsed = time.monotonic() - t0
    pt, ct, tt = _usage_tuple(response)
    preview = _response_preview(response)
    logger.info(
        "llm_completion OK    model=%s provider=%s elapsed=%.2fs tokens_in=%d tokens_out=%d total=%d preview=%r",
        model,
        provider,
        elapsed,
        pt,
        ct,
        tt,
        preview,
    )
    return response
