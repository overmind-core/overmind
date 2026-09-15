"""Non-finetuned models available via OpenRouter, all with ``finetuned=False``.

Curation rules: closed-source entries are the latest generation per provider,
with no mini/fast/nano variants and no reasoning-only models; every id must be
confirmed live on OpenRouter and use OpenRouter's casing.

The lists here are the single source of truth — ``NON_FINETUNED_PREFIXES`` and
``is_non_finetuned_model`` derive from them and are never maintained by hand.
"""

from __future__ import annotations

_EXTERNAL_MODELS: list[dict] = [
    # Anthropic — Fable (flagship agentic), Sonnet (balanced), Opus (max)
    {"id": "anthropic/claude-fable-5", "owned_by": "anthropic"},
    {"id": "anthropic/claude-sonnet-5", "owned_by": "anthropic"},
    {"id": "anthropic/claude-opus-5", "owned_by": "anthropic"},
    # OpenAI — Sol (flagship), Terra (balanced); no Luna (fast tier)
    {"id": "openai/gpt-5.6-sol", "owned_by": "openai"},
    {"id": "openai/gpt-5.6-terra", "owned_by": "openai"},
    # Google — latest Pro; kept 2.5 Pro as it is widely GA and still current
    {"id": "google/gemini-3.1-pro-preview", "owned_by": "google"},
    {"id": "google/gemini-2.5-pro", "owned_by": "google"},
    {"id": "x-ai/grok-4.20", "owned_by": "x-ai"},
    # DeepSeek — latest general-purpose chat model (no R1 reasoning model)
    {"id": "deepseek/deepseek-v3.2", "owned_by": "deepseek"},
]

# Open-weight models we also fine-tune for, so users can compare base against
# fine-tuned behaviour through one API.
_BASE_MODELS: list[dict] = [
    # Meta-Llama — 3B compact, 8B small, 70B large
    {"id": "meta-llama/llama-3.2-3b-instruct", "owned_by": "meta-llama"},
    {"id": "meta-llama/llama-3.1-8b-instruct", "owned_by": "meta-llama"},
    {"id": "meta-llama/llama-3.3-70b-instruct", "owned_by": "meta-llama"},
    {"id": "qwen/qwen3-8b", "owned_by": "qwen"},
    {"id": "qwen/qwen3-14b", "owned_by": "qwen"},
    {"id": "qwen/qwen3-32b", "owned_by": "qwen"},
]

NON_FINETUNED_MODELS: list[dict] = _EXTERNAL_MODELS + _BASE_MODELS

NON_FINETUNED_PREFIXES: frozenset[str] = frozenset(
    m["id"].split("/")[0] + "/" for m in NON_FINETUNED_MODELS
)


def is_non_finetuned_model(model_id: str) -> bool:
    return any(model_id.startswith(p) for p in NON_FINETUNED_PREFIXES)


def external_frontier_models() -> list[dict]:
    """Closed-source models only. The public /library catalog lists these as
    inference-only cards; the open-weight base models are excluded because
    models.json already lists them with fine-tuning support.
    """
    return list(_EXTERNAL_MODELS)


def non_finetuned_models_response() -> list[dict]:
    """Return the model list in the OpenAI /v1/models response format."""
    return [
        {
            "id": m["id"],
            "object": "model",
            "created": 0,
            "owned_by": m["owned_by"],
            "finetuned": False,
        }
        for m in NON_FINETUNED_MODELS
    ]
