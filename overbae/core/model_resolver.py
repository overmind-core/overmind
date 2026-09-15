import logging
import os
from enum import StrEnum

logger = logging.getLogger(__name__)


class TaskType(StrEnum):
    JUDGE_SCORING = "judge_scoring"
    CRITERIA_GENERATION = "criteria_generation"
    EVAL_RECOMMENDATION = "eval_recommendation"
    DEFAULT = "default"


# Every chain is ordered gpt → claude → gemini. Entry one leads; the rest ride on
# the request as OpenRouter's ``models`` array, so a rate limit or an outage fails
# over inside the same request rather than raising.
_FAST_CHAIN = ("gpt-5.6-luna", "claude-sonnet-5", "gemini-3.8-flash")

MODEL_PRIORITY: dict[TaskType, tuple[str, ...]] = {
    TaskType.JUDGE_SCORING: _FAST_CHAIN,
    TaskType.CRITERIA_GENERATION: _FAST_CHAIN,
    TaskType.EVAL_RECOMMENDATION: _FAST_CHAIN,
    TaskType.DEFAULT: _FAST_CHAIN,
}

# Catalog model → OpenRouter slug, verified against openrouter.ai/api/v1/models.
# Anthropic slugs use dots ("claude-sonnet-4.6") where our catalog uses dashes.
OPENROUTER_MODEL_SLUGS: dict[str, str] = {
    "gpt-5.6-luna": "openai/gpt-5.6-luna",
    "gpt-5.4": "openai/gpt-5.4",
    "gpt-5.4-pro": "openai/gpt-5.4-pro",
    "gpt-5.4-mini": "openai/gpt-5.4-mini",
    "gpt-5.4-nano": "openai/gpt-5.4-nano",
    "gpt-5.2": "openai/gpt-5.2",
    "gpt-5.2-pro": "openai/gpt-5.2-pro",
    "gpt-5": "openai/gpt-5",
    "gpt-5-mini": "openai/gpt-5-mini",
    "gpt-5-nano": "openai/gpt-5-nano",
    "gpt-4.1": "openai/gpt-4.1",
    "gpt-5.5": "openai/gpt-5.5",
    "gpt-5.5-pro": "openai/gpt-5.5-pro",
    "claude-opus-4-6": "anthropic/claude-opus-4.6",
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4.6",
    "claude-opus-4-5": "anthropic/claude-opus-4.5",
    "claude-sonnet-4-5": "anthropic/claude-sonnet-4.5",
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
    "claude-opus-5": "anthropic/claude-opus-5",
    "claude-sonnet-5": "anthropic/claude-sonnet-5",
    "claude-fable-5": "anthropic/claude-fable-5",
    "gemini-3.8-flash": "google/gemini-3.8-flash",
    "gemini-3.1-pro-preview": "google/gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite-preview": "google/gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash": "google/gemini-3.5-flash",
    "gemini-3.5-flash-lite": "google/gemini-3.5-flash-lite",
    "gemini-3.6-flash": "google/gemini-3.6-flash",
    "gemini-3-flash-preview": "google/gemini-3-flash-preview",
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    "gemini-2.5-flash-lite": "google/gemini-2.5-flash-lite",
    "gemini-2.5-pro": "google/gemini-2.5-pro",
    "grok-4.20": "x-ai/grok-4.20",
    "deepseek-v3.2": "deepseek/deepseek-v3.2",
}


def openrouter_configured() -> bool:
    """One key reaches every model in the chains, so availability is all or nothing."""
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def model_chain(task: TaskType) -> list[str]:
    """The full priority chain, best first."""
    return list(MODEL_PRIORITY.get(task, MODEL_PRIORITY[TaskType.DEFAULT]))


def resolve_model(task: TaskType) -> str:
    if not openrouter_configured():
        raise RuntimeError(
            f"No LLM API key configured for task '{task.value}'. Set OPENROUTER_API_KEY."
        )
    model = model_chain(task)[0]
    logger.debug("Resolved model for %s: %s", task.value, model)
    return model
