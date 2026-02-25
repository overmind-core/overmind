from datetime import datetime
import logging

import litellm

logger = logging.getLogger(__name__)


def to_nano(timestamp: datetime) -> int:
    return int(timestamp.timestamp() * 1_000_000_000)


def safe_int(value, default: int = 0) -> int:
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
    if not model_name:
        return 0.0
    try:
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model_name,
            prompt_tokens=int(input_tokens),
            completion_tokens=int(output_tokens),
        )
        return round(prompt_cost + completion_cost, 8)
    except Exception:
        logger.warning(f"Unknown model for LLM cost calculation: {model_name}")
        return 0.0
