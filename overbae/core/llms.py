import json
import logging
import os
import time
from collections.abc import Iterator
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

from modal_shared.context_budget import INCOMPLETE_FINISH_REASONS
from modal_shared.modelfam import serve_image_key
from modal_shared.shared import routing_headers as _modal_routing_headers
from overbae.core.model_registry import (
    PROVIDERS,
    Provider,
    TaskType,
    is_decision_model,
    normalize_model_name,
    openrouter_slug,
    reasoning_of,
    resolve_model,
)
from overbae.models import DeployedModel

logger = logging.getLogger(__name__)

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


class IncompleteCompletionError(RuntimeError):
    def __init__(self, content: str, stats: dict):
        super().__init__("The model reached its output token limit before completing the response.")
        self.content = content
        self.stats = stats


@lru_cache(maxsize=8)
def _provider_client(name: str) -> OpenAI:
    provider = PROVIDERS[name]
    api_key = provider.key()
    if not api_key:
        raise RuntimeError(f"{provider.key_env} is required for LLM completions")
    return OpenAI(
        api_key=api_key,
        base_url=provider.base_url,
        default_headers=provider.headers or None,
        timeout=_REQUEST_TIMEOUT,
    )


def _openrouter_client() -> OpenAI:
    return _provider_client("openrouter")


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

    slug = openrouter_slug(spec.model_id)
    return _openrouter_client(), slug, "openrouter"


def _effective_max_tokens(model_name: str, max_tokens: int) -> int:
    """``max_tokens`` stays the caller's intended visible-output size; reasoning
    models get extra room so hidden reasoning cannot starve the answer.
    """
    if reasoning_of(model_name).adaptive is not None:
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
        elif getattr(response.choices[0], "finish_reason", None) in INCOMPLETE_FINISH_REASONS:
            content = ""
        else:
            raise ValueError("No content or tool calls received from LLM")

    usage = getattr(response, "usage", None)
    stats: dict = {
        "finish_reason": getattr(response.choices[0], "finish_reason", None),
        "prompt_tokens": _usage_value(usage, "prompt_tokens"),
        "completion_tokens": _usage_value(usage, "completion_tokens"),
        "response_ms": getattr(response, "_response_ms", 0),
        "response_cost": _usage_value(usage, "cost", None),
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

    if stats["finish_reason"] in INCOMPLETE_FINISH_REASONS:
        raise IncompleteCompletionError(content.strip(), stats)
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
            slug = openrouter_slug(name)
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
    reasoning = reasoning_of(selected_model_name)
    effective_reasoning_effort = reasoning_effort
    if effective_reasoning_effort is None and reasoning.required:
        effective_reasoning_effort = "medium"

    if reasoning.adaptive is False and thinking_budget_tokens is not None:
        if thinking_budget_tokens in reasoning.budgets and thinking_budget_tokens > 0:
            return {"reasoning": {"max_tokens": thinking_budget_tokens}}
        return None

    if reasoning.adaptive is True and effective_reasoning_effort in reasoning.levels:
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
    if any(
        is_decision_model(name)
        for name in [
            model or "",
            model_spec.model_id if model_spec else "",
            *(fallback_models or []),
        ]
    ):
        raise ValueError("Decision models require typed questions through the decision transport.")
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
            if "max_tokens" in request_kwargs:
                cap = request_kwargs.pop("max_tokens")
                if cap is not None:
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
        selected_model = openrouter_slug(selected_model_name)
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

    except IncompleteCompletionError:
        raise
    except Exception as e:
        raise RuntimeError(f"Error calling LLM: {e}") from e


# OpenAI rejects unknown keys; cache_control and reasoning_details are OpenRouter-only.
def _portable_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "system" and isinstance(message.get("content"), list):
            text = "\n".join(
                str(part.get("text") or "") for part in message["content"] if isinstance(part, dict)
            )
            out.append({**message, "content": text})
        elif "reasoning_details" in message:
            out.append({k: v for k, v in message.items() if k != "reasoning_details"})
        else:
            out.append(message)
    return out


def _tool_completion_kwargs(
    provider: Provider,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model: str | None,
    max_tokens: int,
    fallback_models: list[str] | None,
    reasoning_effort: str | None = None,
) -> tuple[dict, str]:
    selected_model_name = normalize_model_name(model) if model else _get_default_model()
    selected = (
        openrouter_slug(selected_model_name) if provider.openrouter_extras else selected_model_name
    )
    completion_kwargs: dict = {
        "model": selected,
        "messages": messages if provider.openrouter_extras else _portable_messages(messages),
        provider.output_cap_param: _effective_max_tokens(selected_model_name, max_tokens),
    }
    # An empty tools array is a 400 on some providers.
    if tools:
        completion_kwargs["tools"] = tools
    if provider.openrouter_extras:
        extra_body: dict = {"usage": {"include": True}, "provider": {"require_parameters": True}}
        reasoning_body = _reasoning_extra_body(selected_model_name, reasoning_effort, None)
        if reasoning_body:
            extra_body.update(reasoning_body)
        chain = _fallback_slugs(fallback_models, selected)
        if chain:
            extra_body["models"] = chain
        completion_kwargs["extra_body"] = extra_body
    elif tools and provider.tools_reasoning_effort:
        completion_kwargs["reasoning_effort"] = provider.tools_reasoning_effort
    elif (
        provider.reasoning_effort
        and reasoning_effort
        and reasoning_effort in reasoning_of(selected_model_name).levels
    ):
        completion_kwargs["reasoning_effort"] = reasoning_effort
    return completion_kwargs, selected


def _tool_call_stats(usage: Any, served_model: Any, selected: str, response_ms: Any) -> dict:
    return {
        "prompt_tokens": _usage_value(usage, "prompt_tokens"),
        "completion_tokens": _usage_value(usage, "completion_tokens"),
        "response_ms": response_ms or 0,
        "response_cost": _usage_value(usage, "cost", None),
        "cached_tokens": _cached_tokens(usage),
        "served_model": served_model or selected,
    }


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
    provider = PROVIDERS["openrouter"]
    completion_kwargs, selected = _tool_completion_kwargs(
        provider, messages, tools, model, max_tokens, fallback_models
    )
    try:
        response = _do_openai_completion(
            _provider_client(provider.name), completion_kwargs, {}, retry_deadline
        )
    except Exception as e:
        raise RuntimeError(f"Error calling LLM: {e}") from e

    message = response.choices[0].message
    text = (message.content or "").strip()
    tool_calls = [tc.model_dump() for tc in (getattr(message, "tool_calls", None) or [])]
    stats = _tool_call_stats(
        getattr(response, "usage", None),
        getattr(response, "model", None),
        selected,
        getattr(response, "_response_ms", 0),
    )
    stats["finish_reason"] = getattr(response.choices[0], "finish_reason", None)
    if stats["finish_reason"] in INCOMPLETE_FINISH_REASONS:
        raise IncompleteCompletionError(text, stats)
    return text, tool_calls, stats


def _merge_tool_call_delta(acc: dict[int, dict], delta: Any) -> None:
    for part in delta or ():
        index = getattr(part, "index", 0) or 0
        call = acc.setdefault(
            index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        )
        if getattr(part, "id", None):
            call["id"] = part.id
        function = getattr(part, "function", None)
        if function is None:
            continue
        if getattr(function, "name", None):
            call["function"]["name"] = function.name
        if getattr(function, "arguments", None):
            call["function"]["arguments"] += function.arguments


def _merge_reasoning_details(acc: list[dict], parts: Any) -> None:
    for part in parts or ():
        if not isinstance(part, dict):
            part = getattr(part, "model_dump", lambda: {})()
        if not isinstance(part, dict):
            continue
        index = part.get("index")
        target = next((d for d in acc if index is not None and d.get("index") == index), None)
        if target is None:
            acc.append(dict(part))
            continue
        for key, value in part.items():
            if key in ("text", "summary", "data") and isinstance(value, str):
                target[key] = str(target.get(key) or "") + value
            elif value is not None:
                target[key] = value


@dataclass(frozen=True)
class ToolStreamDelta:
    kind: str  # text | reasoning
    text: str


@dataclass
class ToolStreamResult:
    text: str
    tool_calls: list[dict[str, Any]]
    stats: dict[str, Any]
    reasoning: str = ""
    reasoning_details: list[dict[str, Any]] = field(default_factory=list)

    def assistant_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.text or None}
        if self.tool_calls:
            message["tool_calls"] = self.tool_calls
        if self.reasoning_details:
            message["reasoning_details"] = self.reasoning_details
        return message


