"""
Task to generate and update agent descriptions based on span examples and feedback.
Separate from criteria generation - focuses only on describing what the agent does.
"""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from celery import shared_task
from pydantic import BaseModel, Field
from sqlalchemy import select, and_
from sqlalchemy.orm.attributes import flag_modified

from overmind.db.session import get_session_local
from overmind.models.prompts import Prompt
from overmind.models.traces import SpanModel
from overmind.models.iam.projects import Project
from overmind.core.llms import call_llm, try_json_parsing
from overmind.core.model_resolver import TaskType, resolve_model
from overmind.tasks.prompts import (
    AGENT_DESCRIPTION_SYSTEM_PROMPT,
    AGENT_DESCRIPTION_GENERATION_PROMPT,
    AGENT_DESCRIPTION_UPDATE_FROM_FEEDBACK_PROMPT,
)

logger = logging.getLogger(__name__)


class AgentDescriptionResponse(BaseModel):
    description: str = Field(description="Description of what this agent does")


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


async def _format_spans_as_examples(
    spans: list[SpanModel],
    include_feedback: bool = False,
    feedback_override: dict[str, dict[str, str]] | None = None,
) -> str:
    """Format spans into a readable example format."""
    examples = []
    for i, span in enumerate(spans, 1):
        feedback_section = ""
        if include_feedback:
            # Prefer inline feedback provided by the caller over whatever is
            # stored in the DB, so intermediate review iterations never read
            # stale judge_feedback from a previous session.
            if feedback_override and span.span_id in feedback_override:
                fb = feedback_override[span.span_id]
                rating = fb.get("rating", "unknown")
                text = (fb.get("text") or "").strip()
                if text:
                    feedback_section = f"\nUser Feedback: {rating} - {text}"
                else:
                    feedback_section = f"\nUser Feedback: {rating}"
            else:
                judge_fb = (span.feedback_score or {}).get("judge_feedback")
                if judge_fb and isinstance(judge_fb, dict):
                    rating = judge_fb.get("rating", "unknown")
                    text = judge_fb.get("text", "").strip()
                    if text:
                        feedback_section = f"\nUser Feedback: {rating} - {text}"
                    else:
                        feedback_section = f"\nUser Feedback: {rating}"

        example = f"""
Example {i}:
Input: {json.dumps(span.input or {}, indent=2)}
Output: {json.dumps(span.output or {}, indent=2)}{feedback_section}
"""
        examples.append(example)
    return "\n".join(examples)


async def _generate_initial_agent_description(
    prompt_id: str, span_limit: int = 10
) -> dict[str, Any]:
    """
    Generate initial agent description based on first N spans.

    This creates a brand new agent description from scratch, typically called:
    1. After agent discovery when a new prompt is created (first 10 spans)
    2. When feedback comes in but no existing description exists

    Args:
        prompt_id: The prompt ID
        span_limit: Number of spans to analyze (default 10)

    Returns:
        Dict with description and metadata:
        - prompt_id: The prompt ID
        - description: The generated description
        - spans_analyzed: Number of spans used
        - updated: False (indicates new creation, not update)
    """
    AsyncSessionLocal = get_session_local()

    logger.info(
        f"Generating initial agent description for prompt {prompt_id} "
        f"using up to {span_limit} spans"
    )

    # Get spans for this prompt
    async with AsyncSessionLocal() as session:
        try:
            project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
            project_uuid = UUID(project_id_str)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid prompt_id format: {e}")
            raise

        # Get prompt
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

        # Get spans
        spans_result = await session.execute(
            select(SpanModel)
            .where(
                and_(
                    SpanModel.prompt_id == prompt_id,
                    SpanModel.exclude_system_spans(),
                )
            )
            .order_by(SpanModel.created_at.asc())
            .limit(span_limit)
        )
        spans = list(spans_result.scalars().all())

        if not spans:
            raise ValueError(f"No spans found for prompt {prompt_id}")

        # Get project description
        project_description = await _get_project_description(project_uuid)

        # Format examples
        examples_text = await _format_spans_as_examples(spans, include_feedback=False)

        # Generate description using LLM
        prompt_text = AGENT_DESCRIPTION_GENERATION_PROMPT.format(
            project_description=project_description,
            examples=examples_text,
            feedback_note="",
        )

        response, _ = call_llm(
            prompt_text,
            system_prompt=AGENT_DESCRIPTION_SYSTEM_PROMPT,
            model=resolve_model(TaskType.AGENT_DESCRIPTION),
            response_format=AgentDescriptionResponse,
        )

        result_data = try_json_parsing(response)

        if "description" not in result_data or not isinstance(
            result_data["description"], str
        ):
            raise ValueError("Invalid description format received from LLM")

        description = result_data["description"]

        logger.info(
            f"LLM generated new agent description for prompt {prompt_id}. "
            f"Length: {len(description)} chars, used {len(spans)} spans."
        )

        # Store description in prompt (creating new agent_description structure)
        existing = prompt.agent_description or {}
        prompt.agent_description = {
            **existing,
            "description": description,
            "last_review_span_count": 10,
            "next_review_span_count": 50,
            "feedback_history": existing.get("feedback_history", []),
        }
        flag_modified(prompt, "agent_description")

        await session.commit()

        logger.info(
            f"Successfully stored initial agent description for prompt {prompt_id}. "
            f"Description: {description[:100]}..."
        )

        return {
            "prompt_id": prompt_id,
            "description": description,
            "spans_analyzed": len(spans),
            "updated": False,  # Indicates this is a new creation, not an update
            "created": True,
        }


