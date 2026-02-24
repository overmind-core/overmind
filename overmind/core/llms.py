import re
from typing import Any
from openai import OpenAI
from litellm import completion
import json
from overmind.config import settings
from overmind.core.model_resolver import TaskType, resolve_model
from pydantic import BaseModel
import json_repair

client = OpenAI(api_key=settings.openai_api_key)

SUPPORTED_LLM_MODELS = [
    {"provider": "openai", "model_name": "gpt-5.2"},
    {"provider": "openai", "model_name": "gpt-5-mini"},
    {"provider": "openai", "model_name": "gpt-5-nano"},
    {"provider": "openai", "model_name": "gpt-5.2-nano"},
    {"provider": "openai", "model_name": "gpt-5.2-pro"},
    {"provider": "openai", "model_name": "gpt-5"},
    {"provider": "openai", "model_name": "gpt-4.1"},
    {"provider": "anthropic", "model_name": "claude-opus-4-6"},
    {"provider": "anthropic", "model_name": "claude-opus-4-5"},
    {"provider": "anthropic", "model_name": "claude-sonnet-4-6"},
    {"provider": "anthropic", "model_name": "claude-sonnet-4-5"},
    {"provider": "anthropic", "model_name": "claude-haiku-4-5"},
    {"provider": "gemini", "model_name": "gemini-3-pro-preview"},
    {"provider": "gemini", "model_name": "gemini-3-flash-preview"},
    {"provider": "gemini", "model_name": "gemini-2.5-flash"},
    {"provider": "gemini", "model_name": "gemini-2.5-flash-lite"},
    {"provider": "gemini", "model_name": "gemini-2.5-pro"},
]
SUPPORTED_LLM_MODEL_NAMES = {item["model_name"] for item in SUPPORTED_LLM_MODELS}
LLM_PROVIDER_BY_MODEL = {
    item["model_name"]: item["provider"] for item in SUPPORTED_LLM_MODELS
}


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


def get_embedding(input_text: str, system_prompt: str | None = None) -> list[float]:
    """
    Get an embedding vector for the given input text using OpenAI's embeddings API.

    Args:
        input_text: The text to get an embedding for
        system_prompt: Not used for embeddings (kept for compatibility)

    Returns:
        The embedding vector as a list of floats
    """
    # Use placeholder API key - in production, this should be set via environment variable
    api_key = "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"

    client = OpenAI(api_key=api_key)

    try:
        response = client.embeddings.create(
            model="text-embedding-3-small", input=input_text, encoding_format="float"
        )

        embedding = response.data[0].embedding
        if embedding is None:
            raise Exception("No embedding received from OpenAI")

        return embedding

    except Exception as e:
        # In production, you might want to log this error and handle it more gracefully
        raise Exception(f"Error getting embedding: {str(e)}")


def call_llm(
    input_text: str,
    system_prompt: str | None = None,
    model: str | None = None,
    response_format: BaseModel | None = None,
    request_kwargs: dict = {},
    messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
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

    Returns:
        tuple: (content, stats_dict) where stats_dict contains:
            - prompt_tokens: int
            - completion_tokens: int
            - response_ms: float
            - response_cost: float
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

        response = completion(**completion_kwargs, **request_kwargs)

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
        stats = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "response_ms": response._response_ms,
            "response_cost": response._hidden_params["response_cost"],
        }

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