def stream_llm_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model: str | None = None,
    max_tokens: int = 4000,
    fallback_models: list[str] | None = None,
    retry_deadline: float = RETRY_DEADLINE_BACKGROUND,
    reasoning_effort: str | None = None,
    provider: Provider | None = None,
) -> Iterator[ToolStreamDelta | ToolStreamResult]:
    provider = provider or PROVIDERS["openrouter"]
    completion_kwargs, selected = _tool_completion_kwargs(
        provider, messages, tools, model, max_tokens, fallback_models, reasoning_effort
    )
    completion_kwargs["stream"] = True
    completion_kwargs["stream_options"] = {"include_usage": True}

    started = time.monotonic()
    try:
        stream = _do_openai_completion(
            _provider_client(provider.name), completion_kwargs, {}, retry_deadline
        )
    except Exception as e:
        raise RuntimeError(f"Error calling LLM: {e}") from e

    parts: list[str] = []
    thoughts: list[str] = []
    details: list[dict] = []
    calls: dict[int, dict] = {}
    usage = None
    served_model = None
    finish_reason = None
    try:
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if getattr(chunk, "model", None):
                served_model = chunk.model
            choices = getattr(chunk, "choices", None) or ()
            if not choices:
                continue
            finish_reason = getattr(choices[0], "finish_reason", None) or finish_reason
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            extra = _extra(delta)
            thought = extra.get("reasoning") or getattr(delta, "reasoning", None)
            if isinstance(thought, str) and thought:
                thoughts.append(thought)
                yield ToolStreamDelta("reasoning", thought)
            _merge_reasoning_details(details, extra.get("reasoning_details"))
            text = getattr(delta, "content", None)
            if text:
                parts.append(text)
                yield ToolStreamDelta("text", text)
            _merge_tool_call_delta(calls, getattr(delta, "tool_calls", None))
    except Exception as e:
        raise RuntimeError(f"Error streaming LLM: {e}") from e
    finally:
        with suppress(Exception):
            stream.close()

    tool_calls = [calls[index] for index in sorted(calls)]
    stats = _tool_call_stats(usage, served_model, selected, (time.monotonic() - started) * 1000)
    stats["finish_reason"] = finish_reason
    if finish_reason in INCOMPLETE_FINISH_REASONS:
        raise IncompleteCompletionError("".join(parts).strip(), stats)
    yield ToolStreamResult(
        "".join(parts).strip(), tool_calls, stats, "".join(thoughts).strip(), details
    )


def try_json_parsing(json_data: str):
    res = json_repair.loads(json_data)
    if not res:
        raise ValueError(f"Failed to parse JSON: {json_data}")
    return res
