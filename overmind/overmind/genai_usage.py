"""Canonical ``genai.*`` usage mirroring.

The Overmind server rolls token usage onto an Agent from the canonical
``genai.prompt_tokens`` / ``genai.completion_tokens`` / ``genai.total_tokens``
span attributes (see ``overbae/api/otlp.py::_build_span_usage``) and prices
each span from them at ingest.  Third-party OTel
auto-instrumentors, however, emit the OpenTelemetry GenAI semantic
convention keys (``gen_ai.usage.prompt_tokens`` etc.) or the Traceloop
``llm.usage.total_tokens`` variant — none of which the server reads.

:func:`canonical_usage_updates` bridges the gap: given a span's existing
attributes it returns ONLY the canonical ``genai.*`` keys that are
derivable but not already present.  It never zero-fills — a token count
we don't have is simply omitted — so the server never records a misleading
``0``.

This is consumed both by the on-end span processor (to mirror
auto-instrumentor spans) and is the single source of truth for the token
key aliases the SDK understands.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from overmind import attrs

# Every attribute key that may carry a given token count, in priority order.
# Covers: the canonical short form, the ``genai.usage.*`` alias, the OTel
# semconv (``gen_ai.usage.prompt_tokens`` / ``…input_tokens``), the Traceloop
# ``llm.usage.*`` variant, and the OpenInference ``llm.token_count.*`` variant
# (emitted by the ``providers=["langchain"]`` instrumentor).
_PROMPT_TOKEN_KEYS: tuple[str, ...] = (
    attrs.LLM_PROMPT_TOKENS,
    attrs.LLM_USAGE_PROMPT_TOKENS,
    "gen_ai.usage.prompt_tokens",
    "gen_ai.usage.input_tokens",
    "llm.usage.prompt_tokens",
    "llm.token_count.prompt",
)
_COMPLETION_TOKEN_KEYS: tuple[str, ...] = (
    attrs.LLM_COMPLETION_TOKENS,
    attrs.LLM_USAGE_COMPLETION_TOKENS,
    "gen_ai.usage.completion_tokens",
    "gen_ai.usage.output_tokens",
    "llm.usage.completion_tokens",
    "llm.token_count.completion",
)
_TOTAL_TOKEN_KEYS: tuple[str, ...] = (
    attrs.LLM_TOTAL_TOKENS,
    attrs.LLM_USAGE_TOTAL_TOKENS,
    "gen_ai.usage.total_tokens",
    "llm.usage.total_tokens",
    "llm.token_count.total",
)
_MODEL_KEYS: tuple[str, ...] = (
    attrs.LLM_MODEL,
    attrs.LLM_RESPONSE_MODEL,
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.model",
    "llm.model_name",
)

_USAGE_SOURCE_KEYS = frozenset((*_PROMPT_TOKEN_KEYS, *_COMPLETION_TOKEN_KEYS, *_TOTAL_TOKEN_KEYS, *_MODEL_KEYS))


def _first_int(attributes: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    """Return the first key's value coerced to a positive int, else ``None``."""
    for key in keys:
        if key not in attributes:
            continue
        raw = attributes[key]
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _first_str(attributes: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def canonical_usage_updates(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Return canonical ``genai.*`` keys derivable from *attributes* but missing.

    Never zero-fills.  Only tokens actually found (in any supported alias)
    are mirrored to the canonical short keys.  Already-canonical keys are
    left untouched so an explicit value always wins.
    """
    updates: dict[str, Any] = {}
    # Most spans carry no usage keys at all; skip the alias probing for them.
    if not _USAGE_SOURCE_KEYS.intersection(attributes):
        return updates

    prompt_tokens = _first_int(attributes, _PROMPT_TOKEN_KEYS)
    completion_tokens = _first_int(attributes, _COMPLETION_TOKEN_KEYS)
    total_tokens = _first_int(attributes, _TOTAL_TOKEN_KEYS)
    if total_tokens is None and (prompt_tokens or completion_tokens):
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    if prompt_tokens is not None and attrs.LLM_PROMPT_TOKENS not in attributes:
        updates[attrs.LLM_PROMPT_TOKENS] = prompt_tokens
    if completion_tokens is not None and attrs.LLM_COMPLETION_TOKENS not in attributes:
        updates[attrs.LLM_COMPLETION_TOKENS] = completion_tokens
    if total_tokens is not None and attrs.LLM_TOTAL_TOKENS not in attributes:
        updates[attrs.LLM_TOTAL_TOKENS] = total_tokens

    model = _first_str(attributes, _MODEL_KEYS)
    if model and attrs.LLM_MODEL not in attributes:
        updates[attrs.LLM_MODEL] = model

    return updates
