"""
Agent review endpoints for interactive criteria refinement and span feedback.
"""

import json
import logging
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, cast, select, Float
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from overmind.api.v1.helpers.authentication import AuthenticatedUserOrToken, get_current_user
from overmind.api.v1.endpoints.utils.prompts import are_criteria_same, are_descriptions_same
from overmind.db.session import get_db
from overmind.models.prompts import Prompt
from overmind.models.traces import SpanModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SpanForReview(BaseModel):
    span_id: str
    input: Any
    output: Any
    correctness_score: float | None = None
    created_at: str


class AgentReviewSpansResponse(BaseModel):
    prompt_id: str
    worst_spans: list[SpanForReview]
    best_spans: list[SpanForReview]
    agent_description: str | None = None
    evaluation_criteria: dict[str, list[str]] | None = None


class AgentDescriptionUpdateRequest(BaseModel):
    description: str
    criteria: dict[str, list[str]]


class SyncRefreshDescriptionRequest(BaseModel):
    span_ids: list[str]
    # Inline feedback from the current review session (span_id → {rating, text}).
    # When present this is used instead of reading judge_feedback from the DB,
    # so intermediate iterations never expose stale feedback from a prior session.
    feedback: dict[str, dict[str, str]] | None = None


class ReviewFailedRequest(BaseModel):
    iteration: int
    span_ids: list[str]
    feedback: dict[str, str]


