"""
Task to auto-generate evaluation criteria for prompts based on their linked spans.
"""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from celery import shared_task
from pydantic import BaseModel, Field
from sqlalchemy import select, and_

from overmind_core.db.session import get_session_local
from overmind_core.models.prompts import Prompt
from overmind_core.models.traces import SpanModel
from overmind_core.models.iam.projects import Project
from overmind_core.overmind.llms import call_llm, try_json_parsing
from overmind_core.overmind.model_resolver import TaskType, resolve_model
from overmind_core.tasks.prompts import (
    CRITERIA_GENERATION_SYSTEM_PROMPT,
    CRITERIA_GENERATION_PROMPT,
    AGENTIC_NOTE_FOR_CRITERIA,
)
from overmind_core.tasks.agentic_span_processor import detect_agentic_span

logger = logging.getLogger(__name__)


class CriteriaResponse(BaseModel):
    correctness: list[str] = Field(description="List of correctness rules")


async def _get_spans_for_prompt(
    prompt_id: str, limit: int = 10, prefer_judge_feedback: bool = True
) -> list[SpanModel]:
    """Fetch spans linked to a prompt. Prefer spans with judge_feedback when adjusting criteria.
    Excludes system-generated spans (prompt tuning, backtesting)."""
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SpanModel)
            .where(
                and_(
                    SpanModel.prompt_id == prompt_id,
                    SpanModel.exclude_system_spans(),
                )
            )
            .order_by(SpanModel.created_at.asc())
            .limit(limit * 2)
        )
        all_spans = list(result.scalars().all())
        if not prefer_judge_feedback or len(all_spans) <= limit:
            return all_spans[:limit]
        # Prioritize spans that have judge_feedback (for criteria adjustment)
        with_feedback = [
            s for s in all_spans if (s.feedback_score or {}).get("judge_feedback")
        ]
        without_feedback = [
            s for s in all_spans if not (s.feedback_score or {}).get("judge_feedback")
        ]
        combined = (
            with_feedback[:limit] + without_feedback[: limit - len(with_feedback)]
        )
        return combined[:limit]


async def _format_spans_as_examples(
    spans: list[SpanModel], include_judge_feedback: bool = True
) -> str:
    """Format spans into a readable example format. Includes judge feedback when present."""
    examples = []
    for i, span in enumerate(spans, 1):
        judge_fb = (
            (span.feedback_score or {}).get("judge_feedback")
            if include_judge_feedback
            else None
        )
        judge_section = ""
        if judge_fb and isinstance(judge_fb, dict):
            rating = judge_fb.get("rating", "unknown")
            text = judge_fb.get("text", "").strip()
            if text:
                judge_section = f"\nUser feedback on Judge (rating={rating}): {text}"
            else:
                judge_section = f"\nUser feedback on Judge: rating={rating}"
        example = f"""
Example {i}:
Input: {json.dumps(span.input or {}, indent=2)}
Output: {json.dumps(span.output or {}, indent=2)}{judge_section}
"""
        examples.append(example)
    return "\n".join(examples)


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


async def _get_project_description(project_id: UUID) -> str:
    """Get project description for context."""
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if project and project.description:
            return project.description
        return "No project description available."


async def _generate_criteria_for_prompt(prompt_id: str) -> dict[str, Any]:
    """
    Generate evaluation criteria for a prompt using its linked spans and project context.
    Generates up to 5 specific rules for correctness evaluation.
    Automatically detects agentic spans and includes appropriate guidance.
    """
    # Get first 10 spans linked to this prompt
    spans = await _get_spans_for_prompt(prompt_id, limit=10)

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
        project_description = await _get_project_description(project_uuid)
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
    examples_text = await _format_spans_as_examples(spans)
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


_criteria_cache: dict[str, dict[str, list[str]] | None] = {}


async def ensure_prompt_has_criteria(prompt_id: str) -> dict[str, list[str]] | None:
    """
    Check if a prompt has evaluation criteria, and generate them if not.
    Results are cached in a plain dict to avoid alru_cache's event-loop affinity
    (Celery tasks create a new loop on each invocation via asyncio.run).

    Args:
        prompt_id: String ID of the prompt (format: {project_id}_{version}_{slug})

    Returns:
        The evaluation criteria (existing or newly generated)
    """
    if prompt_id in _criteria_cache:
        return _criteria_cache[prompt_id]
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

        # If criteria already exists, return it
        if prompt.evaluation_criteria:
            _criteria_cache[prompt_id] = prompt.evaluation_criteria
            return prompt.evaluation_criteria

        # Otherwise, trigger generation task
        logger.info(f"No criteria found for prompt {prompt_id}, generating...")
        result = await _generate_criteria_for_prompt(prompt_id)
        criteria = result.get("criteria")
        _criteria_cache[prompt_id] = criteria
        return criteria


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
        from overmind_core.db.session import dispose_engine

        try:
            result = await _generate_criteria_for_prompt(prompt_id)
            logger.info(f"Generated criteria for prompt {prompt_id}")
            return result
        except Exception as exc:
            logger.error(f"Failed to generate criteria for prompt {prompt_id}: {exc}")
            raise
        finally:
            # CRITICAL: Dispose of the engine to close all connections
            # This prevents event loop errors when the same worker runs the task again
            await dispose_engine()

    return asyncio.run(_run())
