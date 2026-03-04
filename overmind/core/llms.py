import re
from typing import Any
import litellm
import json
from overmind.core.model_resolver import (
    TaskType,
    get_available_providers,
    resolve_model,
)
from pydantic import BaseModel
import json_repair


# Reasoning support: https://docs.litellm.ai/docs/reasoning_content
# - OpenAI (gpt-5*, gpt-4.1): reasoning_effort via Responses API; levels: low, medium, high
# - Anthropic: https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking
#   - Opus 4.6, Sonnet 4.6: adaptive mode (thinking.type="adaptive") + effort; manual/budget_tokens deprecated
#   - Opus 4.5, Sonnet 4.5, Haiku 4.5: manual mode only (thinking.type="enabled", budget_tokens)
# - Gemini: reasoning_effort maps to thinking_level (3+) or thinking_budget (2.5); 3.x adds "minimal"
SUPPORTED_LLM_MODELS = [
    {
        "provider": "openai",
        "model_name": "gpt-5.2",
        "supports_reasoning": True,
        "reasoning_levels": ["low", "medium", "high"],
        "backtesting_preferred": True,
    },
    {
        "provider": "openai",
        "model_name": "gpt-5-mini",
        "supports_reasoning": True,
        "reasoning_levels": ["low", "medium", "high"],
    },
    {
        "provider": "openai",
        "model_name": "gpt-5-nano",
        "supports_reasoning": True,
        "reasoning_levels": ["low", "medium", "high"],
    },
    {
        "provider": "openai",
        "model_name": "gpt-5.2-nano",
        "supports_reasoning": True,
        "reasoning_levels": ["low", "medium", "high"],
    },
    {
        "provider": "openai",
        "model_name": "gpt-5.2-pro",
        "supports_reasoning": True,
        "reasoning_levels": ["low", "medium", "high"],
    },
    {
        "provider": "openai",
        "model_name": "gpt-5",
        "supports_reasoning": True,
        "reasoning_levels": ["low", "medium", "high"],
    },
    {"provider": "openai", "model_name": "gpt-4.1", "supports_reasoning": False},
    # Opus 4.6, Sonnet 4.6: adaptive thinking (effort); "max" is Opus 4.6 only
    {
        "provider": "anthropic",
        "model_name": "claude-opus-4-6",
        "supports_reasoning": True,
        "reasoning_levels": ["low", "medium", "high", "max"],
        "anthropic_reasoning_mode": "adaptive",
    },
    {
        "provider": "anthropic",
        "model_name": "claude-sonnet-4-6",
        "supports_reasoning": True,
        "reasoning_levels": ["low", "medium", "high"],
        "anthropic_reasoning_mode": "adaptive",
        "backtesting_preferred": True,
    },
    # Opus 4.5, Sonnet 4.5, Haiku 4.5: manual thinking (budget_tokens) only; no reasoning_effort
    {
        "provider": "anthropic",
        "model_name": "claude-opus-4-5",
        "supports_reasoning": True,
        "thinking_budget_tokens": [8000],
        "anthropic_reasoning_mode": "manual",
    },
    {
        "provider": "anthropic",
        "model_name": "claude-sonnet-4-5",
        "supports_reasoning": True,
        "thinking_budget_tokens": [8000],
        "anthropic_reasoning_mode": "manual",
    },
    {
        "provider": "anthropic",
        "model_name": "claude-haiku-4-5",
        "supports_reasoning": True,
        "thinking_budget_tokens": [8000],
        "anthropic_reasoning_mode": "manual",
    },
    {
        "provider": "gemini",
        "model_name": "gemini-3.1-pro-preview",
        "supports_reasoning": True,
        "reasoning_levels": ["low", "medium", "high"],
    },
    {
        "provider": "gemini",
        "model_name": "gemini-3-flash-preview",
        "supports_reasoning": True,
        "reasoning_levels": ["minimal", "low", "medium", "high"],
        "backtesting_preferred": True,
    },
    {
        "provider": "gemini",
        "model_name": "gemini-3.1-flash-lite-preview",
        "supports_reasoning": True,
        "reasoning_levels": ["low", "medium", "high"],
    },
    {
        "provider": "gemini",
        "model_name": "gemini-2.5-flash",
        "supports_reasoning": True,
        "thinking_budget_tokens": [8000],
    },
    {
        "provider": "gemini",
        "model_name": "gemini-2.5-flash-lite",
        "supports_reasoning": False,
    },
    {
        "provider": "gemini",
        "model_name": "gemini-2.5-pro",
        "supports_reasoning": True,
        "reasoning_levels": ["low", "medium", "high"],
        "reasoning_required": True,
    },  # cannot disable reasoning
]
SUPPORTED_LLM_MODEL_NAMES = {item["model_name"] for item in SUPPORTED_LLM_MODELS}
LLM_PROVIDER_BY_MODEL = {
    item["model_name"]: item["provider"] for item in SUPPORTED_LLM_MODELS
}
REASONING_SUPPORT_BY_MODEL = {
    item["model_name"]: {
        "supports_reasoning": item["supports_reasoning"],
        "reasoning_levels": item.get("reasoning_levels"),
        "thinking_budget_tokens": item.get("thinking_budget_tokens"),
        "reasoning_required": item.get("reasoning_required", False),
        **(
            {"anthropic_reasoning_mode": item["anthropic_reasoning_mode"]}
            if "anthropic_reasoning_mode" in item
            else {}
        ),
    }
    for item in SUPPORTED_LLM_MODELS
}