async def _update_agent_description_from_feedback(
    prompt_id: str,
    span_ids: list[str],
    feedback_override: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Update agent description based on user feedback on spans.

    This function intelligently handles both cases:
    1. If an existing agent description is found, it updates it based on the new feedback
       by using the previous description as context for the LLM
    2. If this is the first time (no existing description), it generates a new one from scratch

    Args:
        prompt_id: The prompt ID
        span_ids: List of span IDs with judge feedback

    Returns:
        Dict with updated description and metadata:
        - prompt_id: The prompt ID
        - description: The new/updated description
        - previous_description: The old description (if updating)
        - spans_analyzed: Number of spans analyzed
        - updated: True if updated existing, False if created new
    """
    AsyncSessionLocal = get_session_local()

    async with AsyncSessionLocal() as session:
        try:
            project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
            project_uuid = UUID(project_id_str)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid prompt_id format: {e}")
            raise

        # Get prompt
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

        # Check for existing description
        agent_desc_data = prompt.agent_description or {}
        current_description = agent_desc_data.get("description", "")

        # Get spans with feedback
        spans_result = await session.execute(
            select(SpanModel).where(SpanModel.span_id.in_(span_ids))
        )
        spans = list(spans_result.scalars().all())

        if not spans:
            logger.warning(
                "No spans found for provided span_ids, keeping current description"
            )
            return {
                "prompt_id": prompt_id,
                "description": current_description,
                "previous_description": current_description,
                "updated": False,
                "reason": "No spans found with feedback",
            }

        # Get project description for context
        project_description = await _get_project_description(project_uuid)

        # Format examples — always include feedback so the LLM sees the user's reasons.
        # feedback_override takes precedence over DB-stored judge_feedback so that
        # intermediate review iterations use fresh in-session votes.
        examples_text = await _format_spans_as_examples(
            spans, include_feedback=True, feedback_override=feedback_override
        )

        if not current_description:
            # No existing description — generate one from scratch using the feedback spans
            logger.info(
                f"No existing agent description for prompt {prompt_id}. "
                f"Generating initial description from {len(spans)} feedback spans."
            )
            feedback_note = (
                "<Feedback Note>\n"
                "The examples below include user feedback on the agent's output. "
                "Use the feedback to inform what the agent is expected to do correctly.\n"
                "</Feedback Note>"
            )
            prompt_text = AGENT_DESCRIPTION_GENERATION_PROMPT.format(
                project_description=project_description,
                examples=examples_text,
                feedback_note=feedback_note,
            )
        else:
            # Existing description — update it based on the new feedback
            logger.info(
                f"Updating agent description for prompt {prompt_id} "
                f"from {len(spans)} feedback spans. "
                f"Current length: {len(current_description)} chars."
            )
            prompt_text = AGENT_DESCRIPTION_UPDATE_FROM_FEEDBACK_PROMPT.format(
                project_description=project_description,
                current_description=current_description,
                examples_with_feedback=examples_text,
            )

        response, _ = call_llm(
            prompt_text,
            system_prompt=AGENT_DESCRIPTION_SYSTEM_PROMPT,
            model=resolve_model(TaskType.AGENT_DESCRIPTION),
            response_format=AgentDescriptionResponse,
        )

        result_data = try_json_parsing(response)

        if "description" not in result_data or not isinstance(
            result_data["description"], str
        ):
            raise ValueError("Invalid description format received from LLM")

        new_description = result_data["description"]

        logger.info(
            f"Agent description updated for prompt {prompt_id}. "
            f"Previous length: {len(current_description)} chars, "
            f"New length: {len(new_description)} chars"
        )

        # Assign a fresh dict so SQLAlchemy detects the JSONB column as dirty
        prompt.agent_description = {
            **agent_desc_data,
            "description": new_description,
            "feedback_history": agent_desc_data.get("feedback_history", []),
        }
        flag_modified(prompt, "agent_description")

        await session.commit()

        logger.info(
            f"Successfully updated agent description for prompt {prompt_id} based on feedback. "
            f"Changed: {current_description[:50]}... → {new_description[:50]}..."
        )

        return {
            "prompt_id": prompt_id,
            "description": new_description,
            "previous_description": current_description or None,
            "spans_analyzed": len(spans),
            "updated": bool(current_description),
            "created": not bool(current_description),
            "description_changed": new_description != current_description,
        }


@shared_task(name="agent_description_generator.generate_initial_description")
def generate_initial_agent_description_task(prompt_id: str) -> dict[str, Any]:
    """
    Celery task to generate initial agent description for a newly discovered prompt.

    Args:
        prompt_id: String ID of the prompt (format: {project_id}_{version}_{slug})

    Returns:
        Dict with generation results
    """

    async def _run():
        from overmind.db.session import dispose_engine

        try:
            result = await _generate_initial_agent_description(prompt_id)
            logger.info(f"Generated initial agent description for prompt {prompt_id}")
            return result
        except Exception:
            logger.exception(
                f"Failed to generate agent description for prompt {prompt_id}"
            )
            raise
        finally:
            await dispose_engine()

    return asyncio.run(_run())


@shared_task(name="agent_description_generator.update_from_feedback")
def update_agent_description_from_feedback_task(
    prompt_id: str, span_ids: list[str]
) -> dict[str, Any]:
    """
    Celery task to update agent description based on user feedback.

    Args:
        prompt_id: String ID of the prompt
        span_ids: List of span IDs with judge feedback

    Returns:
        Dict with update results
    """

    async def _run():
        from overmind.db.session import dispose_engine

        try:
            result = await _update_agent_description_from_feedback(prompt_id, span_ids)
            logger.info(
                f"Updated agent description for prompt {prompt_id} based on feedback"
            )
            return result
        except Exception:
            logger.exception(
                f"Failed to update agent description for prompt {prompt_id}"
            )
            raise
        finally:
            await dispose_engine()

    return asyncio.run(_run())
