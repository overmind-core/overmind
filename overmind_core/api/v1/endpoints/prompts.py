from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from overmind_core.models.prompts import Prompt
from overmind_core.models.traces import SpanModel
from overmind_core.models.jobs import Job
from overmind_core.db.session import get_db
from overmind_core.api.v1.helpers.authentication import AuthenticatedUserOrToken, get_current_user
from overmind_core.api.v1.endpoints.utils.prompts import are_criteria_same
from overmind_core.api.v1.endpoints.jobs import JobType, JobStatus
from overmind_core.tasks.criteria_generator import generate_criteria_task
from overmind_core.tasks.prompt_display_name_generator import generate_display_name_task
from uuid import UUID
import hashlib
import logging
import uuid as _uuid

router = APIRouter()
logger = logging.getLogger(__name__)


class CreatePromptRequest(BaseModel):
    slug: str
    prompt: str
    project_id: str


class PromptResponse(BaseModel):
    message: str
    prompt_id: str
    version: int
    is_new: bool


class PromptDetail(BaseModel):
    prompt_id: str
    slug: str
    prompt: str
    hash: str
    version: int
    project_id: str
    user_id: str
    display_name: str | None = None
    created_at: str
    updated_at: str | None = None
    evaluation_criteria: dict[str, list[str]] | None = None

    class Config:
        from_attributes = True


class UpdateCriteriaRequest(BaseModel):
    evaluation_criteria: dict[str, list[str]]
    re_evaluate: bool = False


class UpdateDisplayNameRequest(BaseModel):
    display_name: str


class GenerateCriteriaResponse(BaseModel):
    message: str
    task_id: str
    prompt_id: str


@router.post("/", response_model=PromptResponse)
async def create_prompt(
    request: CreatePromptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
):
    """
    Create a new prompt or return existing one if identical.

    If a prompt with the same slug and hash exists, returns the existing prompt.
    If a prompt with the same slug but different hash exists, creates a new version.
    Otherwise, creates a new prompt with version 1.
    """
    hash = hashlib.sha256(request.prompt.encode()).hexdigest()

    # Convert project_id string to UUID for comparison
    project_uuid = UUID(request.project_id)
    if not await current_user.is_project_member(project_uuid, db):
        raise HTTPException(
            status_code=403,
            detail="Access denied: User is not a member of this project",
        )

    # Try to find an existing prompt by slug and project_id
    stmt = (
        select(Prompt)
        .where(
            Prompt.slug == request.slug,
            Prompt.project_id == request.project_id,
        )
        .order_by(Prompt.version.desc())
    )
    result = await db.execute(stmt)
    prompt_row = result.scalar_one_or_none()

    if not prompt_row:
        # Create new prompt with version 1
        # Initially set display_name to slug, will be generated in background
        new_prompt = Prompt(
            slug=request.slug,
            hash=hash,
            prompt=request.prompt,
            display_name=request.slug,  # Initially set to slug
            user_id=current_user.user_id,
            project_id=request.project_id,
            version=1,
        )
        db.add(new_prompt)
        await db.commit()
        await db.refresh(new_prompt)

        # Trigger display name generation in background
        logger.info(
            f"Triggering display name generation for new prompt {new_prompt.prompt_id}"
        )
        generate_display_name_task.delay(prompt_id=new_prompt.prompt_id)

        # Trigger criteria generation for new prompt
        logger.info(
            f"Triggering criteria generation for new prompt {new_prompt.prompt_id}"
        )
        generate_criteria_task.delay(prompt_id=new_prompt.prompt_id)

        return PromptResponse(
            message="Prompt created.",
            prompt_id=str(new_prompt.prompt_id),
            version=new_prompt.version,
            is_new=True,
        )

    if prompt_row.hash == hash:
        # Prompt already exists with same content
        return PromptResponse(
            message="Prompt already exists with this hash. No action taken.",
            prompt_id=str(prompt_row.prompt_id),
            version=prompt_row.version,
            is_new=False,
        )

    # Create new version of the prompt
    # Initially set display_name to slug, will be generated in background
    new_prompt = Prompt(
        slug=request.slug,
        hash=hash,
        prompt=request.prompt,
        display_name=request.slug,  # Initially set to slug
        user_id=current_user.user_id,
        project_id=request.project_id,
        version=prompt_row.version + 1,
    )
    db.add(new_prompt)
    await db.commit()
    await db.refresh(new_prompt)

    # Trigger display name generation in background
    logger.info(
        f"Triggering display name generation for new prompt version {new_prompt.prompt_id}"
    )
    generate_display_name_task.delay(prompt_id=new_prompt.prompt_id)

    # Trigger criteria generation for new version
    logger.info(
        f"Triggering criteria generation for new prompt version {new_prompt.prompt_id}"
    )
    generate_criteria_task.delay(prompt_id=new_prompt.prompt_id)

    return PromptResponse(
        message="Prompt version upgraded.",
        prompt_id=str(new_prompt.prompt_id),
        version=new_prompt.version,
        is_new=True,
    )