def model_supports_reasoning(model_name: str) -> bool:
    """Return True if the model supports reasoning/reasoning_effort."""
    info = REASONING_SUPPORT_BY_MODEL.get(normalize_model_name(model_name))
    return info["supports_reasoning"] if info else False


def get_reasoning_levels(model_name: str) -> list[str]:
    """Return valid reasoning_effort levels for the model, or [] if unsupported."""
    info = REASONING_SUPPORT_BY_MODEL.get(normalize_model_name(model_name))
    if not info or not info["supports_reasoning"]:
        return []
    levels = info.get("reasoning_levels")
    return list(levels) if levels else []


def get_thinking_budget_tokens(model_name: str) -> list[int]:
    """Return valid thinking budget_tokens for Anthropic manual mode, or [] otherwise."""
    info = REASONING_SUPPORT_BY_MODEL.get(normalize_model_name(model_name))
    if not info or info.get("anthropic_reasoning_mode") != "manual":
        return []
    budgets = info.get("thinking_budget_tokens")
    return list(budgets) if budgets else []


def get_anthropic_reasoning_mode(model_name: str) -> str | None:
    """Return 'adaptive' | 'manual' for Anthropic models, None otherwise."""
    info = REASONING_SUPPORT_BY_MODEL.get(normalize_model_name(model_name))
    return info.get("anthropic_reasoning_mode") if info else None


def is_reasoning_required(model_name: str) -> bool:
    """Return True if the model requires reasoning (cannot disable)."""
    info = REASONING_SUPPORT_BY_MODEL.get(normalize_model_name(model_name))
    return info.get("reasoning_required", False) if info else False


def get_backtesting_preferred_models() -> list[str]:
    """Return backtesting_preferred models filtered to providers with API keys."""
    available = get_available_providers()
    return [
        item["model_name"]
        for item in SUPPORTED_LLM_MODELS
        if item.get("backtesting_preferred") and item["provider"] in available
    ]


def _get_default_model() -> str:
    """Lazy default: resolved at call time so the resolver sees current API keys."""
    return resolve_model(TaskType.DEFAULT)


# Pattern to strip date suffixes like "-2025-08-07" from versioned model names
_DATE_SUFFIX_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def normalize_model_name(model_name: str) -> str:
    """Strip date-version suffix (e.g. '-2025-08-07') from a model name.

    Span metadata often stores the fully-qualified model name returned by the
    provider (e.g. ``gpt-5-mini-2025-08-07``).  This helper maps it back to
    the base name (``gpt-5-mini``) so it can be looked up in
    ``SUPPORTED_LLM_MODEL_NAMES``.
    """
    base = _DATE_SUFFIX_RE.sub("", model_name)
    if base in SUPPORTED_LLM_MODEL_NAMES:
        return base
    return model_name  # return as-is if stripping didn't help


def get_embedding(input_text: str) -> list[float]:
    """
    Get an embedding vector for the given input text using OpenAI's embeddings API.

    Args:
        input_text: The text to get an embedding for

    Returns:
        The embedding vector as a list of floats
    """
    try:
        response = litellm.embedding(model="text-embedding-3-small", input=[input_text])
        text_embedding = response.data[0]["embedding"]

        if text_embedding is None:
            raise Exception("No embedding received from OpenAI")

        return text_embedding

    except Exception as e:
        # In production, you might want to log this error and handle it more gracefully
        raise Exception(f"Error getting embedding: {e}")


