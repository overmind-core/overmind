from collections import namedtuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def to_nano(timestamp: datetime) -> int:
    return int(timestamp.timestamp() * 1_000_000_000)


LLMInfo = namedtuple("LLMInfo", ["model_name", "input_cost", "output_cost"])

LLM_COSTS_BY_MODEL = {
    "gpt-5.2": LLMInfo(
        model_name="gpt-5.2",
        input_cost=1.75,
        output_cost=14,
    ),
    "gpt-5.1": LLMInfo(
        model_name="gpt-5.1",
        input_cost=1.25,
        output_cost=10,
    ),
    "gpt-5": LLMInfo(
        model_name="gpt-5",
        input_cost=1.25,
        output_cost=10,
    ),
    "gpt-5-mini": LLMInfo(
        model_name="gpt-5-mini",
        input_cost=0.25,
        output_cost=2,
    ),
    "gpt-5-nano": LLMInfo(
        model_name="gpt-5-nano",
        input_cost=0.05,
        output_cost=0.4,
    ),
    "gpt-5.2-chat-latest": LLMInfo(
        model_name="gpt-5.2-chat-latest",
        input_cost=1.75,
        output_cost=14,
    ),
    "gpt-5.1-chat-latest": LLMInfo(
        model_name="gpt-5.1-chat-latest",
        input_cost=1.25,
        output_cost=10,
    ),
    "gpt-5-chat-latest": LLMInfo(
        model_name="gpt-5-chat-latest",
        input_cost=1.25,
        output_cost=10,
    ),
    "gpt-5.2-codex": LLMInfo(
        model_name="gpt-5.2-codex",
        input_cost=1.75,
        output_cost=14,
    ),
    "gpt-5.1-codex-max": LLMInfo(
        model_name="gpt-5.1-codex-max",
        input_cost=1.25,
        output_cost=10,
    ),
    "gpt-5.1-codex": LLMInfo(
        model_name="gpt-5.1-codex",
        input_cost=1.25,
        output_cost=10,
    ),
    "gpt-5-codex": LLMInfo(
        model_name="gpt-5-codex",
        input_cost=1.25,
        output_cost=10,
    ),
    "gpt-5.2-pro": LLMInfo(
        model_name="gpt-5.2-pro",
        input_cost=21,
        output_cost=168,
    ),
    "gpt-5-pro": LLMInfo(
        model_name="gpt-5-pro",
        input_cost=15,
        output_cost=120,
    ),
    "gpt-4.1": LLMInfo(
        model_name="gpt-4.1",
        input_cost=2,
        output_cost=8,
    ),
    "gpt-4.1-mini": LLMInfo(
        model_name="gpt-4.1-mini",
        input_cost=0.4,
        output_cost=1.6,
    ),
    "gpt-4.1-nano": LLMInfo(
        model_name="gpt-4.1-nano",
        input_cost=0.1,
        output_cost=0.4,
    ),
    "gpt-4o": LLMInfo(
        model_name="gpt-4o",
        input_cost=2.5,
        output_cost=10,
    ),
    "gpt-4o-2024-05-13": LLMInfo(
        model_name="gpt-4o-2024-05-13",
        input_cost=5,
        output_cost=15,
    ),
    "gpt-4o-mini": LLMInfo(
        model_name="gpt-4o-mini",
        input_cost=0.15,
        output_cost=0.6,
    ),
    "claude-opus-4-6": LLMInfo(
        model_name="claude-opus-4-6",
        input_cost=5,
        output_cost=25,
    ),
    "claude-opus-4-5-20251101": LLMInfo(
        model_name="claude-opus-4-5-20251101",
        input_cost=5,
        output_cost=25,
    ),
    "claude-haiku-4-5-20251001": LLMInfo(
        model_name="claude-haiku-4-5-20251001",
        input_cost=1,
        output_cost=5,
    ),
    "claude-sonnet-4-6": LLMInfo(
        model_name="claude-sonnet-4-6",
        input_cost=3,
        output_cost=15,
    ),
    "claude-sonnet-4-5": LLMInfo(
        model_name="claude-sonnet-4-5",
        input_cost=5,
        output_cost=25,
    ),
    "claude-sonnet-4-5-20250929": LLMInfo(
        model_name="claude-sonnet-4-5-20250929",
        input_cost=3,
        output_cost=15,
    ),
    "claude-opus-4-1-20250805": LLMInfo(
        model_name="claude-opus-4-1-20250805",
        input_cost=15,
        output_cost=75,
    ),
    "claude-opus-4-20250514": LLMInfo(
        model_name="claude-opus-4-20250514",
        input_cost=15,
        output_cost=75,
    ),
    "claude-sonnet-4-20250514": LLMInfo(
        model_name="claude-sonnet-4-20250514",
        input_cost=3,
        output_cost=15,
    ),
    "claude-3-7-sonnet-20250219": LLMInfo(
        model_name="claude-3-7-sonnet-20250219",
        input_cost=3,
        output_cost=15,
    ),
    "claude-3-5-haiku-20241022": LLMInfo(
        model_name="claude-3-5-haiku-20241022",
        input_cost=0.8,
        output_cost=4,
    ),
    "claude-3-haiku-20240307": LLMInfo(
        model_name="claude-3-haiku-20240307",
        input_cost=0.25,
        output_cost=1.25,
    ),
    "gemini-3-pro-preview": LLMInfo(
        model_name="gemini-3-pro-preview",
        input_cost=2,
        output_cost=12,
    ),
    "gemini-3-flash-preview": LLMInfo(
        model_name="gemini-3-flash-preview",
        input_cost=0.50,
        output_cost=3,
    ),
    "gemini-3-pro-image-preview": LLMInfo(
        model_name="gemini-3-pro-image-preview",
        input_cost=2,
        output_cost=12,
    ),
    "gemini-2.5-pro": LLMInfo(
        model_name="gemini-2.5-pro",
        input_cost=1.25,
        output_cost=10,
    ),
    "gemini-2.5-flash": LLMInfo(
        model_name="gemini-2.5-flash",
        input_cost=0.3,
        output_cost=2.5,
    ),
    "gemini-2.5-flash-preview-09-2025": LLMInfo(
        model_name="gemini-2.5-flash-preview-09-2025",
        input_cost=0.3,
        output_cost=2.5,
    ),
    "gemini-2.5-flash-lite": LLMInfo(
        model_name="gemini-2.5-flash-lite",
        input_cost=0.1,
        output_cost=0.4,
    ),
    "gemini-2.5-flash-lite-preview-09-2025": LLMInfo(
        model_name="gemini-2.5-flash-lite-preview-09-2025",
        input_cost=0.10,
        output_cost=0.4,
    ),
    "gemini-2.5-flash-preview-tts": LLMInfo(
        model_name="gemini-2.5-flash-preview-tts",
        input_cost=0.5,
        output_cost=10,
    ),
    "gemini-2.5-pro-preview-tts": LLMInfo(
        model_name="gemini-2.5-pro-preview-tts",
        input_cost=1,
        output_cost=20,
    ),
    "gemini-2.0-flash": LLMInfo(
        model_name="gemini-2.0-flash",
        input_cost=0.10,
        output_cost=0.4,
    ),
    "gemini-2.0-flash-lite": LLMInfo(
        model_name="gemini-2.0-flash-lite",
        input_cost=0.075,
        output_cost=0.3,
    ),
}


_1M_TOKEN = 1_000_000


def _safe_int(value, default: int = 0) -> int:
    """Convert *value* to int, returning *default* on any error.

    Handles int, float, numeric strings like "1500", and broken legacy values
    like "False" that were created by the old OTLP attribute parser (int_value=0
    gets serialised as str(False)).
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def calculate_llm_usage_cost(
    model_name: str, input_tokens: int, output_tokens: int
) -> float:
    if not model_name or model_name not in LLM_COSTS_BY_MODEL:
        if model_name:
            logger.warning(f"Unknown model for LLM cost calculation: {model_name}")
        return 0
    try:
        info = LLM_COSTS_BY_MODEL[model_name]
        return round(
            (
                int(input_tokens) * info.input_cost
                + int(output_tokens) * info.output_cost
            )
            / _1M_TOKEN,
            8,
        )  # upto 8 decimal places
    except Exception as e:
        logger.error(f"Error calculating LLM usage cost for model {model_name}: {e}")
        return 0
