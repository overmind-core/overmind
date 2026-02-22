from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from overmind_core.api.v1.endpoints.jobs import (
    JobType,
    get_check_pending_job_count,
)
from overmind_core.api.v1.helpers.authentication import AuthenticatedUserOrToken, get_current_user
from overmind_core.overmind.llms import SUPPORTED_LLM_MODELS, SUPPORTED_LLM_MODEL_NAMES
from overmind_core.api.v1.endpoints.utils.jobs import (
    cancel_existing_system_jobs,
    create_job,
)
from overmind_core.db.session import get_db
from overmind_core.models.prompts import Prompt
from typing import List, Optional
from uuid import UUID
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


class ModelInfo(BaseModel):
    """Information about a supported model."""

    provider: str
    model_name: str


class BacktestingRequest(BaseModel):
    """Request to run model backtesting."""

    prompt_id: str
    models: List[str]  # List of model names to test
    max_spans: Optional[int] = 50  # Maximum number of spans to test (default 50)
    min_spans: Optional[int] = 10  # Minimum number of spans required (default 10)


class BacktestingResponse(BaseModel):
    """Response from backtesting endpoint."""

    message: str
    job_id: str
    prompt_id: str
    span_count: int
    models: List[str]


@router.get("/models", response_model=List[ModelInfo])
async def list_available_models(
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
):
    """
    Get a list of all available models for backtesting.

    Returns a list of models with their provider and model name.
    """
    return [
        ModelInfo(provider=model["provider"], model_name=model["model_name"])
        for model in SUPPORTED_LLM_MODELS
    ]


@router.post("/run", response_model=BacktestingResponse)
async def run_backtesting(
    request: BacktestingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
):
    """
    Run model backtesting for a prompt template.

    This endpoint will:
    1. Validate models and prompt access
    2. Check evaluation criteria exists
    3. Count available spans for backtesting
    4. Create a job with status "pending"
    5. Job reconciler will pick up the job and execute the backtesting task

    The backtesting runs as a background task and returns a job_id for tracking.
    """
    # Validate request
    if not request.models:
        raise HTTPException(
            status_code=400,
            detail="At least one model must be specified",
        )

    # Validate models
    invalid_models = [m for m in request.models if m not in SUPPORTED_LLM_MODEL_NAMES]
    if invalid_models:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid models: {', '.join(invalid_models)}",
        )

    # Parse and validate prompt_id
    try:
        project_id_str, version, slug = Prompt.parse_prompt_id(request.prompt_id)
        project_uuid = UUID(project_id_str)
    except (ValueError, TypeError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid prompt_id format: {str(e)}",
        )

    # Check user has access to the project
    if not await current_user.is_project_member(project_uuid, db):
        raise HTTPException(
            status_code=403,
            detail="Access denied: User is not a member of this project",
        )

    # Verify prompt exists
    stmt = select(Prompt).where(
        and_(
            Prompt.project_id == project_uuid,
            Prompt.version == version,
            Prompt.slug == slug,
        )
    )
    result = await db.execute(stmt)
    prompt = result.scalar_one_or_none()

    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    await get_check_pending_job_count(
        db, str(project_uuid), slug, JobType.MODEL_BACKTESTING
    )

    # Run validation checks for user-triggered jobs before creating the job
    from overmind_core.tasks.backtesting import validate_backtesting_eligibility

    (
        is_eligible,
        error_message,
        validation_stats,
    ) = await validate_backtesting_eligibility(prompt, db, models=request.models)

    if not is_eligible:
        logger.info(
            f"Backtesting not eligible for {request.prompt_id}: {error_message}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Prompt is not eligible for backtesting: {error_message}",
        )

    # Calculate actual number of spans to use
    available_span_count = validation_stats.get("available_spans", 0)
    span_count = min(available_span_count, request.max_spans)

    logger.info(
        f"Creating backtesting job for prompt {request.prompt_id} (user-triggered, validated) "
        f"with {span_count} spans across {len(request.models)} models: {', '.join(request.models)}"
    )

    # Get organisation_id from user (optional in core)
    organisation_id = current_user.get_organisation_id()

    # Cancel any existing PENDING system jobs for the same scope
    await cancel_existing_system_jobs(db, project_uuid, slug, "model_backtesting")

    # Create the job using create_job helper
    job = await create_job(
        db,
        job_type="model_backtesting",
        project_id=str(project_uuid),
        prompt_slug=slug,
        user_id=current_user.user.user_id,
        result={
            "parameters": {
                "prompt_id": request.prompt_id,
                "models": request.models,
                "span_count": span_count,
                "user_id": str(current_user.user_id),
                "organisation_id": str(organisation_id) if organisation_id else None,
            },
            "validation_stats": validation_stats,
        },
    )

    return BacktestingResponse(
        message="Model backtesting job created and will be executed shortly",
        job_id=str(job.job_id),
        prompt_id=request.prompt_id,
        span_count=span_count,
        models=request.models,
    )
