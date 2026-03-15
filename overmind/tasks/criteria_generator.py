"""
Task to auto-generate evaluation criteria for prompts based on their linked spans.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

from celery import shared_task
from pydantic import BaseModel, Field
from sqlalchemy import select, and_

from overmind.db.session import get_session_local
from overmind.models.prompts import Prompt
from overmind.core.llms import call_llm, try_json_parsing
from overmind.core.model_resolver import TaskType, resolve_model
from overmind.tasks.utils.criteria import (
    format_spans_as_examples,
    get_project_description,
    get_spans_for_prompt,
)
from overmind.tasks.utils.prompts import (
    CRITERIA_GENERATION_SYSTEM_PROMPT,
    CRITERIA_GENERATION_PROMPT,
    AGENTIC_NOTE_FOR_CRITERIA,
)
from overmind.tasks.agentic_span_processor import detect_agentic_span

logger = logging.getLogger(__name__)


class CriteriaResponse(BaseModel):
    correctness: list[str] = Field(description="List of correctness rules")


async def _store_criteria_to_prompt(
    prompt_id: str, criteria: dict[str, list[str]]
) -> bool:
    """Store generated criteria in evaluation_criteria field."""
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        try:
            project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
            project_uuid = UUID(project_id_str)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid prompt_id format: {e}")
            return False

        result = await session.execute(
            select(Prompt).where(
                and_(
                    Prompt.project_id == project_uuid,
                    Prompt.version == version,
                    Prompt.slug == slug,
                )
            )
        )
        prompt = result.scalar_one_or_none()

        if prompt is None:
            logger.warning(f"Prompt not found for id={prompt_id}")
            return False

        prompt.evaluation_criteria = criteria
        await session.commit()
        return True


async def _generate_criteria_for_prompt(prompt_id: str) -> dict[str, Any]:
    """
    Generate evaluation criteria for a prompt using its linked spans and project context.
    Generates up to 5 specific rules for correctness evaluation.
    Automatically detects agentic spans and includes appropriate guidance.
    """
    # Get first 10 spans linked to this prompt
    spans = await get_spans_for_prompt(prompt_id, limit=10)

    if not spans:
        raise ValueError(f"No spans found for prompt {prompt_id}")

    if len(spans) < 3:
        logger.warning(
            f"Only {len(spans)} spans found for prompt {prompt_id}. Consider adding more examples."
        )

    # Get project description for context
    try:
        project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
        project_uuid = UUID(project_id_str)
        project_description = await get_project_description(project_uuid)
    except (ValueError, TypeError) as e:
        logger.error(f"Invalid prompt_id format: {e}")
        project_description = "No project description available."

    # Detect if any spans are agentic
    has_agentic_spans = False
    for span in spans:
        is_agentic = detect_agentic_span(
            input_data=span.input or {},
            output_data=span.output or {},
            metadata=span.metadata_attributes or {},
        )
        if is_agentic:
            has_agentic_spans = True
            logger.info(f"Detected agentic span {span.span_id} for criteria generation")
            break

    # Format spans as examples (include judge feedback for criteria adjustment)
    examples_text = format_spans_as_examples(spans)
    has_judge_feedback = any(
        (s.feedback_score or {}).get("judge_feedback") for s in spans
    )
    judge_feedback_note = (
        "\n\n<UserFeedbackNote>\nSome examples include user feedback on the Overmind Judge scoring. "
        "Use this feedback to adjust the evaluation criteria - users indicate where the Judge's assessment was incorrect.\n</UserFeedbackNote>"
        if has_judge_feedback
        else ""
    )

    # Add agentic note if spans include tool calls
    agentic_note = AGENTIC_NOTE_FOR_CRITERIA if has_agentic_spans else ""

    # Generate criteria using LLM
    prompt_text = CRITERIA_GENERATION_PROMPT.format(
        project_description=project_description,
        examples=examples_text,
        judge_feedback_note=judge_feedback_note,
        agentic_note=agentic_note,
    )

    response, _ = call_llm(
        prompt_text,
        system_prompt=CRITERIA_GENERATION_SYSTEM_PROMPT,
        model=resolve_model(TaskType.CRITERIA_GENERATION),
        response_format=CriteriaResponse,
    )

    # Parse the response
    result_data = try_json_parsing(response)

    if "correctness" not in result_data or not isinstance(
        result_data["correctness"], list
    ):
        raise ValueError("Invalid criteria format received from LLM")

    criteria = {"correctness": result_data["correctness"]}

    # Store criteria in the prompt
    stored = await _store_criteria_to_prompt(prompt_id, criteria)

    return {
        "prompt_id": prompt_id,
        "criteria": criteria,
        "spans_analyzed": len(spans),
        "has_agentic_behavior": has_agentic_spans,
        "stored": stored,
    }


async def ensure_prompt_has_criteria(prompt_id: str) -> dict[str, list[str]] | None:
    """
    Check if a prompt has evaluation criteria, and generate them if not.

    Args:
        prompt_id: String ID of the prompt (format: {project_id}_{version}_{slug})

    Returns:
        The evaluation criteria (existing or newly generated)
    """
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        try:
            project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
            project_uuid = UUID(project_id_str)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid prompt_id format: {str(e)}")

        result = await session.execute(
            select(Prompt).where(
                and_(
                    Prompt.project_id == project_uuid,
                    Prompt.version == version,
                    Prompt.slug == slug,
                )
            )
        )
        prompt = result.scalar_one_or_none()

        if prompt is None:
            raise ValueError(f"Prompt not found: {prompt_id}")

        if prompt.evaluation_criteria:
            return prompt.evaluation_criteria

        logger.info(f"No criteria found for prompt {prompt_id}, generating...")
        result = await _generate_criteria_for_prompt(prompt_id)
        return result.get("criteria")


@shared_task(name="criteria_generator.generate_criteria")
def generate_criteria_task(prompt_id: str) -> dict[str, Any]:
    """
    Celery task to generate evaluation criteria for a prompt.

    Args:
        prompt_id: String ID of the prompt (format: {project_id}_{version}_{slug})

    Returns:
        Dict with generation results
    """

    async def _run():
        from overmind.db.session import dispose_engine

        try:
            result = await _generate_criteria_for_prompt(prompt_id)
            logger.info(f"Generated criteria for prompt {prompt_id}")
            return result
        except Exception:
            logger.exception(f"Failed to generate criteria for prompt {prompt_id}")
            raise
        finally:
            # CRITICAL: Dispose of the engine to close all connections
            # This prevents event loop errors when the same worker runs the task again
            await dispose_engine()

    return asyncio.run(_run())
