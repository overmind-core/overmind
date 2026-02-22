"""Central model resolver that picks the best available LLM for each task type.

Each task type has a priority-ordered list of (model, provider) pairs.
``resolve_model`` walks the list and returns the first model whose provider
has an API key configured in the environment.  This lets the product work
with any single provider while always preferring the best model for the job.
"""

import logging
from enum import Enum
from typing import Dict, List, Set, Tuple

from overmind_core.config import settings

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    JUDGE_SCORING = "judge_scoring"
    PROMPT_TUNING = "prompt_tuning"
    CRITERIA_GENERATION = "criteria_generation"
    AGENT_DESCRIPTION = "agent_description"
    DEFAULT = "default"


# Priority-ordered model lists per task.
# First model whose provider has a configured API key wins.
MODEL_PRIORITY: Dict[TaskType, List[Tuple[str, str]]] = {
    TaskType.JUDGE_SCORING: [
        ("gpt-5-mini", "openai"),
        ("gemini-3-flash-preview", "gemini"),
        ("claude-haiku-4-5", "anthropic"),
    ],
    TaskType.PROMPT_TUNING: [
        ("claude-sonnet-4-6", "anthropic"),
        ("gpt-5.2", "openai"),
        ("gemini-3-pro-preview", "gemini"),
    ],
    TaskType.CRITERIA_GENERATION: [
        ("claude-sonnet-4-6", "anthropic"),
        ("gpt-5.2", "openai"),
        ("gemini-3-pro-preview", "gemini"),
    ],
    TaskType.AGENT_DESCRIPTION: [
        ("claude-sonnet-4-6", "anthropic"),
        ("gpt-5-mini", "openai"),
        ("gemini-3-flash-preview", "gemini"),
    ],
    TaskType.DEFAULT: [
        ("gpt-5-mini", "openai"),
        ("gemini-3-flash-preview", "gemini"),
        ("claude-haiku-4-5", "anthropic"),
    ],
}

# Backtest models grouped by provider.  get_available_backtest_models()
# filters this to providers that have an API key.
BACKTEST_MODELS_BY_PROVIDER: Dict[str, List[str]] = {
    "openai": ["gpt-5-mini", "gpt-5.2", "gpt-5-nano"],
    "anthropic": ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"],
    "gemini": [
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
    ],
}


def get_available_providers() -> Set[str]:
    """Return providers that have a non-empty API key configured."""
    available: Set[str] = set()
    if settings.openai_api_key:
        available.add("openai")
    if settings.anthropic_api_key:
        available.add("anthropic")
    if settings.gemini_api_key:
        available.add("gemini")
    return available


def resolve_model(task: TaskType) -> str:
    """Return the best available model for *task*.

    Walks the priority list and returns the first model whose provider
    has an API key configured.  Raises ``RuntimeError`` when no
    provider is available at all.
    """
    available = get_available_providers()
    priority = MODEL_PRIORITY.get(task, MODEL_PRIORITY[TaskType.DEFAULT])

    for model_name, provider in priority:
        if provider in available:
            logger.debug(
                "Resolved model for %s: %s (provider=%s)", task.value, model_name, provider
            )
            return model_name

    raise RuntimeError(
        f"No LLM API key configured for task '{task.value}'. "
        "Set at least one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY"
    )


def get_available_backtest_models() -> List[str]:
    """Return the default backtest model list filtered to available providers."""
    available = get_available_providers()
    models: List[str] = []
    for provider, provider_models in BACKTEST_MODELS_BY_PROVIDER.items():
        if provider in available:
            models.extend(provider_models)
    return models
