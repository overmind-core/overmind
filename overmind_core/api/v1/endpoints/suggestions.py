import random
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Any, Optional

from overmind_core.api.v1.endpoints.utils.suggestions import get_suggestion_or_404
from overmind_core.api.v1.helpers.authentication import AuthenticatedUserOrToken, get_current_user
from overmind_core.db.session import get_db
from overmind_core.models.prompts import Prompt
from overmind_core.models.traces import SpanModel

router = APIRouter()


class Suggestion(BaseModel):
    description: str
    id: str
    title: str


class DetectedAgent(BaseModel):
    name: str
    prompt: str
    suggestions: List[Suggestion]
    traces: List[Any]


class PaginatedResponse(BaseModel):
    data: List[DetectedAgent]
    next_page: Optional[int] = None
    previous_page: Optional[int] = None


@router.get("/", response_model=PaginatedResponse)
async def get_prompt_optimizations(
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prompts = await db.execute(
        select(Prompt).where(Prompt.user_id == user.user.user_id)
    )
    prompts = prompts.scalars().all()
    spans_query = select(SpanModel).where(
        SpanModel.prompt_id.in_([prompt.prompt_id for prompt in prompts])
    )
    spans_result = await db.execute(spans_query)
    spans = spans_result.scalars().all()

    items = []
    for prompt in prompts:
        items.append(
            DetectedAgent(
                name=prompt.slug,
                prompt=prompt.prompt,
                suggestions=random.choices(suggestions, k=random.randint(1, 3)),
                traces=[
                    span.trace_id
                    for span in spans
                    if span.prompt_id == prompt.prompt_id
                ],
            )
        )

    return PaginatedResponse(
        data=items,
        next_page=None,
        previous_page=None,
    )


class SuggestionFeedbackRequest(BaseModel):
    vote: int  # upvote = 1, downvote = -1
    feedback: Optional[str] = None


class SuggestionDetailOut(BaseModel):
    id: str
    title: str
    description: str
    status: str
    vote: int
    feedback: Optional[str] = None
    prompt_slug: Optional[str] = None
    new_prompt_version: Optional[int] = None
    scores: Optional[dict] = None
    created_at: Optional[str] = None


@router.get("/{suggestion_id}", response_model=SuggestionDetailOut)
async def get_suggestion(
    suggestion_id: str,
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve a suggestion by ID, including its current vote and feedback."""
    try:
        sid = _uuid.UUID(suggestion_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid suggestion_id format")

    suggestion = await get_suggestion_or_404(sid, user, db)

    return SuggestionDetailOut(
        id=str(suggestion.suggestion_id),
        title=suggestion.title,
        description=suggestion.description,
        status=suggestion.status,
        vote=suggestion.vote,
        feedback=suggestion.feedback,
        prompt_slug=suggestion.prompt_slug,
        new_prompt_version=suggestion.new_prompt_version,
        scores=suggestion.scores,
        created_at=suggestion.created_at.isoformat() if suggestion.created_at else None,
    )


@router.post("/{suggestion_id}/feedback", response_model=SuggestionDetailOut)
async def add_suggestion_feedback(
    suggestion_id: str,
    data: SuggestionFeedbackRequest,
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Add feedback to a suggestion. Accepts vote (1 = upvote, -1 = downvote) and
    optional text feedback. Retrieves the suggestion first, then updates it.
    """
    try:
        sid = _uuid.UUID(suggestion_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid suggestion_id format")

    if data.vote not in (1, -1):
        raise HTTPException(
            status_code=400,
            detail="vote must be 1 (upvote) or -1 (downvote)",
        )

    # Retrieve suggestion first
    suggestion = await get_suggestion_or_404(sid, user, db)

    # Add feedback
    suggestion.vote = data.vote
    suggestion.feedback = data.feedback

    await db.commit()
    await db.refresh(suggestion)

    return SuggestionDetailOut(
        id=str(suggestion.suggestion_id),
        title=suggestion.title,
        description=suggestion.description,
        status=suggestion.status,
        vote=suggestion.vote,
        feedback=suggestion.feedback,
        prompt_slug=suggestion.prompt_slug,
        new_prompt_version=suggestion.new_prompt_version,
        scores=suggestion.scores,
        created_at=suggestion.created_at.isoformat() if suggestion.created_at else None,
    )


suggestions = [
    Suggestion(
        description="Use the updated prompt to improve the agent's performance",
        id="use-updated-prompt",
        title="use updated prompt",
    ),
    Suggestion(
        description="Switch to a more cost-effective model",
        id="switch-model",
        title="switch model",
    ),
    Suggestion(
        description="Re-tune the prompt to improve the agent's performance",
        id="re-tune-prompt",
        title="re-tune prompt",
    ),
    Suggestion(
        description="Use the updated prompt to improve the agent's performance",
        id="use-updated-prompt",
        title="use updated prompt",
    ),
    Suggestion(
        description="Switch to a more cost-effective model",
        id="switch-model",
        title="switch model",
    ),
    Suggestion(
        description="Add input validation to reduce prompt errors",
        id="input-validation",
        title="add input validation",
    ),
    Suggestion(
        description="Tune hyperparameters for improved accuracy",
        id="tune-hyperparameters",
        title="tune hyperparameters",
    ),
    Suggestion(
        description="Implement logging for better traceability",
        id="add-logging",
        title="add logging",
    ),
    Suggestion(
        description="Enable prompt versioning for A/B testing",
        id="enable-versioning",
        title="enable prompt versioning",
    ),
    Suggestion(
        description="Refactor prompt for more clarity in instructions",
        id="clarify-instructions",
        title="clarify prompt instructions",
    ),
    Suggestion(
        description="Incorporate user feedback for continual improvements",
        id="use-feedback",
        title="use user feedback",
    ),
    Suggestion(
        description="Analyze failed cases for targeted optimization",
        id="analyze-failed",
        title="analyze failed cases",
    ),
]
