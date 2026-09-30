"""List-price cost for observed LLM spans, stamped as ``genai.cost`` at ingest.

Gateway billing prices against OpenRouter in ``model_catalog`` instead: it charges
what OpenRouter charged us, not the provider's list price.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from genai_prices import Usage, calc_price
from genai_prices.data import providers

from overbae.api import overmind_attrs as oc_attrs
from overbae.core.utils import safe_int

PROMPT_TOKEN_KEYS = (
    oc_attrs.LLM_USAGE_PROMPT_TOKENS,
    oc_attrs.LLM_PROMPT_TOKENS,
    "gen_ai.usage.prompt_tokens",
    "gen_ai.usage.input_tokens",
    "llm.usage.prompt_tokens",
)
COMPLETION_TOKEN_KEYS = (
    oc_attrs.LLM_USAGE_COMPLETION_TOKENS,
    oc_attrs.LLM_COMPLETION_TOKENS,
    "gen_ai.usage.completion_tokens",
    "gen_ai.usage.output_tokens",
    "llm.usage.completion_tokens",
)
CACHE_READ_TOKEN_KEYS = (
    oc_attrs.LLM_CACHE_READ_TOKENS,
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_read_input_tokens",
    "gen_ai.usage.cache_read_tokens",
)
MODEL_KEYS = (
    oc_attrs.LLM_MODEL,
    oc_attrs.LLM_RESPONSE_MODEL,
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.model",
)

_PROVIDER_IDS = frozenset(provider.id for provider in providers)
# OTel GenAI semconv provider names that differ from genai-prices ids.
_SEMCONV_PROVIDERS = {
    "aws.bedrock": "aws",
    "azure.ai.inference": "azure",
    "azure.ai.openai": "azure",
    "gcp.gemini": "google",
    "gcp.gen_ai": "google",
    "gcp.vertex_ai": "google",
    "mistral_ai": "mistral",
    "x_ai": "x-ai",
}


def _first_int(attributes: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = safe_int(attributes.get(key))
        if value > 0:
            return value
    return 0


def _first_str(attributes: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _provider_id(name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    key = name.strip().lower()
    key = _SEMCONV_PROVIDERS.get(key, key)
    return key if key in _PROVIDER_IDS else None


def price_llm_call(
    model: str | None,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    provider: str | None = None,
    at: datetime | None = None,
) -> float | None:
    """USD at the provider's list price on ``at``; ``None`` when the model is unpriced."""
    if not model or not (prompt_tokens or completion_tokens):
        return None
    # genai-prices counts cache reads inside input tokens; Anthropic-shaped usage
    # reports them separately, which shows as more cache reads than input.
    if cache_read_tokens > prompt_tokens:
        prompt_tokens += cache_read_tokens
    usage = Usage(
        input_tokens=prompt_tokens or None,
        output_tokens=completion_tokens or None,
        cache_read_tokens=cache_read_tokens or None,
    )
    # Routed refs (``openai/gpt-4o``, ``models/gemini-2.5-flash``) fall back to the bare model.
    refs = (model, model.rsplit("/", 1)[1]) if "/" in model else (model,)
    for ref in refs:
        for provider_id in dict.fromkeys((_provider_id(provider), None)):
            try:
                price = calc_price(usage, ref, provider_id=provider_id, genai_request_timestamp=at)
            except (LookupError, ValueError):
                continue
            return float(price.total_price)
    return None


def stamp_span_cost(attributes: dict[str, Any], *, start_time_ns: int | None) -> None:
    """Sets ``genai.cost`` when the span carries tokens and a priced model but no
    reported cost. A reported cost is never replaced."""
    if attributes.get(oc_attrs.LLM_COST) not in (None, ""):
        return
    cost = price_llm_call(
        _first_str(attributes, MODEL_KEYS),
        prompt_tokens=_first_int(attributes, PROMPT_TOKEN_KEYS),
        completion_tokens=_first_int(attributes, COMPLETION_TOKEN_KEYS),
        cache_read_tokens=_first_int(attributes, CACHE_READ_TOKEN_KEYS),
        provider=attributes.get(oc_attrs.LLM_PROVIDER),
        at=datetime.fromtimestamp(start_time_ns / 1e9, tz=UTC) if start_time_ns else None,
    )
    if cost is not None:
        attributes[oc_attrs.LLM_COST] = cost
