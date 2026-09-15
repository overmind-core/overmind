"""Central LLM calling layer over OpenRouter, with retry and reasoning support."""

import json
import logging
import os
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import json_repair
import openai
from openai import OpenAI
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel
from tenacity import (
    RetryCallState,
    Retrying,
    before_sleep_log,
    stop_after_delay,
    wait_exponential_jitter,
)

from modal_shared.modelfam import serve_image_key
from modal_shared.shared import routing_headers as _modal_routing_headers
from overbae.core.model_resolver import OPENROUTER_MODEL_SLUGS, TaskType, resolve_model
from overbae.models import DeployedModel

logger = logging.getLogger(__name__)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_TOGETHER_BASE_URL = "https://api.together.xyz/v1"

# Seconds per completion. The SDK default of 600s lets one hung socket read pin
# a `--pool=threads` worker thread long enough to starve the queue, so this stays
# well above a healthy completion but far below the default. Total retry time is
# bounded separately by `stop_after_delay(300)`.
_REQUEST_TIMEOUT = float(os.environ.get("LLM_REQUEST_TIMEOUT", "120"))

# Reasoning models spend hidden reasoning tokens out of the same completion
# budget as the visible answer, so a bare ``max_tokens`` can be consumed entirely
# by reasoning and return ``finish_reason="length"`` with empty content. This
# headroom is added only for reasoning models, leaving other budgets untouched.
_REASONING_TOKEN_HEADROOM = int(os.environ.get("LLM_REASONING_TOKEN_HEADROOM", "12000"))
_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://overmindlab.ai",
    "X-Title": "Overmind",
}

_RETRYABLE_OPENAI_ERRORS = (
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APIConnectionError,
)

_NON_RETRYABLE_MESSAGE_MARKERS = (
    "missing credentials",
    "authentication_error",
)


@dataclass(frozen=True)
class ModelSpec:
    """A model outside the hardcoded :data:`SUPPORTED_LLM_MODELS` catalog —
    fine-tunes and custom OpenAI-compatible endpoints — built from a ``ModelRef``.
    """

    provider: str  # openai | anthropic | gemini | together | custom
    model_id: str
    base_url: str = ""
    # Env var name; secrets are never persisted.
    api_key_env: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _openrouter_client() -> OpenAI:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required for LLM completions")
    return OpenAI(
        api_key=api_key,
        base_url=_OPENROUTER_BASE_URL,
        default_headers=_OPENROUTER_HEADERS,
        timeout=_REQUEST_TIMEOUT,
    )


@lru_cache(maxsize=1)
def _openai_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")
    return OpenAI(api_key=api_key, timeout=_REQUEST_TIMEOUT)


@lru_cache(maxsize=64)
def _openai_compatible_client(
    base_url: str,
    api_key: str,
    query_items: tuple[tuple[str, str], ...] = (),
    header_items: tuple[tuple[str, str], ...] = (),
) -> OpenAI:
    # ``query_items`` are routing params that must ride on every request. Passing
    # them via ``default_query`` keeps them after the appended
    # ``/chat/completions`` path; baked into ``base_url`` they get corrupted.
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=_REQUEST_TIMEOUT,
        default_query=dict(query_items) or None,
        default_headers=dict(header_items) or None,
    )


def _inference_routing_header_items(model_id: str) -> tuple[tuple[str, str], ...]:
    """Postgres routing for Modal gateway calls. Empty if the row is missing."""
    dep = (
        DeployedModel.objects.filter(model_id=model_id)
        .exclude(weights_path="")
        .exclude(gpu_type="")
        .first()
    )
    if dep is None:
        return ()
    return tuple(
        sorted(
            _modal_routing_headers(
                gpu_type=dep.gpu_type,
                weights_path=dep.weights_path,
                max_model_len=dep.max_model_len,
                serve_image=serve_image_key(dep.base_model_id, dep.model_id),
                adapter_path=dep.adapter_path or "",
                lora_rank=dep.lora_rank or 0,
            ).items()
        )
    )