@router.get("/", response_model=list[PromptDetail])
async def list_prompts(
    project_id: str = Query(..., description="Project ID to filter prompts"),
    slug: str | None = Query(None, description="Filter by slug"),
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
):
    """
    List all prompts for a project.

    Optionally filter by slug to get all versions of a specific prompt.
    """
    project_uuid = UUID(project_id)
    if not await current_user.is_project_member(project_uuid, db):
        raise HTTPException(
            status_code=403,
            detail="Access denied: User is not a member of this project",
        )

    # Build query
    conditions = [Prompt.project_id == project_id]
    if slug:
        conditions.append(Prompt.slug == slug)

    stmt = (
        select(Prompt)
        .where(and_(*conditions))
        .order_by(Prompt.slug, Prompt.version.desc())
    )

    result = await db.execute(stmt)
    prompts = result.scalars().all()

    return [
        PromptDetail(
            prompt_id=str(p.prompt_id),
            slug=p.slug,
            prompt=p.prompt,
            hash=p.hash,
            version=p.version,
            project_id=str(p.project_id),
            user_id=str(p.user_id),
            display_name=p.display_name,
            created_at=p.created_at.isoformat() if p.created_at else None,
            updated_at=p.updated_at.isoformat() if p.updated_at else None,
            evaluation_criteria=p.evaluation_criteria,
        )
        for p in prompts
    ]


@router.get("/{prompt_id}", response_model=PromptDetail)
async def get_prompt(
    prompt_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
):
    """
    Get a specific prompt by its ID.
    prompt_id format: {project_id}_{version}_{slug}
    """
    try:
        project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
        project_uuid = UUID(project_id_str)
    except (ValueError, TypeError) as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid prompt_id format: {str(e)}"
        )

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

    # Check if user has access to this project
    if not await current_user.is_project_member(prompt.project_id, db):
        raise HTTPException(
            status_code=403,
            detail="Access denied: User is not a member of this project",
        )

    return PromptDetail(
        prompt_id=str(prompt.prompt_id),
        slug=prompt.slug,
        prompt=prompt.prompt,
        hash=prompt.hash,
        version=prompt.version,
        project_id=str(prompt.project_id),
        user_id=str(prompt.user_id),
        display_name=prompt.display_name,
        created_at=prompt.created_at.isoformat() if prompt.created_at else None,
        updated_at=prompt.updated_at.isoformat() if prompt.updated_at else None,
        evaluation_criteria=prompt.evaluation_criteria,
    )


@router.put("/{prompt_id}/criteria")
async def update_prompt_criteria(
    prompt_id: str,
    request: UpdateCriteriaRequest,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
):
    """
    Update evaluation criteria for a prompt.

    The evaluation criteria should be a JSON object where:
    - Keys are metric names (e.g., "correctness", "completeness")
    - Values are lists of rules defining that metric

    Parameters:
    - evaluation_criteria: The new criteria to apply
    - re_evaluate: If True and criteria changed, re-evaluate last 50 spans for this prompt

    Example:
    {
      "evaluation_criteria": {
        "correctness": [
          "Must provide accurate information",
          "Must contain no factual errors",
          "Must be logically consistent"
        ]
      },
      "re_evaluate": true
    }
    """
    try:
        project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
        project_uuid = UUID(project_id_str)
    except (ValueError, TypeError) as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid prompt_id format: {str(e)}"
        )

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

    # Check if user has access to this project
    if not await current_user.is_project_member(prompt.project_id, db):
        raise HTTPException(
            status_code=403,
            detail="Access denied: User is not a member of this project",
        )

    # Check if criteria is actually different
    if are_criteria_same(prompt.evaluation_criteria, request.evaluation_criteria):
        logger.info(f"Criteria unchanged for prompt {prompt_id}, no update needed")
        return {
            "message": "Evaluation criteria unchanged, no update performed",
            "prompt_id": str(prompt.prompt_id),
            "evaluation_criteria": prompt.evaluation_criteria,
            "criteria_updated": False,
            "spans_re_evaluated": 0,
        }

    # Criteria is different, update it and roll back improvement metadata so
    # prompt improvement can re-trigger with the updated scoring logic.
    prompt.evaluation_criteria = request.evaluation_criteria
    from overmind_core.tasks.prompt_improvement import invalidate_prompt_improvement_metadata

    invalidate_prompt_improvement_metadata(prompt)

    await db.commit()
    await db.refresh(prompt)

    logger.info(f"Updated criteria for prompt {prompt_id}")

    # If re_evaluate is True, create a job to re-evaluate last 50 spans
    spans_to_re_evaluate = 0
    job_id = None
    if request.re_evaluate:
        logger.info(f"Re-evaluation requested for prompt {prompt_id}")

        # Fetch last 50 spans for this prompt_id, ordered by creation time (most recent first)
        # Exclude system-generated spans (prompt tuning, backtesting)
        spans_stmt = (
            select(SpanModel)
            .where(
                and_(
                    SpanModel.prompt_id == prompt_id,
                    SpanModel.exclude_system_spans(),
                )
            )
            .order_by(desc(SpanModel.created_at))
            .limit(50)
        )
        spans_result = await db.execute(spans_stmt)
        spans = spans_result.scalars().all()

        spans_to_re_evaluate = len(spans)

        if spans_to_re_evaluate > 0:
            logger.info(
                f"Found {spans_to_re_evaluate} spans to re-evaluate for prompt {prompt_id}"
            )

            # Get span IDs for the job
            span_ids = [span.span_id for span in spans]

            # Create a JUDGE_SCORING job with span_ids
            try:
                organisation_id = current_user.get_organisation_id()
                job_params = {
                    "span_ids": span_ids,
                    "user_id": str(current_user.user_id),
                    "business_id": str(organisation_id) if organisation_id else None,
                }

                job = Job(
                    job_id=_uuid.uuid4(),
                    job_type=JobType.JUDGE_SCORING.value,
                    project_id=prompt.project_id,
                    prompt_slug=prompt.slug,
                    status=JobStatus.PENDING.value,
                    result={"parameters": job_params},
                    triggered_by_user_id=current_user.user_id,
                )
                db.add(job)
                await db.commit()
                await db.refresh(job)

                job_id = str(job.job_id)
                logger.info(
                    f"Created JUDGE_SCORING job {job_id} for {spans_to_re_evaluate} spans"
                )
            except Exception as e:
                logger.error(f"Failed to create re-evaluation job: {e}")
        else:
            logger.info(
                f"No spans found for prompt {prompt_id}, skipping re-evaluation"
            )

    response = {
        "message": "Evaluation criteria updated successfully",
        "prompt_id": str(prompt.prompt_id),
        "evaluation_criteria": prompt.evaluation_criteria,
        "criteria_updated": True,
        "spans_re_evaluated": spans_to_re_evaluate,
    }

    if job_id:
        response["re_evaluation_job_id"] = job_id

    return response


