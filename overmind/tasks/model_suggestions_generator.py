"""
Task to generate proactive model recommendations based on agent description.

Runs immediately after agent description creation so users can see which models
to consider before any backtesting jobs are triggered by the scheduler.

Unlike backtesting (which measures real performance on historical spans), these
suggestions are LLM-generated based solely on the agent's purpose and the known
characteristics of each available model.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from celery import shared_task
from pydantic import BaseModel, Field
from sqlalchemy import select, and_
from sqlalchemy.orm.attributes import flag_modified

from overmind.core.llms import call_llm, try_json_parsing
from overmind.core.model_resolver import (
    TaskType,
    get_available_providers,
    resolve_model,
    BACKTEST_MODELS_BY_PROVIDER,
)
from overmind.db.session import get_session_local
from overmind.models.prompts import Prompt
from overmind.tasks.utils.prompts import (
    MODEL_SUGGESTIONS_SYSTEM_PROMPT,
    MODEL_SUGGESTIONS_GENERATION_PROMPT,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model capability descriptions (used to give the LLM context about each model)
# ---------------------------------------------------------------------------

_MODEL_DESCRIPTIONS: dict[str, str] = {
    # OpenAI
    "gpt-5-nano": "Ultra-fast, cheapest OpenAI model. Best for simple classification, extraction, or high-volume tasks where cost matters most.",
    "gpt-5-mini": "Fast and cost-effective OpenAI model with reasoning support. Great for straightforward tasks, Q&A, and moderate-complexity workflows.",
    "gpt-5.2": "OpenAI's most capable balanced model with strong reasoning. Excels at complex multi-step tasks, code generation, and nuanced analysis.",
    # Anthropic
    "claude-haiku-4-5": "Anthropic's fastest and cheapest model. Ideal for simple tasks, high-throughput pipelines, and latency-sensitive interactive use cases.",
    "claude-sonnet-4-6": "Anthropic's best balanced model with adaptive reasoning. Excellent for complex reasoning, structured outputs, and nuanced language tasks.",
    "claude-opus-4-6": "Anthropic's most powerful model. Best for the most demanding tasks requiring deep reasoning, creative writing, or exhaustive analysis.",
    # Google Gemini
    "gemini-2.5-flash-lite": "Google's fastest and cheapest Gemini model. Suited for high-volume simple tasks where speed and cost are priorities.",
    "gemini-2.5-flash": "Fast Gemini model with reasoning support. Good balance of speed and capability for moderate-complexity tasks.",
    "gemini-3-flash-preview": "Google's best balanced fast model with reasoning. Strong at multi-modal tasks, structured extraction, and real-time applications.",
    "gemini-3.1-pro-preview": "Google's most capable Gemini model. Excellent for complex reasoning, large-context tasks, and advanced analysis.",
}


def _format_available_models() -> str:
    """Build a human-readable model list filtered to configured providers."""
    available_providers = get_available_providers()
    lines: list[str] = []
    for provider, models in BACKTEST_MODELS_BY_PROVIDER.items():
        if provider not in available_providers:
            continue
        for model in models:
            description = _MODEL_DESCRIPTIONS.get(model, "")
            lines.append(f"- {model} ({provider}): {description}")
    return "\n".join(lines) if lines else "No models available."


class _ModelRecommendation(BaseModel):
    model: str
    provider: str
    category: str = Field(
        description="One of: best_overall, most_capable, fastest, cheapest"
    )
    reason: str


class _ModelSuggestionsResponse(BaseModel):
    recommendations: list[_ModelRecommendation]
    summary: str


async def _generate_model_suggestions(prompt_id: str) -> dict[str, Any]:
    """
    Generate LLM-based model recommendations for a prompt using its agent description.

    Reads the agent description, formats available models with their characteristics,
    calls the LLM, and stores the result in ``prompt.model_suggestions``.

    Args:
        prompt_id: String ID of the prompt (format: {project_id}_{version}_{slug})

    Returns:
        Dict with the generated suggestions and metadata.
    """
    AsyncSessionLocal = get_session_local()

    logger.info(f"Generating model suggestions for prompt {prompt_id}")

    async with AsyncSessionLocal() as session:
        try:
            project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
            project_uuid = UUID(project_id_str)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid prompt_id format: {e}")
            raise

        prompt_result = await session.execute(
            select(Prompt).where(
                and_(
                    Prompt.project_id == project_uuid,
                    Prompt.version == version,
                    Prompt.slug == slug,
                )
            )
        )
        prompt = prompt_result.scalar_one_or_none()
        if not prompt:
            raise ValueError(f"Prompt not found: {prompt_id}")

        agent_desc_data = prompt.agent_description or {}
        agent_description = agent_desc_data.get("description", "").strip()
        if not agent_description:
            raise ValueError(
                f"Prompt {prompt_id} has no agent description yet — "
                "model suggestions require an agent description to exist first."
            )

        available_models_text = _format_available_models()
        if available_models_text == "No models available.":
            logger.warning(
                f"No backtest models available for prompt {prompt_id} — skipping model suggestions"
            )
            return {
                "prompt_id": prompt_id,
                "skipped": True,
                "reason": "No models available",
            }

        prompt_text = MODEL_SUGGESTIONS_GENERATION_PROMPT.format(
            agent_description=agent_description,
            available_models=available_models_text,
        )

        response, _ = call_llm(
            prompt_text,
            system_prompt=MODEL_SUGGESTIONS_SYSTEM_PROMPT,
            model=resolve_model(TaskType.AGENT_DESCRIPTION),
            response_format=_ModelSuggestionsResponse,
        )

        result_data = try_json_parsing(response)

        recommendations = result_data.get("recommendations", [])
        summary = result_data.get("summary", "")

        if not recommendations:
            raise ValueError("LLM returned no model recommendations")

        suggestions_payload: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "recommendations": recommendations,
            "summary": summary,
        }

        prompt.backtest_model_suggestions = suggestions_payload
        flag_modified(prompt, "backtest_model_suggestions")
        await session.commit()

        logger.info(
            f"Stored {len(recommendations)} model suggestion(s) for prompt {prompt_id}. "
            f"Summary: {summary[:100]}"
        )

        return {
            "prompt_id": prompt_id,
            "recommendations_count": len(recommendations),
            "summary": summary,
        }


@shared_task(name="model_suggestions_generator.generate_model_suggestions")
def generate_model_suggestions_task(prompt_id: str) -> dict[str, Any]:
    """
    Celery task to generate proactive model recommendations for a newly discovered prompt.

    Called immediately after agent description creation so users can see model
    recommendations without waiting for a backtesting job to run.

    Args:
        prompt_id: String ID of the prompt (format: {project_id}_{version}_{slug})

    Returns:
        Dict with generation results.
    """

    async def _run() -> dict[str, Any]:
        from overmind.db.session import dispose_engine

        try:
            result = await _generate_model_suggestions(prompt_id)
            logger.info(f"Generated model suggestions for prompt {prompt_id}")
            return result
        except Exception:
            logger.exception(
                f"Failed to generate model suggestions for prompt {prompt_id}"
            )
            raise
        finally:
            await dispose_engine()

    return asyncio.run(_run())