def _split_base_url(url: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Return ``(base_without_query, query_items)`` — see _openai_compatible_client."""
    parts = urlsplit(url)
    base = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return base, tuple(parse_qsl(parts.query))


def _model_spec_client_and_name(spec: ModelSpec) -> tuple[OpenAI, str, str]:
    provider = (spec.provider or "").lower()
    if provider == "together":
        key_env = spec.api_key_env or "TOGETHER_API_KEY"
        api_key = os.environ.get(key_env)
        if not api_key:
            raise RuntimeError(f"{key_env} is required for Together model '{spec.model_id}'")
        return _openai_compatible_client(_TOGETHER_BASE_URL, api_key), spec.model_id, provider

    if provider == "custom":
        if not spec.base_url:
            raise RuntimeError(f"Custom model '{spec.model_id}' requires base_url")
        if not spec.api_key_env:
            raise RuntimeError(f"Custom model '{spec.model_id}' requires api_key_env")
        api_key = os.environ.get(spec.api_key_env)
        if not api_key:
            raise RuntimeError(f"{spec.api_key_env} is required for custom model '{spec.model_id}'")
        base, query_items = _split_base_url(spec.base_url)
        header_items = (
            _inference_routing_header_items(spec.model_id)
            if spec.api_key_env == "INFERENCE_API_KEY"
            else ()
        )
        return (
            _openai_compatible_client(base, api_key, query_items, header_items),
            spec.model_id,
            provider,
        )

    slug = _openrouter_model_slug(spec.model_id)
    return _openrouter_client(), slug, "openrouter"


def _openrouter_model_slug(model_name: str) -> str:
    selected_model_name = normalize_model_name(model_name)
    if selected_model_name in OPENROUTER_MODEL_SLUGS:
        return OPENROUTER_MODEL_SLUGS[selected_model_name]
    if "/" in selected_model_name:
        return selected_model_name.removeprefix("openrouter/")
    raise ValueError(f"Unsupported model: {selected_model_name}")


# Public routing surface for callers that build ModelRef rows.
OPENROUTER_BASE_URL = _OPENROUTER_BASE_URL
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"


# Qualifies slashless names ("gpt-4o-mini" → "openai/gpt-4o-mini"). Most-specific
# stems first, since matching is by prefix.
_OPENROUTER_VENDOR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("chatgpt", "openai/"),
    ("gpt-", "openai/"),
    ("o1", "openai/"),
    ("o3", "openai/"),
    ("o4", "openai/"),
    ("claude", "anthropic/"),
    ("gemini", "google/"),
    ("grok", "x-ai/"),
    ("deepseek", "deepseek/"),
    ("llama", "meta-llama/"),
    ("qwen", "qwen/"),
    ("mistral", "mistralai/"),
    ("kimi", "moonshotai/"),
)


def resolve_openrouter_slug(model_name: str) -> str:
    """Best-effort OpenRouter slug for a model name/slug; pass through if unknown.

    A bare vendor name — no ``/`` and absent from the curated slug map — is
    qualified with its vendor prefix, so an incumbent model stored provider-less
    still resolves.
    """
    try:
        return _openrouter_model_slug(model_name)
    except ValueError:
        name = (model_name or "").strip()
        if name and "/" not in name:
            lowered = name.lower()
            for stem, prefix in _OPENROUTER_VENDOR_PREFIXES:
                if lowered.startswith(stem):
                    return f"{prefix}{name}"
        return model_name


SUPPORTED_LLM_MODELS = [
    {
        "provider": "openai",
        "model_name": "gpt-5.4",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "is_new": True,
        "description": "OpenAI's frontier model for complex professional work and agentic tasks.",
    },
    {
        "provider": "openai",
        "model_name": "gpt-5.4-pro",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["medium", "high"],
        "is_new": True,
        "description": "Premium variant of GPT-5.4 that uses more compute to think harder.",
    },
    {
        "provider": "openai",
        "model_name": "gpt-5.4-mini",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "is_new": True,
        "description": "Fast and efficient GPT-5.4 variant for high-volume agentic and coding workloads.",
    },
    {
        "provider": "openai",
        "model_name": "gpt-5.4-nano",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "is_new": True,
        "description": "Smallest and fastest GPT-5.4 model.",
    },
    {
        "provider": "openai",
        "model_name": "gpt-5.2",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "description": "OpenAI's frontier model for professional work and long-running capabilities.",
    },
    {
        "provider": "openai",
        "model_name": "gpt-5.2-pro",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["medium", "high"],
        "description": "Highest-compute GPT-5.2 variant.",
    },
    {
        "provider": "openai",
        "model_name": "gpt-5",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "description": "OpenAI's best model for coding and agentic tasks.",
    },
    {
        "provider": "openai",
        "model_name": "gpt-5.6-luna",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "is_new": True,
        "description": "OpenAI's fast tier: near-frontier quality at a fraction of the price.",
    },
    {
        "provider": "openai",
        "model_name": "gpt-5-mini",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "description": "Smaller, faster GPT-5 variant for high-volume workloads.",
    },
    {
        "provider": "openai",
        "model_name": "gpt-5-nano",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "description": "Smallest and cheapest GPT-5 model.",
    },
    {
        "provider": "openai",
        "model_name": "gpt-4.1",
        "supports_reasoning": False,
        "description": "OpenAI model with major improvements in coding and instruction following.",
    },
    {
        "provider": "anthropic",
        "model_name": "claude-opus-4-6",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high", "max"],
        "is_new": True,
        "description": "Anthropic's most intelligent model.",
    },
    {
        "provider": "anthropic",
        "model_name": "claude-sonnet-5",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "is_new": True,
        "description": "Anthropic's best combination of speed and intelligence.",
    },
    {
        "provider": "anthropic",
        "model_name": "claude-sonnet-4-6",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "is_new": True,
        "description": "Anthropic's best combination of speed and intelligence.",
    },
    {
        "provider": "anthropic",
        "model_name": "claude-opus-4-5",
        "supports_reasoning": True,
        "adaptive_mode": False,
        "thinking_budget_tokens": [8000],
        "description": "Anthropic's most powerful 4.5-generation model.",
    },
    {
        "provider": "anthropic",
        "model_name": "claude-sonnet-4-5",
        "supports_reasoning": True,
        "adaptive_mode": False,
        "thinking_budget_tokens": [8000],
        "description": "Anthropic's balanced 4.5-generation model.",
    },
    {
        "provider": "anthropic",
        "model_name": "claude-haiku-4-5",
        "supports_reasoning": True,
        "adaptive_mode": False,
        "thinking_budget_tokens": [8000],
        "description": "Anthropic's fastest model.",
    },
    {
        "provider": "gemini",
        "model_name": "gemini-3.1-pro-preview",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "is_new": True,
        "description": "Google's most advanced reasoning model.",
    },
    {
        "provider": "gemini",
        "model_name": "gemini-3.1-flash-lite-preview",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "is_new": True,
        "description": "Most cost-efficient Gemini 3 model.",
    },
    {
        "provider": "gemini",
        "model_name": "gemini-3.8-flash",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "is_new": True,
        "description": "Google's fast Gemini 3 tier.",
    },
    {
        "provider": "gemini",
        "model_name": "gemini-3-flash-preview",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "description": "Google's most powerful agentic model in the Gemini 3 series.",
    },
    {
        "provider": "gemini",
        "model_name": "gemini-2.5-flash",
        "supports_reasoning": True,
        "adaptive_mode": False,
        "thinking_budget_tokens": [-1],
        "description": "Google's first full hybrid reasoning model.",
    },
    {
        "provider": "gemini",
        "model_name": "gemini-2.5-flash-lite",
        "supports_reasoning": False,
        "description": "Google's fastest and cheapest Gemini 2.5 model.",
    },
    {
        "provider": "gemini",
        "model_name": "gemini-2.5-pro",
        "supports_reasoning": True,
        "adaptive_mode": True,
        "reasoning_levels": ["low", "medium", "high"],
        "reasoning_required": True,
        "description": "Google's most capable Gemini 2.5 model with always-on reasoning.",
    },
    {
        # OpenRouter slug used for dataset schema interpretation.
        "provider": "openrouter",
        "model_name": "moonshotai/kimi-k2",
        "supports_reasoning": False,
        "description": "Moonshot AI's Kimi K2 (open-weight MoE) served via OpenRouter.",
    },
]

SUPPORTED_LLM_MODEL_NAMES = {item["model_name"] for item in SUPPORTED_LLM_MODELS}
LLM_PROVIDER_BY_MODEL = {item["model_name"]: item["provider"] for item in SUPPORTED_LLM_MODELS}
REASONING_SUPPORT_BY_MODEL = {
    item["model_name"]: {
        "supports_reasoning": item["supports_reasoning"],
        "adaptive_mode": item.get("adaptive_mode"),
        "reasoning_levels": item.get("reasoning_levels"),
        "thinking_budget_tokens": item.get("thinking_budget_tokens"),
        "reasoning_required": item.get("reasoning_required", False),
    }
    for item in SUPPORTED_LLM_MODELS
}

_DATE_SUFFIX_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def normalize_model_name(model_name: str) -> str:
    base = _DATE_SUFFIX_RE.sub("", model_name)
    if base in SUPPORTED_LLM_MODEL_NAMES:
        return base
    return model_name


def model_supports_reasoning(model_name: str) -> bool:
    info = REASONING_SUPPORT_BY_MODEL.get(normalize_model_name(model_name))
    return info["supports_reasoning"] if info else False


def get_reasoning_levels(model_name: str) -> list[str]:
    info = REASONING_SUPPORT_BY_MODEL.get(normalize_model_name(model_name))
    if not info or not info["supports_reasoning"]:
        return []
    return list(info.get("reasoning_levels") or [])


def get_thinking_budget_tokens(model_name: str) -> list[int]:
    info = REASONING_SUPPORT_BY_MODEL.get(normalize_model_name(model_name))
    if not info or info.get("adaptive_mode") is not False:
        return []
    return list(info.get("thinking_budget_tokens") or [])


def is_adaptive_mode(model_name: str) -> bool | None:
    info = REASONING_SUPPORT_BY_MODEL.get(normalize_model_name(model_name))
    return info.get("adaptive_mode") if info else None


def is_reasoning_required(model_name: str) -> bool:
    info = REASONING_SUPPORT_BY_MODEL.get(normalize_model_name(model_name))
    return info.get("reasoning_required", False) if info else False


def _effective_max_tokens(model_name: str, max_tokens: int) -> int:
    """``max_tokens`` stays the caller's intended visible-output size; reasoning
    models get extra room so hidden reasoning cannot starve the answer.
    """
    if model_supports_reasoning(model_name):
        return max_tokens + _REASONING_TOKEN_HEADROOM
    return max_tokens


def _get_default_model() -> str:
    return resolve_model(TaskType.DEFAULT)


def _should_retry_llm_call(retry_state: RetryCallState) -> bool:
    exc = retry_state.outcome.exception()
    if exc is None:
        return False
    if isinstance(exc, _RETRYABLE_OPENAI_ERRORS):
        pass
    elif isinstance(exc, openai.APIStatusError):
        if exc.status_code < 500:
            return False
    else:
        return False
    message = str(exc).lower()
    return not any(marker in message for marker in _NON_RETRYABLE_MESSAGE_MARKERS)


# A request path holds a browser connection open, so it must give up long before
# a Celery task does. Callers pass the deadline that suits them.
RETRY_DEADLINE_INTERACTIVE = float(os.environ.get("LLM_RETRY_DEADLINE_INTERACTIVE", "45"))
RETRY_DEADLINE_BACKGROUND = float(os.environ.get("LLM_RETRY_DEADLINE_BACKGROUND", "300"))


def _retry_after_seconds(exc: BaseException) -> float | None:
    """A 429 carries the wait the provider wants; blind backoff ignores it."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        # The header also permits an HTTP date; backing off blind is fine there.
        return None


class _WaitHonouringRetryAfter:
    def __init__(self, fallback):
        self._fallback = fallback

    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if exc is not None:
            after = _retry_after_seconds(exc)
            if after is not None:
                return min(after, 60.0)
        return self._fallback(retry_state)


_LLM_WAIT = _WaitHonouringRetryAfter(wait_exponential_jitter(initial=1, max=60, jitter=5))


def _do_openai_completion(
    client: OpenAI,
    completion_kwargs: dict,
    request_kwargs: dict,
    retry_deadline: float = RETRY_DEADLINE_BACKGROUND,
):
    def _once():
        started = time.monotonic()
        response = client.chat.completions.create(**completion_kwargs, **request_kwargs)
        response_ms = (time.monotonic() - started) * 1000
        with suppress(AttributeError, TypeError):
            object.__setattr__(response, "_response_ms", response_ms)
        return response

    return Retrying(
        retry=_should_retry_llm_call,
        wait=_LLM_WAIT,
        stop=stop_after_delay(retry_deadline),
        reraise=True,
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )(_once)


class EmbeddingUnavailableError(RuntimeError):
    """Raised when embeddings cannot run: they need a direct OpenAI key
    (OpenRouter does not proxy the embeddings API)."""


_EMBEDDING_MODEL = "text-embedding-3-small"


# Well under the 2048-input / payload ceilings; keeps each request small and retryable.
def _require_openai_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise EmbeddingUnavailableError(
            "Embeddings require OPENAI_API_KEY (text-embedding-3-small); "
            "OpenRouter does not proxy the embeddings API."
        )


def _vector(item: Any) -> list[float]:
    embedding = item["embedding"] if isinstance(item, dict) else item.embedding
    if embedding is None:
        raise ValueError("No embedding received")
    return embedding


def get_embedding(input_text: str) -> list[float]:
    _require_openai_key()
    response = _openai_client().embeddings.create(model=_EMBEDDING_MODEL, input=[input_text])
    return _vector(response.data[0])


def _extra(obj: Any) -> dict[str, Any]:
    value = getattr(obj, "model_extra", None)
    return value if isinstance(value, dict) else {}


def _usage_value(usage: Any, key: str, default: Any = 0) -> Any:
    extras = _extra(usage)
    if key in extras:
        return extras[key]
    value = getattr(usage, key, None)
    if value is not None:
        return value
    return default


def _cached_tokens(usage: Any) -> int:
    """Accounting metadata never fails a completion — an odd shape reads as zero."""
    details = getattr(usage, "prompt_tokens_details", None) or _extra(usage).get(
        "prompt_tokens_details"
    )
    raw = (
        details.get("cached_tokens")
        if isinstance(details, dict)
        else getattr(details, "cached_tokens", None)
    )
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _extract_llm_response(response) -> tuple[str, dict]:
    message = response.choices[0].message
    content = message.content
    if content is None:
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            content = json.dumps({"tool_calls": [tc.model_dump() for tc in tool_calls]})
        else:
            raise ValueError("No content or tool calls received from LLM")

    usage = getattr(response, "usage", None)
    stats: dict = {
        "prompt_tokens": _usage_value(usage, "prompt_tokens"),
        "completion_tokens": _usage_value(usage, "completion_tokens"),
        "response_ms": getattr(response, "_response_ms", 0),
        "response_cost": _usage_value(usage, "cost"),
        "cached_tokens": _cached_tokens(usage),
        "cache_discount": _usage_value(usage, "cache_discount", None),
    }
    # A chain request can be answered by a model further down the list.
    served = getattr(response, "model", None)
    if served:
        stats["served_model"] = served
    reasoning_content = _extra(message).get("reasoning") or getattr(
        message, "reasoning_content", None
    )
    if reasoning_content:
        stats["reasoning_content"] = reasoning_content

    return content.strip(), stats


def _provider_preferences(completion_kwargs: dict) -> dict | None:
    """Without ``require_parameters`` OpenRouter may route a schema-carrying request
    to a provider that ignores the schema, and the reply comes back as prose.
    """
    if "response_format" in completion_kwargs or "tools" in completion_kwargs:
        return {"require_parameters": True}
    return None


def _fallback_slugs(models: list[str] | None, selected_slug: str) -> list[str] | None:
    """OpenRouter walks ``models`` on any error and bills the one that answered,
    naming it in ``response.model``. The lead model must head the list.
    """
    if not models:
        return None
    slugs = [selected_slug]
    for name in models:
        with suppress(ValueError):
            slug = _openrouter_model_slug(name)
            if slug not in slugs:
                slugs.append(slug)
    return slugs if len(slugs) > 1 else None


def _response_format_param(response_format: type[BaseModel] | None) -> dict | None:
    if response_format is None:
        return None
    # Strict structured outputs need `additionalProperties: false` on every object
    # and every key in `required`; pydantic's model_json_schema emits neither, so
    # this uses the SDK's own transformer, the one `.parse()` uses.
    return {
        "type": "json_schema",
        "json_schema": {
            "name": response_format.__name__,
            "strict": True,
            "schema": to_strict_json_schema(response_format),
        },
    }


def _reasoning_extra_body(
    selected_model_name: str,
    reasoning_effort: str | None,
    thinking_budget_tokens: int | None,
) -> dict[str, Any] | None:
    adaptive = is_adaptive_mode(selected_model_name)
    effective_reasoning_effort = reasoning_effort
    if effective_reasoning_effort is None and is_reasoning_required(selected_model_name):
        effective_reasoning_effort = "medium"

    if adaptive is False and thinking_budget_tokens is not None:
        budgets = get_thinking_budget_tokens(selected_model_name)
        if thinking_budget_tokens in budgets and thinking_budget_tokens > 0:
            return {"reasoning": {"max_tokens": thinking_budget_tokens}}
        return None

    if effective_reasoning_effort and adaptive is True:
        levels = get_reasoning_levels(selected_model_name)
        if levels and effective_reasoning_effort in levels:
            effort = "high" if effective_reasoning_effort == "max" else effective_reasoning_effort
            return {"reasoning": {"effort": effort}}
    return None


def call_llm(
    input_text: str,
    system_prompt: str | None = None,
    model: str | None = None,
    response_format: type[BaseModel] | None = None,
    request_kwargs: dict | None = None,
    messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    reasoning_effort: str | None = None,
    thinking_budget_tokens: int | None = None,
    model_spec: ModelSpec | None = None,
    max_tokens: int = 5000,
    fallback_models: list[str] | None = None,
    retry_deadline: float = RETRY_DEADLINE_BACKGROUND,
) -> tuple[str, dict]:
    if request_kwargs is None:
        request_kwargs = {}

    if messages is None:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": input_text})
    else:
        messages = [
            m
            for m in messages
            if not (m.get("content") is None and m.get("role") in ("system", "user"))
        ]

    try:
        if model_spec is not None:
            if model_spec.params:
                request_kwargs = {**model_spec.params, **request_kwargs}
            client, selected_model, provider = _model_spec_client_and_name(model_spec)
            selected_model_name = normalize_model_name(model_spec.model_id)

            completion_kwargs = {
                "model": selected_model,
                "messages": messages,
                "max_tokens": _effective_max_tokens(selected_model_name, max_tokens),
            }
            # A spec-level max_tokens overrides the default budget, and an
            # explicit None omits the param so the server sizes output itself.
            # vLLM 400s any request whose max_tokens exceeds max_model_len, so
            # small self-hosted deployments need that omission.
            if "max_tokens" in request_kwargs:
                cap = request_kwargs.pop("max_tokens")
                if cap is None:
                    completion_kwargs.pop("max_tokens")
                else:
                    completion_kwargs["max_tokens"] = int(cap)
            formatted_response = _response_format_param(response_format)
            if formatted_response:
                completion_kwargs["response_format"] = formatted_response
            if tools:
                completion_kwargs["tools"] = tools
            extra_body = {"usage": {"include": True}}
            reasoning_body = _reasoning_extra_body(
                selected_model_name,
                reasoning_effort,
                thinking_budget_tokens,
            )
            if reasoning_body and provider == "openrouter":
                extra_body.update(reasoning_body)
            if provider == "openrouter":
                preferences = _provider_preferences(completion_kwargs)
                if preferences:
                    extra_body["provider"] = preferences
                completion_kwargs["extra_body"] = extra_body

            response = _do_openai_completion(
                client, completion_kwargs, request_kwargs, retry_deadline
            )
            return _extract_llm_response(response)

        selected_model_name = normalize_model_name(model) if model else _get_default_model()
        selected_model = _openrouter_model_slug(selected_model_name)
        client = _openrouter_client()

        completion_kwargs: dict = {
            "model": selected_model,
            "messages": messages,
            "max_tokens": _effective_max_tokens(selected_model_name, max_tokens),
            "extra_body": {"usage": {"include": True}},
        }
        formatted_response = _response_format_param(response_format)
        if formatted_response:
            completion_kwargs["response_format"] = formatted_response

        if tools:
            completion_kwargs["tools"] = tools

        reasoning_body = _reasoning_extra_body(
            selected_model_name,
            reasoning_effort,
            thinking_budget_tokens,
        )
        if reasoning_body:
            completion_kwargs["extra_body"].update(reasoning_body)

        preferences = _provider_preferences(completion_kwargs)
        if preferences:
            completion_kwargs["extra_body"]["provider"] = preferences
        chain = _fallback_slugs(fallback_models, selected_model)
        if chain:
            completion_kwargs["extra_body"]["models"] = chain

        response = _do_openai_completion(client, completion_kwargs, request_kwargs, retry_deadline)
        return _extract_llm_response(response)

    except Exception as e:
        raise RuntimeError(f"Error calling LLM: {e}") from e


def call_llm_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model: str | None = None,
    max_tokens: int = 4000,
    fallback_models: list[str] | None = None,
    retry_deadline: float = RETRY_DEADLINE_BACKGROUND,
) -> tuple[str, list[dict[str, Any]], dict]:
    """One tool-calling completion returning ``(text, tool_calls, stats)``.

    :func:`call_llm` drops ``tool_calls`` whenever the model also returns text;
    this keeps both, which agentic loops need when a model narrates while
    proposing calls.
    """
    selected_model_name = normalize_model_name(model) if model else _get_default_model()
    selected_slug = _openrouter_model_slug(selected_model_name)
    completion_kwargs: dict = {
        "model": selected_slug,
        "messages": messages,
        "max_tokens": _effective_max_tokens(selected_model_name, max_tokens),
        "tools": tools,
        "extra_body": {"usage": {"include": True}, "provider": {"require_parameters": True}},
    }
    chain = _fallback_slugs(fallback_models, selected_slug)
    if chain:
        completion_kwargs["extra_body"]["models"] = chain
    try:
        response = _do_openai_completion(
            _openrouter_client(), completion_kwargs, {}, retry_deadline
        )
    except Exception as e:
        raise RuntimeError(f"Error calling LLM: {e}") from e

    message = response.choices[0].message
    text = (message.content or "").strip()
    tool_calls = [tc.model_dump() for tc in (getattr(message, "tool_calls", None) or [])]
    usage = getattr(response, "usage", None)
    stats = {
        "prompt_tokens": _usage_value(usage, "prompt_tokens"),
        "completion_tokens": _usage_value(usage, "completion_tokens"),
        "response_ms": getattr(response, "_response_ms", 0),
        "response_cost": _usage_value(usage, "cost"),
        "cached_tokens": _cached_tokens(usage),
        "served_model": getattr(response, "model", None) or selected_slug,
    }
    return text, tool_calls, stats


def try_json_parsing(json_data: str):
    res = json_repair.loads(json_data)
    if not res:
        raise ValueError(f"Failed to parse JSON: {json_data}")
    return res
