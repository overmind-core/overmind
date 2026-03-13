"""
Daily feedback processor: consumes unconsumed agent_feedback, judge_feedback,
and reference_output signals to update agent descriptions.

Runs once per day. Uses watermarks stored in prompt.agent_description to track
which feedback has already been processed, so no signal is consumed twice.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from celery import shared_task
from sqlalchemy import select, and_, or_
from sqlalchemy.orm.attributes import flag_modified

from overmind.db.session import get_session_local
from overmind.models.prompts import Prompt, PROMPT_STATUS_ACTIVE
from overmind.models.traces import SpanModel
from overmind.models.iam.projects import Project
from overmind.tasks.utils.task_lock import with_task_lock

logger = logging.getLogger(__name__)

# Minimum number of unconsumed feedback signals required to trigger a description update.
MIN_SIGNALS_REQUIRED = 5

# Maximum number of spans to pass into the description update per prompt per cycle.
MAX_SIGNALS_PER_CYCLE = 20


def _has_user_signal(span: SpanModel) -> bool:
    """Check if a span has any user-provided feedback or reference output."""
    fb = span.feedback_score or {}
    if fb.get("agent_feedback") and isinstance(fb["agent_feedback"], dict):
        return True
    if fb.get("judge_feedback") and isinstance(fb["judge_feedback"], dict):
        return True
    if (
        fb.get("reference_output")
        and isinstance(fb["reference_output"], dict)
        and fb["reference_output"].get("content")
    ):
        return True
    return False


def _get_signal_time(span: SpanModel) -> datetime:
    """
    Get the best timestamp for a span's feedback signal.
    Prefers given_at from feedback (handles retroactive feedback on old spans).
    Falls back to span created_at.
    """
    fb = span.feedback_score or {}

    # Try given_at from judge_feedback first
    judge_fb = fb.get("judge_feedback")
    if isinstance(judge_fb, dict) and judge_fb.get("given_at"):
        try:
            return datetime.fromisoformat(judge_fb["given_at"])
        except (ValueError, TypeError):
            pass

    # Try given_at from agent_feedback
    agent_fb = fb.get("agent_feedback")
    if isinstance(agent_fb, dict) and agent_fb.get("given_at"):
        try:
            return datetime.fromisoformat(agent_fb["given_at"])
        except (ValueError, TypeError):
            pass

    # Fall back to span created_at
    if span.created_at:
        if span.created_at.tzinfo is None:
            return span.created_at.replace(tzinfo=timezone.utc)
        return span.created_at

    return datetime.now(timezone.utc)


def _select_diverse_spans(
    spans: list[SpanModel], max_count: int = MAX_SIGNALS_PER_CYCLE
) -> list[SpanModel]:
    """
    Select up to max_count spans with balanced positive/negative signal.

    Strategy:
    - Split into positive (any "up" rating), negative (any "down" rating or ref-output-only)
    - Take up to half from each side
    - Fill remainder from whichever has more, then neutral (ref-output only)
    - Most recent first within each group
    """
    positive = []
    negative = []
    neutral = []  # reference output only, no explicit rating

    for span in spans:
        fb = span.feedback_score or {}
        judge_rating = (fb.get("judge_feedback") or {}).get("rating")
        agent_rating = (fb.get("agent_feedback") or {}).get("rating")
        has_ref = bool((fb.get("reference_output") or {}).get("content"))

        has_up = judge_rating == "up" or agent_rating == "up"
        has_down = judge_rating == "down" or agent_rating == "down"

        if has_down:
            negative.append(span)
        elif has_up:
            positive.append(span)
        elif has_ref:
            neutral.append(span)

    half = max_count // 2
    selected_neg = negative[:half]
    selected_pos = positive[:half]

    remaining = max_count - len(selected_neg) - len(selected_pos)

    # Fill from the side that has more, then neutral
    leftover_neg = negative[half:]
    leftover_pos = positive[half:]
    filler = leftover_neg + leftover_pos + neutral
    selected_fill = filler[:remaining]

    return selected_neg + selected_pos + selected_fill


async def _process_feedback_for_prompt(
    prompt: Prompt,
    session,
) -> dict[str, Any]:
    """
    Process unconsumed feedback for a single prompt.

    Returns a dict with the result of processing.
    """
    from overmind.tasks.agent_description_generator import (
        update_agent_description_from_feedback,
    )

    agent_desc = prompt.agent_description or {}

    # Skip if initial review not completed — agent description not yet set up
    if not agent_desc.get("initial_review_completed"):
        return {"status": "skipped", "reason": "initial_review_not_completed"}

    # Parse watermarks
    feedback_watermark: datetime | None = None
    ref_watermark: datetime | None = None

    wm_str = agent_desc.get("feedback_watermark")
    if wm_str:
        try:
            feedback_watermark = datetime.fromisoformat(wm_str)
        except (ValueError, TypeError):
            feedback_watermark = None

    ref_wm_str = agent_desc.get("reference_output_watermark")
    if ref_wm_str:
        try:
            ref_watermark = datetime.fromisoformat(ref_wm_str)
        except (ValueError, TypeError):
            ref_watermark = None

    # Query 1: spans with agent_feedback or judge_feedback, newer than watermark
    fb_conditions = [
        SpanModel.prompt_id == prompt.prompt_id,
        or_(
            SpanModel.feedback_score.has_key("agent_feedback"),
            SpanModel.feedback_score.has_key("judge_feedback"),
        ),
        SpanModel.exclude_system_spans(),
    ]
    if feedback_watermark is not None:
        fb_conditions.append(SpanModel.created_at > feedback_watermark)

    fb_result = await session.execute(
        select(SpanModel)
        .where(and_(*fb_conditions))
        .order_by(SpanModel.created_at.desc())
        .limit(40)
    )
    feedback_spans = list(fb_result.scalars().all())

    # Query 2: spans with reference_output, newer than ref watermark
    ref_conditions = [
        SpanModel.prompt_id == prompt.prompt_id,
        SpanModel.feedback_score.has_key("reference_output"),
        SpanModel.exclude_system_spans(),
    ]
    if ref_watermark is not None:
        ref_conditions.append(SpanModel.created_at > ref_watermark)

    ref_result = await session.execute(
        select(SpanModel)
        .where(and_(*ref_conditions))
        .order_by(SpanModel.created_at.desc())
        .limit(40)
    )
    ref_spans = list(ref_result.scalars().all())

    # Merge and deduplicate by span_id
    seen_ids: set[str] = set()
    all_signal_spans: list[SpanModel] = []
    for span in feedback_spans + ref_spans:
        if span.span_id not in seen_ids:
            seen_ids.add(span.span_id)
            all_signal_spans.append(span)

    total_signals = len(all_signal_spans)

    if total_signals < MIN_SIGNALS_REQUIRED:
        logger.info(
            f"Prompt {prompt.prompt_id}: only {total_signals} unconsumed signals "
            f"(need {MIN_SIGNALS_REQUIRED}), skipping"
        )
        return {
            "status": "skipped",
            "reason": "insufficient_signals",
            "signal_count": total_signals,
        }

    # Select diverse subset
    selected_spans = _select_diverse_spans(all_signal_spans, MAX_SIGNALS_PER_CYCLE)

    logger.info(
        f"Prompt {prompt.prompt_id}: processing {len(selected_spans)} signals "
        f"(from {total_signals} available, feedback={len(feedback_spans)}, ref={len(ref_spans)})"
    )

    # Call the existing agent description update function
    # It opens its own session internally
    result = await update_agent_description_from_feedback(
        prompt_id=prompt.prompt_id,
        span_ids=[s.span_id for s in selected_spans],
    )

    # Advance watermarks — use max created_at of consumed spans from each query
    fb_selected = [
        s for s in selected_spans if s.span_id in {sp.span_id for sp in feedback_spans}
    ]
    ref_selected = [
        s for s in selected_spans if s.span_id in {sp.span_id for sp in ref_spans}
    ]

    new_agent_desc = dict(prompt.agent_description or {})

    if fb_selected:
        max_fb_time = max(
            (s.created_at for s in fb_selected if s.created_at),
            default=None,
        )
        if max_fb_time:
            if max_fb_time.tzinfo is None:
                max_fb_time = max_fb_time.replace(tzinfo=timezone.utc)
            new_agent_desc["feedback_watermark"] = max_fb_time.isoformat()

    if ref_selected:
        max_ref_time = max(
            (s.created_at for s in ref_selected if s.created_at),
            default=None,
        )
        if max_ref_time:
            if max_ref_time.tzinfo is None:
                max_ref_time = max_ref_time.replace(tzinfo=timezone.utc)
            new_agent_desc["reference_output_watermark"] = max_ref_time.isoformat()

    prompt.agent_description = new_agent_desc
    flag_modified(prompt, "agent_description")
    await session.commit()

    logger.info(
        f"Prompt {prompt.prompt_id}: description updated, watermarks advanced. "
        f"description_changed={result.get('description_changed', False)}"
    )

    return {
        "status": "processed",
        "spans_selected": len(selected_spans),
        "total_signals": total_signals,
        "description_changed": result.get("description_changed", False),
        "feedback_watermark": new_agent_desc.get("feedback_watermark"),
        "reference_output_watermark": new_agent_desc.get("reference_output_watermark"),
    }


async def _run_feedback_processor() -> dict[str, Any]:
    """
    Main logic: iterate all active prompts across all projects and process feedback.
    """
    AsyncSessionLocal = get_session_local()

    stats: dict[str, Any] = {
        "prompts_checked": 0,
        "prompts_processed": 0,
        "prompts_skipped": 0,
        "errors": [],
    }

    async with AsyncSessionLocal() as session:
        # Get all active projects
        projects_result = await session.execute(
            select(Project).where(Project.is_active.is_(True))
        )
        projects = list(projects_result.scalars().all())

        for project in projects:
            # Get latest active prompt version per slug for this project
            prompts_result = await session.execute(
                select(Prompt).where(
                    and_(
                        Prompt.project_id == project.project_id,
                        Prompt.status == PROMPT_STATUS_ACTIVE,
                    )
                )
            )
            prompts = list(prompts_result.scalars().all())

            for prompt in prompts:
                stats["prompts_checked"] += 1
                try:
                    result = await _process_feedback_for_prompt(prompt, session)
                    if result["status"] == "processed":
                        stats["prompts_processed"] += 1
                    else:
                        stats["prompts_skipped"] += 1
                except Exception as e:
                    error_msg = (
                        f"Failed to process feedback for prompt {prompt.prompt_id}: {e}"
                    )
                    logger.exception(error_msg)
                    stats["errors"].append(error_msg)

    return stats


@shared_task(name="feedback_processor.process_feedback", bind=True)
@with_task_lock(lock_name="feedback_processor")
def process_feedback_task(self) -> dict[str, Any]:
    """
    Daily Celery task to process unconsumed feedback signals and update agent descriptions.

    For each active prompt:
    - Collects unconsumed agent_feedback, judge_feedback, and reference_output signals
      (using watermarks in prompt.agent_description to avoid reprocessing)
    - Skips prompts with fewer than MIN_SIGNALS_REQUIRED (5) unconsumed signals
    - Selects up to MAX_SIGNALS_PER_CYCLE (20) diverse signals (balanced pos/neg)
    - Calls update_agent_description_from_feedback with the selected spans
    - Advances watermarks so consumed signals are not reprocessed

    Criteria are NEVER modified by this task. Only agent descriptions are updated.
    """

    async def _run():
        from overmind.db.session import dispose_engine

        try:
            stats = await _run_feedback_processor()
            logger.info(
                f"Feedback processor complete: {stats['prompts_checked']} checked, "
                f"{stats['prompts_processed']} processed, "
                f"{stats['prompts_skipped']} skipped, "
                f"{len(stats['errors'])} errors"
            )
            return stats
        except Exception:
            logger.exception("Feedback processor failed")
            raise
        finally:
            await dispose_engine()

    return asyncio.run(_run())
