"""
Task to trigger periodic agent reviews at span count thresholds (100, 1000, 10k).
These reviews are dismissible, allowing users to skip if satisfied with current scoring.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

from celery import shared_task
from sqlalchemy import select, func, and_

from overmind.db.session import get_session_local
from overmind.models.prompts import Prompt
from overmind.models.traces import SpanModel
from overmind.models.iam.projects import Project
from overmind.tasks.task_lock import with_task_lock

logger = logging.getLogger(__name__)

# Review thresholds: 10, 50, 100, 200, 500, 1000, then every 1000 after that
REVIEW_THRESHOLDS = [10, 50, 100, 200, 500, 1000]


def get_next_review_threshold(current_count: int) -> int | None:
    """
    Calculate the next review threshold based on current span count.
    Sequence: 10, 50, 100, 200, 500, 1000, 2000, 3000, 4000...

    Args:
        current_count: Current span count

    Returns:
        Next threshold, or None if no more reviews
    """
    # Check if current count is below any of the initial thresholds
    for threshold in REVIEW_THRESHOLDS:
        if current_count < threshold:
            return threshold

    # After 1000, increment by 1000
    next_threshold = ((current_count // 1000) + 1) * 1000
    return next_threshold


async def _get_span_count_for_prompt(prompt_id: str) -> int:
    """Get count of scored spans for a prompt."""
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        # Count scored spans (those with correctness score)
        result = await session.execute(
            select(func.count(SpanModel.span_id)).where(
                and_(
                    SpanModel.prompt_id == prompt_id,
                    SpanModel.feedback_score.has_key("correctness"),
                    SpanModel.exclude_system_spans(),
                )
            )
        )
        return result.scalar() or 0


async def _should_trigger_review(prompt: Prompt, span_count: int) -> bool:
    """
    Check if a periodic review should be triggered based on span count thresholds.

    Args:
        prompt: The Prompt model instance
        span_count: Current count of scored spans

    Returns:
        True if a review threshold has been reached
    """
    if span_count == 0:
        return False

    agent_desc = prompt.agent_description or {}
    next_review_count = agent_desc.get(
        "next_review_span_count", 100
    )  # Default to first threshold

    # Check if we've reached the next threshold
    if span_count >= next_review_count:
        logger.info(
            f"Review threshold reached for prompt {prompt.prompt_id}: {span_count} >= {next_review_count}"
        )
        return True

    return False


async def _update_next_review_threshold(prompt: Prompt, current_span_count: int):
    """
    Update the next review threshold for a prompt after a review is completed.

    Args:
        prompt: The Prompt model instance
        current_span_count: Current span count at time of review
    """
    agent_desc = prompt.agent_description or {}

    # Calculate next threshold
    next_threshold = get_next_review_threshold(current_span_count)

    agent_desc["last_review_span_count"] = current_span_count
    agent_desc["next_review_span_count"] = next_threshold

    prompt.agent_description = agent_desc

    if next_threshold:
        logger.info(
            f"Updated prompt {prompt.prompt_id} review threshold: "
            f"last={current_span_count}, next={next_threshold}"
        )
    else:
        logger.info(f"Prompt {prompt.prompt_id} has no more scheduled reviews")


async def _check_prompts_for_reviews() -> dict[str, Any]:
    """
    Check all prompts across all projects for review thresholds.

    Returns:
        Dictionary with review trigger statistics
    """
    AsyncSessionLocal = get_session_local()

    stats = {
        "prompts_checked": 0,
        "reviews_triggered": 0,
        "prompts_needing_review": [],
    }

    async with AsyncSessionLocal() as session:
        # Get all active projects
        projects_result = await session.execute(
            select(Project).where(Project.is_active.is_(True))
        )
        projects = projects_result.scalars().all()

        for project in projects:
            # Get all latest prompts for this project
            prompts_result = await session.execute(
                select(Prompt)
                .where(Prompt.project_id == project.project_id)
                .order_by(Prompt.slug, Prompt.version.desc())
                .distinct(Prompt.slug)
            )
            prompts = prompts_result.scalars().all()

            for prompt in prompts:
                stats["prompts_checked"] += 1

                # Get span count
                span_count = await _get_span_count_for_prompt(prompt.prompt_id)

                # Check if review should be triggered
                if await _should_trigger_review(prompt, span_count):
                    stats["reviews_triggered"] += 1
                    stats["prompts_needing_review"].append(
                        {
                            "prompt_id": prompt.prompt_id,
                            "slug": prompt.slug,
                            "project_id": str(project.project_id),
                            "span_count": span_count,
                            "display_name": prompt.display_name,
                        }
                    )

                    logger.info(
                        f"Review needed for prompt {prompt.prompt_id} "
                        f"(slug: {prompt.slug}, spans: {span_count})"
                    )

    return stats


@shared_task(name="periodic_reviews.check_review_triggers", bind=True)
@with_task_lock(lock_name="periodic_reviews")
def check_review_triggers_task(self) -> dict[str, Any]:
    """
    Celery task to check for prompts that need periodic reviews.
    Runs periodically (e.g., every hour) to detect when prompts reach review thresholds.

    Returns:
        Dictionary with statistics about reviews triggered
    """

    async def _run():
        from overmind.db.session import dispose_engine

        try:
            stats = await _check_prompts_for_reviews()
            logger.info(
                f"Periodic review check complete: {stats['prompts_checked']} prompts checked, "
                f"{stats['reviews_triggered']} reviews triggered"
            )
            return stats
        except Exception:
            logger.exception("Failed to check review triggers")
            raise
        finally:
            await dispose_engine()

    return asyncio.run(_run())


@shared_task(name="periodic_reviews.mark_review_completed")
def mark_review_completed_task(
    prompt_id: str, current_span_count: int
) -> dict[str, Any]:
    """
    Mark a periodic review as completed and update the next review threshold.
    Called after user completes or dismisses a periodic review.

    Args:
        prompt_id: The prompt ID that was reviewed
        current_span_count: Span count at time of review completion

    Returns:
        Success status
    """

    async def _run():
        from overmind.db.session import dispose_engine

        try:
            AsyncSessionLocal = get_session_local()
            async with AsyncSessionLocal() as session:
                try:
                    project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
                    project_uuid = UUID(project_id_str)
                except (ValueError, TypeError) as e:
                    logger.error(f"Invalid prompt_id format: {e}")
                    return {"success": False, "error": "Invalid prompt_id"}

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

                if not prompt:
                    logger.error(f"Prompt not found: {prompt_id}")
                    return {"success": False, "error": "Prompt not found"}

                # Update thresholds
                await _update_next_review_threshold(prompt, current_span_count)
                await session.commit()

                logger.info(f"Review marked as completed for prompt {prompt_id}")
                return {
                    "success": True,
                    "next_review_threshold": (prompt.agent_description or {}).get(
                        "next_review_span_count"
                    ),
                }
        except Exception:
            logger.exception("Failed to mark review as completed")
            raise
        finally:
            await dispose_engine()

    return asyncio.run(_run())
