import logging
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, Body, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from overmind.api.v1.helpers.authentication import AuthenticatedUserOrToken, get_current_user
from overmind.api.v1.helpers.permissions import ProjectPermission
from overmind.db.session import get_db
from overmind.models.traces import SpanModel, TraceModel
from overmind.models.jobs import Job
from overmind.api.v1.endpoints.jobs import JobType, JobStatus

logger = logging.getLogger(__name__)
router = APIRouter()


class SpanFeedbackRequest(BaseModel):
    """Request body for submitting span feedback."""

    feedback_type: Literal["judge", "agent"] = Field(
        ...,
        description="Type of feedback: judge (Overmind Judge) or agent (Agent output)",
    )
    rating: Literal["up", "down"] = Field(
        ...,
        description="Thumbs up (up) or thumbs down (down)",
    )
    text: str | None = Field(
        default=None,
        description="Optional feedback text",
    )


@router.patch("/{span_id}/feedback")
async def submit_span_feedback(
    span_id: str,
    body: SpanFeedbackRequest,
    request: Request,
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit user feedback for a span - either on Overmind Judge scoring or Agent output.

    Feedback is stored in the span's feedback_score field:
    - judge_feedback: { rating, text } - used to adjust template criteria
    - agent_feedback: { rating, text } - used alongside judge score in prompt tuning
    """
    authorization_provider = request.app.state.authorization_provider
    organisation_id = current_user.get_organisation_id()

    # Fetch span and trace to verify access
    result = await db.execute(
        select(SpanModel, TraceModel)
        .join(TraceModel, SpanModel.trace_id == TraceModel.trace_id)
        .where(
            and_(
                SpanModel.span_id == span_id,
                TraceModel.user_id == current_user.user.user_id,
            )
        )
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Span not found or not accessible")

    span_obj, trace_obj = row

    await authorization_provider.check_permissions(
        user=current_user,
        db=db,
        required_permissions=[ProjectPermission.VIEW_CONTENT.value],
        organisation_id=organisation_id,
        project_id=trace_obj.project_id,
    )

    # Update feedback_score - merge with existing
    feedback_score = dict(span_obj.feedback_score) if span_obj.feedback_score else {}

    feedback_key = (
        "judge_feedback" if body.feedback_type == "judge" else "agent_feedback"
    )
    feedback_score[feedback_key] = {
        "rating": body.rating,
        "text": body.text or "",
    }

    span_obj.feedback_score = feedback_score
    await db.commit()
    await db.refresh(span_obj)

    logger.info(
        "Saved %s feedback for span %s: rating=%s",
        body.feedback_type,
        span_id,
        body.rating,
    )
    return {
        "span_id": span_id,
        "feedback_type": body.feedback_type,
        "rating": body.rating,
        "text": body.text,
    }


@router.post("/evaluate")
async def evaluate_spans(
    request: Request,
    span_ids: list[str] = Body(..., description="List of span IDs to evaluate"),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Evaluate multiple spans based on their linked prompt template criteria.

    The endpoint will:
    1. Fetch the prompt template linked to these spans
    2. Use the evaluation_criteria from the prompt (if exists)
    3. Auto-generate criteria if the prompt doesn't have any (using first 10 spans)

    Criteria is always fetched from the prompt table and cannot be provided directly.
    Stores correctness score in each span's feedback_score field.

    This creates a JUDGE_SCORING job that will be executed by the job reconciler.
    """
    authorization_provider = request.app.state.authorization_provider

    # For spans evaluation, we need to verify permissions for at least one span
    # Get the first span to check project permissions
    if not span_ids:
        return {"error": "No span IDs provided", "count": 0, "results": []}

    # Get the first span to determine the project_id
    first_span_result = await db.execute(
        select(SpanModel, TraceModel)
        .join(TraceModel, SpanModel.trace_id == TraceModel.trace_id)
        .where(SpanModel.span_id == span_ids[0])
    )
    first_span_row = first_span_result.first()

    if not first_span_row:
        raise HTTPException(status_code=404, detail="First span not found")

    span_obj, trace_obj = first_span_row
    project_id = trace_obj.project_id

    # Verify user has access to the project
    organisation_id = current_user.get_organisation_id()
    await authorization_provider.check_permissions(
        user=current_user,
        db=db,
        required_permissions=[ProjectPermission.VIEW_CONTENT.value],
        organisation_id=organisation_id,
        project_id=project_id,
    )

    user_id = str(current_user.user.user_id)

    # Create a JUDGE_SCORING job with span_ids
    job_params = {
        "span_ids": span_ids,
        "user_id": user_id,
        "business_id": str(organisation_id) if organisation_id else None,
    }

    job = Job(
        job_id=uuid.uuid4(),
        job_type=JobType.JUDGE_SCORING.value,
        project_id=project_id,
        prompt_slug=None,  # Not prompt-specific since spans may be from different prompts
        status=JobStatus.PENDING.value,
        result={"parameters": job_params},
        triggered_by_user_id=current_user.user.user_id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    logger.info(
        f"Created JUDGE_SCORING job {job.job_id} for {len(span_ids)} spans by user {user_id}"
    )

    return {
        "job_id": str(job.job_id),
        "span_count": len(span_ids),
        "status": "pending",
    }