def call_llm(
    input_text: str,
    system_prompt: str | None = None,
    model: str | None = None,
    response_format: BaseModel | None = None,
    request_kwargs: dict = {},
    messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    reasoning_effort: str | None = None,
    thinking_budget_tokens: int | None = None,
) -> tuple[str, dict]:
    """
    Call an LLM and return the response along with usage metrics.

    When ``messages`` is provided it is used directly, bypassing the default
    construction from ``input_text`` / ``system_prompt``.  This allows callers
    to replay a full conversation (including tool-result turns).

    When ``tools`` is provided the tool definitions are forwarded to the
    provider so the model can make tool-call decisions.  If the model responds
    with tool calls instead of plain text, the tool calls are serialised to a
    JSON string and returned as the content.

    When ``reasoning_effort`` is provided and the model supports it (adaptive
    mode, OpenAI, Gemini), it is passed to LiteLLM. For Anthropic manual mode
    (Opus 4.5, Sonnet 4.5, Haiku 4.5): use ``thinking_budget_tokens`` instead;
    reasoning_effort is not supported. Manual mode passes
    thinking={"type":"enabled","budget_tokens":N}. For Anthropic with tools,
    LiteLLM's modify_params workaround is enabled to handle missing
    thinking_blocks in multi-turn tool calls.

    Returns:
        tuple: (content, stats_dict) where stats_dict contains:
            - prompt_tokens: int
            - completion_tokens: int
            - response_ms: float
            - response_cost: float
            - reasoning_content: str | None (if model returned reasoning)
    """
    if messages is None:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": input_text})

    try:
        selected_model_name = (
            normalize_model_name(model) if model else _get_default_model()
        )
        if selected_model_name not in SUPPORTED_LLM_MODEL_NAMES:
            raise ValueError(f"Unsupported model: {selected_model_name}")

        provider = LLM_PROVIDER_BY_MODEL.get(selected_model_name)
        selected_model = f"{provider}/{selected_model_name}"

        completion_kwargs: dict = {
            "model": selected_model,
            "messages": messages,
            "max_tokens": 5000,
            "response_format": response_format,
        }

        if tools:
            completion_kwargs["tools"] = tools

        if provider == "anthropic":
            completion_kwargs["cache_control"] = {"type": "ephemeral"}

        prev_modify_params: bool | None = None
        mode = get_anthropic_reasoning_mode(selected_model_name)
        effective_reasoning_effort = reasoning_effort
        if effective_reasoning_effort is None and is_reasoning_required(
            selected_model_name
        ):
            effective_reasoning_effort = "medium"

        if mode == "manual" and thinking_budget_tokens is not None:
            budgets = get_thinking_budget_tokens(selected_model_name)
            if thinking_budget_tokens in budgets:
                completion_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking_budget_tokens,
                }
                if tools:
                    prev_modify_params = getattr(litellm, "modify_params", False)
                    litellm.modify_params = True
        elif effective_reasoning_effort and model_supports_reasoning(
            selected_model_name
        ):
            levels = get_reasoning_levels(selected_model_name)
            if levels and effective_reasoning_effort in levels:
                completion_kwargs["reasoning_effort"] = effective_reasoning_effort
                if provider == "anthropic" and tools:
                    prev_modify_params = getattr(litellm, "modify_params", False)
                    litellm.modify_params = True

        try:
            response = litellm.completion(**completion_kwargs, **request_kwargs)
        finally:
            if prev_modify_params is not None:
                litellm.modify_params = prev_modify_params

        content = response.choices[0].message.content
        if content is None:
            # Model responded with tool calls instead of plain text
            tool_calls = getattr(response.choices[0].message, "tool_calls", None)
            if tool_calls:
                content = json.dumps(
                    {"tool_calls": [tc.model_dump() for tc in tool_calls]}
                )
            else:
                raise Exception("No content or tool calls received from LLM")

        # Extract usage metrics
        stats: dict = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "response_ms": response._response_ms,
            "response_cost": response._hidden_params["response_cost"],
        }
        reasoning_content = getattr(
            response.choices[0].message, "reasoning_content", None
        )
        if reasoning_content:
            stats["reasoning_content"] = reasoning_content

        return content.strip(), stats

    except Exception as e:
        raise Exception(f"Error calling LLM: {str(e)}")


def normalize_llm_response_output(response: str) -> str:
    """Normalize a raw ``call_llm`` response to the auto-captured span format.

    Auto-captured spans store output as a JSON string containing a list of
    message objects::

        [{"role": "assistant", "content": "..." | null, "tool_calls": [...]}]

    ``call_llm`` returns either:
    - A plain text string for text responses.
    - A JSON string ``{"tool_calls": [...]}`` when the model makes tool calls.

    Both are converted to the list-of-messages format so that prompt-tuning
    and backtesting spans render identically to collected traces in the UI.
    """
    try:
        parsed = json.loads(response)
        if isinstance(parsed, dict) and "tool_calls" in parsed:
            return json.dumps(
                [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": parsed["tool_calls"],
                    }
                ]
            )
        else:
            return json.dumps([{"role": "assistant", "content": response}])
    except (json.JSONDecodeError, TypeError):
        return json.dumps([{"role": "assistant", "content": response}])


def try_json_parsing(json_data: str):
    res = json_repair.loads(json_data)
    if not res:
        raise ValueError(f"Failed to parse JSON: {json_data}")
    return res