@router.post("/{prompt_id}/criteria/generate", response_model=GenerateCriteriaResponse)
async def generate_prompt_criteria(
    prompt_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
):
    """
    Trigger automatic generation of evaluation criteria for a prompt.

    This will:
    1. Fetch the first 10 spans linked to this prompt
    2. Use Claude Sonnet 4.5 to analyze them and generate up to 5 evaluation rules
    3. Store the generated criteria in the prompt's evaluation_criteria field

    Returns a task_id that can be used to check the status of the generation.
    """
    try:
        project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
        project_uuid = UUID(project_id_str)
    except (ValueError, TypeError) as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid prompt_id format: {str(e)}"
        )

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

    # Check if user has access to this project
    if not await current_user.is_project_member(prompt.project_id, db):
        raise HTTPException(
            status_code=403,
            detail="Access denied: User is not a member of this project",
        )

    # Trigger the criteria generation task
    task = generate_criteria_task.delay(prompt_id=prompt_id)

    return GenerateCriteriaResponse(
        message="Criteria generation started",
        task_id=task.id,
        prompt_id=prompt_id,
    )


@router.get("/{prompt_id}/criteria")
async def get_prompt_criteria(
    prompt_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
):
    """
    Get evaluation criteria for a prompt.

    Returns the evaluation_criteria field from the prompt, or None if not set.
    """
    try:
        project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
        project_uuid = UUID(project_id_str)
    except (ValueError, TypeError) as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid prompt_id format: {str(e)}"
        )

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

    # Check if user has access to this project
    if not await current_user.is_project_member(prompt.project_id, db):
        raise HTTPException(
            status_code=403,
            detail="Access denied: User is not a member of this project",
        )

    return {
        "prompt_id": str(prompt.prompt_id),
        "slug": prompt.slug,
        "version": prompt.version,
        "evaluation_criteria": prompt.evaluation_criteria,
    }


@router.put("/{prompt_id}/display-name")
async def update_prompt_display_name(
    prompt_id: str,
    request: UpdateDisplayNameRequest,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
):
    """
    Update the display name for a prompt.

    This allows users to customize the display name that was auto-generated
    or set a new display name for existing prompts.

    Args:
        prompt_id: The prompt ID in format {project_id}_{version}_{slug}
        request: Request body containing the new display_name

    Example:
    {
      "display_name": "Customer Support Assistant"
    }
    """
    try:
        project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
        project_uuid = UUID(project_id_str)
    except (ValueError, TypeError) as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid prompt_id format: {str(e)}"
        )

    # Validate display name
    if not request.display_name or len(request.display_name.strip()) < 3:
        raise HTTPException(
            status_code=400, detail="Display name must be at least 3 characters long"
        )

    if len(request.display_name) > 255:
        raise HTTPException(
            status_code=400, detail="Display name must be no more than 255 characters"
        )

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

    # Check if user has access to this project
    if not await current_user.is_project_member(prompt.project_id, db):
        raise HTTPException(
            status_code=403,
            detail="Access denied: User is not a member of this project",
        )

    # Update the display name
    prompt.display_name = request.display_name.strip()
    await db.commit()
    await db.refresh(prompt)

    return {
        "message": "Display name updated successfully",
        "prompt_id": str(prompt.prompt_id),
        "display_name": prompt.display_name,
    }