# Note: Span feedback should be submitted via the existing /spans/{span_id}/feedback endpoint
# with feedback_type="judge" and rating="up" or "down"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{prompt_slug}/review-spans", response_model=AgentReviewSpansResponse)
async def get_spans_for_review(
    prompt_slug: str,
    project_id: str | None = Query(None, description="Filter by project ID"),
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get 2 worst-scored and 2 best-scored spans for agent review.
    Used in the interactive criteria refinement dialogue.
    """
    # Resolve project
    if project_id:
        pid = _uuid.UUID(project_id)
        if not await user.is_project_member(pid, db):
            raise HTTPException(status_code=403, detail="Access denied to this project")
    elif user.user.projects:
        pid = user.user.projects[0].project_id
    else:
        raise HTTPException(status_code=400, detail="No project found for user")

    # Get latest prompt version
    prompt_q = await db.execute(
        select(Prompt)
        .where(and_(Prompt.slug == prompt_slug, Prompt.project_id == pid))
        .order_by(Prompt.version.desc())
        .limit(1)
    )
    prompt = prompt_q.scalar_one_or_none()

    if not prompt:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get 2 worst-scored spans (lowest correctness)
    worst_q = await db.execute(
        select(SpanModel)
        .where(
            and_(
                SpanModel.prompt_id == prompt.prompt_id,
                SpanModel.feedback_score.has_key("correctness"),
                SpanModel.exclude_system_spans(),
            )
        )
        .order_by(cast(SpanModel.feedback_score["correctness"], Float).asc())
        .limit(2)
    )
    worst_spans = worst_q.scalars().all()

    # Exclude worst span IDs so best spans are always distinct
    worst_span_ids = [s.span_id for s in worst_spans]

    # Get 2 best-scored spans (highest correctness), excluding worst spans
    best_q = await db.execute(
        select(SpanModel)
        .where(
            and_(
                SpanModel.prompt_id == prompt.prompt_id,
                SpanModel.feedback_score.has_key("correctness"),
                SpanModel.exclude_system_spans(),
                ~SpanModel.span_id.in_(worst_span_ids),
            )
        )
        .order_by(cast(SpanModel.feedback_score["correctness"], Float).desc())
        .limit(2)
    )
    best_spans = best_q.scalars().all()

    def _parse_json_field(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return value
        return value or {}

    # Format spans
    def format_span(span: SpanModel) -> SpanForReview:
        return SpanForReview(
            span_id=span.span_id,
            input=_parse_json_field(span.input),
            output=_parse_json_field(span.output),
            correctness_score=(span.feedback_score or {}).get("correctness"),
            created_at=span.created_at.isoformat() if span.created_at else "",
        )

    # Get agent description and criteria
    agent_desc = (prompt.agent_description or {}).get("description")
    eval_criteria = prompt.evaluation_criteria

    return AgentReviewSpansResponse(
        prompt_id=prompt.prompt_id,
        worst_spans=[format_span(s) for s in worst_spans],
        best_spans=[format_span(s) for s in best_spans],
        agent_description=agent_desc,
        evaluation_criteria=eval_criteria,
    )


@router.post("/{prompt_slug}/update-description")
async def update_agent_description_and_criteria(
    prompt_slug: str,
    payload: AgentDescriptionUpdateRequest,
    project_id: str | None = Query(None),
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update agent description and criteria based on user edits.
    Triggers re-scoring of 10 randomly sampled spans to preview the impact.
    """
    # Resolve project
    if project_id:
        pid = _uuid.UUID(project_id)
        if not await user.is_project_member(pid, db):
            raise HTTPException(status_code=403, detail="Access denied to this project")
    elif user.user.projects:
        pid = user.user.projects[0].project_id
    else:
        raise HTTPException(status_code=400, detail="No project found for user")

    # Get latest prompt version
    prompt_q = await db.execute(
        select(Prompt)
        .where(and_(Prompt.slug == prompt_slug, Prompt.project_id == pid))
        .order_by(Prompt.version.desc())
        .limit(1)
    )
    prompt = prompt_q.scalar_one_or_none()

    if not prompt:
        raise HTTPException(status_code=404, detail="Agent not found")

    description_changed = not are_descriptions_same(
        prompt.agent_description, payload.description
    )
    criteria_changed = not are_criteria_same(
        prompt.evaluation_criteria, payload.criteria
    )

    # Update agent description and criteria
    prompt.agent_description = {
        **(prompt.agent_description or {}),
        "description": payload.description,
    }
    flag_modified(prompt, "agent_description")
    prompt.evaluation_criteria = payload.criteria
    flag_modified(prompt, "evaluation_criteria")

    # Roll back improvement metadata so prompt improvement can re-trigger with
    # the updated scoring logic — but only when something actually changed.
    if description_changed or criteria_changed:
        from overmind.tasks.prompt_improvement import invalidate_prompt_improvement_metadata

        invalidate_prompt_improvement_metadata(prompt)

    await db.commit()

    return {
        "success": True,
        "message": "Agent description and criteria updated.",
    }


@router.post("/{prompt_slug}/update-agent-description")
async def update_agent_description_from_feedback(
    prompt_slug: str,
    project_id: str | None = Query(None),
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update agent description based on accumulated span feedback.
    This endpoint should be called after users submit feedback via the standard span feedback endpoint.
    It analyzes all judge_feedback on spans and updates the agent description accordingly.
    """
    # Resolve project
    if project_id:
        pid = _uuid.UUID(project_id)
        if not await user.is_project_member(pid, db):
            raise HTTPException(status_code=403, detail="Access denied to this project")
    elif user.user.projects:
        pid = user.user.projects[0].project_id
    else:
        raise HTTPException(status_code=400, detail="No project found for user")

    # Get latest prompt version
    prompt_q = await db.execute(
        select(Prompt)
        .where(and_(Prompt.slug == prompt_slug, Prompt.project_id == pid))
        .order_by(Prompt.version.desc())
        .limit(1)
    )
    prompt = prompt_q.scalar_one_or_none()

    if not prompt:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get spans with judge_feedback for this prompt
    spans_with_feedback_q = await db.execute(
        select(SpanModel)
        .where(
            and_(
                SpanModel.prompt_id == prompt.prompt_id,
                SpanModel.feedback_score.has_key("judge_feedback"),
                SpanModel.exclude_system_spans(),
            )
        )
        .order_by(SpanModel.created_at.desc())
        .limit(20)  # Last 20 spans with feedback
    )
    spans_with_feedback = spans_with_feedback_q.scalars().all()

    if not spans_with_feedback:
        return {
            "success": False,
            "message": "No span feedback found to update agent description",
        }

    # Import the agent description generator task
    from overmind.tasks.agent_description_generator import (
        update_agent_description_from_feedback_task,
    )

    # Trigger async task to update description
    update_agent_description_from_feedback_task.delay(
        prompt_id=prompt.prompt_id,
        span_ids=[s.span_id for s in spans_with_feedback],
    )

    return {
        "success": True,
        "message": "Agent description update triggered based on feedback",
        "spans_analyzed": len(spans_with_feedback),
    }


@router.post("/{prompt_slug}/sync-refresh-description")
async def sync_refresh_description(
    prompt_slug: str,
    payload: SyncRefreshDescriptionRequest,
    project_id: str | None = Query(None),
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Synchronously update agent description based on user feedback on spans.
    Unlike the async version, this awaits the LLM call directly so the frontend
    can wait for the result during the interactive review loop.
    """
    if project_id:
        pid = _uuid.UUID(project_id)
        if not await user.is_project_member(pid, db):
            raise HTTPException(status_code=403, detail="Access denied to this project")
    elif user.user.projects:
        pid = user.user.projects[0].project_id
    else:
        raise HTTPException(status_code=400, detail="No project found for user")

    prompt_q = await db.execute(
        select(Prompt)
        .where(and_(Prompt.slug == prompt_slug, Prompt.project_id == pid))
        .order_by(Prompt.version.desc())
        .limit(1)
    )
    prompt = prompt_q.scalar_one_or_none()
    if not prompt:
        raise HTTPException(status_code=404, detail="Agent not found")

    from overmind.tasks.agent_description_generator import (
        _update_agent_description_from_feedback,
    )

    result = await _update_agent_description_from_feedback(
        prompt.prompt_id, payload.span_ids, feedback_override=payload.feedback
    )
    return result


@router.post("/{prompt_slug}/mark-initial-review-complete")
async def mark_initial_review_complete(
    prompt_slug: str,
    project_id: str | None = Query(None),
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Mark the initial agent review as completed so the review badge is dismissed.
    Called after the user confirms the span feedback dialog.
    """
    if project_id:
        pid = _uuid.UUID(project_id)
        if not await user.is_project_member(pid, db):
            raise HTTPException(status_code=403, detail="Access denied to this project")
    elif user.user.projects:
        pid = user.user.projects[0].project_id
    else:
        raise HTTPException(status_code=400, detail="No project found for user")

    prompt_q = await db.execute(
        select(Prompt)
        .where(and_(Prompt.slug == prompt_slug, Prompt.project_id == pid))
        .order_by(Prompt.version.desc())
        .limit(1)
    )
    prompt = prompt_q.scalar_one_or_none()
    if not prompt:
        raise HTTPException(status_code=404, detail="Agent not found")

    prompt.agent_description = {
        **(prompt.agent_description or {}),
        "initial_review_completed": True,
    }
    flag_modified(prompt, "agent_description")
    await db.commit()

    return {"success": True}


@router.post("/{prompt_slug}/review-failed")
async def report_review_failed(
    prompt_slug: str,
    payload: ReviewFailedRequest,
    project_id: str | None = Query(None),
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Log that the max review iterations were reached without user satisfaction.
    Records a structured backend error for investigation.
    """
    if project_id:
        pid = _uuid.UUID(project_id)
        if not await user.is_project_member(pid, db):
            raise HTTPException(status_code=403, detail="Access denied to this project")
    elif user.user.projects:
        pid = user.user.projects[0].project_id
    else:
        raise HTTPException(status_code=400, detail="No project found for user")

    prompt_q = await db.execute(
        select(Prompt)
        .where(and_(Prompt.slug == prompt_slug, Prompt.project_id == pid))
        .order_by(Prompt.version.desc())
        .limit(1)
    )
    prompt = prompt_q.scalar_one_or_none()

    logger.error(
        "Agent review failed after max iterations",
        extra={
            "prompt_slug": prompt_slug,
            "prompt_id": prompt.prompt_id if prompt else None,
            "iteration": payload.iteration,
            "span_ids": payload.span_ids,
            "feedback": payload.feedback,
            "user_id": str(user.user_id),
        },
    )
    return {"success": True, "message": "Review failure logged"}


@router.post("/{prompt_slug}/complete-review")
async def complete_periodic_review(
    prompt_slug: str,
    current_span_count: int = Query(
        ..., description="Current span count at time of review completion"
    ),
    project_id: str | None = Query(None),
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Mark a periodic review as completed/dismissed and update the next review threshold.
    This endpoint is called when a user dismisses or completes a periodic review notification.
    """
    # Resolve project
    if project_id:
        pid = _uuid.UUID(project_id)
        if not await user.is_project_member(pid, db):
            raise HTTPException(status_code=403, detail="Access denied to this project")
    elif user.user.projects:
        pid = user.user.projects[0].project_id
    else:
        raise HTTPException(status_code=400, detail="No project found for user")

    # Get latest prompt version
    prompt_q = await db.execute(
        select(Prompt)
        .where(and_(Prompt.slug == prompt_slug, Prompt.project_id == pid))
        .order_by(Prompt.version.desc())
        .limit(1)
    )
    prompt = prompt_q.scalar_one_or_none()

    if not prompt:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Import and trigger the mark_review_completed task
    from overmind.tasks.periodic_reviews import mark_review_completed_task

    result = mark_review_completed_task.delay(
        prompt_id=prompt.prompt_id,
        current_span_count=current_span_count,
    )

    return {
        "success": True,
        "message": "Periodic review marked as completed",
        "task_id": result.id,
    }
