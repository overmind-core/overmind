"""
Jobs API - CRUD operations for job management.
"""

import logging
import uuid as _uuid
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from overmind.api.v1.endpoints.utils.jobs import (
    get_job_or_404,
    cancel_existing_system_jobs,
    sync_running_job_statuses,
    create_job,
    find_latest_prompt,
)
from overmind.api.v1.helpers.authentication import AuthenticatedUserOrToken, get_current_user
from overmind.db.session import get_db
from overmind.models.jobs import Job
from overmind.models.prompts import Prompt

logger = logging.getLogger(__name__)
router = APIRouter()


class JobType(str, Enum):
    """Valid job types for creation."""

    AGENT_DISCOVERY = "agent_discovery"
    JUDGE_SCORING = "judge_scoring"
    PROMPT_TUNING = "prompt_tuning"
    MODEL_BACKTESTING = "model_backtesting"


class JobStatus(str, Enum):
    """Valid job statuses."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class JobCreateRequest(BaseModel):
    job_type: JobType
    prompt_id: str


class JobUpdateRequest(BaseModel):
    status: JobStatus | None = None
    result: dict[str, Any] | None = None


class JobOut(BaseModel):
    job_id: str
    job_type: JobType
    prompt_slug: str | None = None
    prompt_display_name: str | None = None
    project_id: str
    status: JobStatus
    celery_task_id: str | None = None
    result: dict[str, Any] | None = None
    triggered_by_user_id: str | None = None
    triggered_by: str | None = None  # "manual" or "scheduled"
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_model(
        cls, job: Job, prompt_display_name: str | None = None
    ) -> "JobOut":
        return cls(
            job_id=str(job.job_id),
            job_type=JobType(job.job_type),
            prompt_slug=job.prompt_slug,
            prompt_display_name=prompt_display_name,
            project_id=str(job.project_id),
            status=JobStatus(job.status),
            celery_task_id=job.celery_task_id,
            result=job.result,
            triggered_by_user_id=str(job.triggered_by_user_id)
            if job.triggered_by_user_id
            else None,
            triggered_by="manual" if job.triggered_by_user_id else "scheduled",
            created_at=job.created_at.isoformat() if job.created_at else None,
            updated_at=job.updated_at.isoformat() if job.updated_at else None,
        )


class JobListResponse(BaseModel):
    jobs: list[JobOut]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

MAX_PENDING_JOBS_PER_PROMPT_AND_TYPE = 2


@router.post("/", response_model=JobOut)
async def create_job_from_user(
    data: JobCreateRequest,
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new job of various types:
    - judge_scoring: Scores all unscored spans for a given agent (requires prompt_id)
    - prompt_tuning: Starts prompt tuning for a given agent (requires prompt_id)
    - model_backtesting: Kick off model backtesting for a given agent version (requires prompt_id)

    Note: agent_discovery jobs are system-triggered only and cannot be created manually.
    Job starts in 'pending' status and will be picked up by the reconciler to execute.
    """
    # AGENT_DISCOVERY is system-triggered only
    if data.job_type == JobType.AGENT_DISCOVERY:
        raise HTTPException(
            status_code=400,
            detail="agent_discovery jobs are system-triggered only and cannot be created manually",
        )

    # All user-triggered jobs require prompt_id
    if not data.prompt_id:
        raise HTTPException(
            status_code=400, detail="prompt_id is required for all job types"
        )

    # Extract project_id, prompt_slug, and version from prompt_id
    try:
        project_id_str, prompt_version, prompt_slug = Prompt.parse_prompt_id(
            data.prompt_id
        )
        project_uuid = _uuid.UUID(project_id_str)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid prompt_id format: {str(e)}"
        )

    # Verify prompt exists
    result = await db.execute(
        select(Prompt).where(
            Prompt.project_id == project_uuid,
            Prompt.slug == prompt_slug,
            Prompt.version == prompt_version,
        )
    )
    prompt = result.scalar_one_or_none()
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    # Verify user has access to the project
    if not await user.is_project_member(project_uuid, db):
        raise HTTPException(status_code=403, detail="Access denied to this project")

    # Cancel any existing PENDING system jobs for the same scope
    await cancel_existing_system_jobs(
        db, project_uuid, prompt_slug, data.job_type.value
    )

    # Prepare parameters based on job type
    job_parameters = {
        "prompt_id": data.prompt_id,
    }

    # Placeholder for validation stats (used by MODEL_BACKTESTING)
    validation_stats = None

    # check if there are more than 5 jobs in pending state for the same prompt and type
    await get_check_pending_job_count(db, str(project_uuid), prompt_slug, data.job_type)

    if data.job_type == JobType.JUDGE_SCORING:
        # Run validation checks for user-triggered judge scoring jobs
        from overmind.tasks.evaluations import validate_judge_scoring_eligibility

        (
            is_eligible,
            error_message,
            js_validation_stats,
        ) = await validate_judge_scoring_eligibility(prompt, db)

        if not is_eligible:
            logger.info(
                f"Judge scoring not eligible for {prompt.prompt_id}: {error_message}"
            )
            raise HTTPException(status_code=400, detail=error_message)

        validation_stats = js_validation_stats
        job_parameters.update(
            {
                "project_id": str(project_uuid),
                "prompt_slug": prompt_slug,
            }
        )

    if data.job_type == JobType.MODEL_BACKTESTING:
        from overmind.tasks.backtesting import (
            get_default_backtest_models,
            MAX_SPANS_FOR_BACKTESTING,
            validate_backtesting_eligibility,
        )

        # Run validation checks for user-triggered backtesting jobs
        models_for_backtest = get_default_backtest_models()
        (
            is_eligible,
            error_message,
            bt_validation_stats,
        ) = await validate_backtesting_eligibility(
            prompt, db, models=models_for_backtest
        )

        if not is_eligible:
            logger.info(
                f"Backtesting not eligible for {prompt.prompt_id}: {error_message}"
            )
            raise HTTPException(status_code=400, detail=error_message)

        validation_stats = bt_validation_stats
        available_span_count = bt_validation_stats.get("available_spans", 0)
        span_count = min(available_span_count, MAX_SPANS_FOR_BACKTESTING)

        organisation_id = user.get_organisation_id()

        job_parameters.update(
            {
                "models": models_for_backtest,
                "span_count": span_count,
                "user_id": str(user.user_id),
                "organisation_id": str(organisation_id) if organisation_id else None,
            }
        )

    if data.job_type == JobType.PROMPT_TUNING:
        from overmind.tasks.prompt_improvement import validate_prompt_tuning_eligibility

        # Run validation checks for user-triggered prompt tuning jobs
        (
            is_eligible,
            error_message,
            pt_validation_stats,
        ) = await validate_prompt_tuning_eligibility(prompt, db)

        if not is_eligible:
            logger.info(
                f"Prompt tuning not eligible for {prompt.prompt_id}: {error_message}"
            )
            raise HTTPException(status_code=400, detail=error_message)

        validation_stats = pt_validation_stats

    # Create the job (all user-triggered jobs follow the same pattern)
    job_result = {"parameters": job_parameters}
    if validation_stats is not None:
        job_result["validation_stats"] = validation_stats

    job = Job(
        job_id=_uuid.uuid4(),
        job_type=data.job_type.value,
        project_id=project_uuid,
        prompt_slug=prompt_slug,
        status=JobStatus.PENDING.value,
        triggered_by_user_id=user.user.user_id,
        result=job_result,
    )

    db.add(job)
    await db.commit()
    await db.refresh(job)
    return JobOut.from_model(job)


@router.get("/", response_model=JobListResponse)
async def list_jobs(
    project_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    job_type: JobType | None = Query(None, description="Filter by job type"),
    status: JobStatus | None = Query(None, description="Filter by status"),
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List job statuses for the project with optional filters."""
    if project_id:
        pid = _uuid.UUID(project_id)
    elif user.user.projects:
        pid = user.user.projects[0].project_id
    else:
        return []

    # Sync running jobs with Celery backend first
    await sync_running_job_statuses(db, pid)

    conditions = [Job.project_id == pid]
    if job_type:
        conditions.append(Job.job_type == job_type.value)
    if status:
        conditions.append(Job.status == status.value)

    result = await db.execute(
        select(Job)
        .where(and_(*conditions))
        .order_by(Job.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    jobs = result.scalars().all()

    # Also get total count for pagination
    count_q = await db.execute(select(func.count(Job.job_id)).where(and_(*conditions)))
    total = count_q.scalar() or 0

    # Batch-resolve prompt display names for all slugs in this page
    slugs = {j.prompt_slug for j in jobs if j.prompt_slug}
    display_names: dict[str, str] = {}
    if slugs:
        from overmind.api.v1.endpoints.utils.agents import humanise_slug

        names_q = await db.execute(
            select(Prompt.slug, Prompt.display_name)
            .where(and_(Prompt.project_id == pid, Prompt.slug.in_(slugs)))
            .distinct(Prompt.slug)
        )
        for slug, dname in names_q.all():
            display_names[slug] = dname or humanise_slug(slug)

    return JobListResponse(
        jobs=[
            JobOut.from_model(j, prompt_display_name=display_names.get(j.prompt_slug))
            for j in jobs
        ],
        total=total,
    )


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: str,
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single job by ID."""
    try:
        jid = _uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format")

    job = await get_job_or_404(jid, user, db)
    return JobOut.from_model(job)


@router.patch("/{job_id}", response_model=JobOut)
async def update_job(
    job_id: str,
    data: JobUpdateRequest,
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a job (e.g. cancel a pending job)."""
    try:
        jid = _uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format")

    job = await get_job_or_404(jid, user, db)

    if data.status is not None:
        if data.status not in (JobStatus.PENDING, JobStatus.CANCELLED):
            raise HTTPException(
                status_code=400,
                detail="Invalid status. Only 'pending' and 'cancelled' are allowed for updates.",
            )
        job.status = data.status.value
        if data.status == JobStatus.CANCELLED:
            job.result = job.result or {}
            if isinstance(job.result, dict):
                job.result = {
                    **job.result,
                    "cancelled_by_user": str(user.user_id),
                }

    if data.result is not None:
        job.result = data.result

    await db.commit()
    await db.refresh(job)
    return JobOut.from_model(job)


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: str,
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a pending job."""
    try:
        jid = _uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format")

    job = await get_job_or_404(jid, user, db)

    if job.status != JobStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail="Can only delete pending jobs",
        )

    await db.delete(job)
    await db.commit()
    return None


@router.post("/extract-templates", response_model=JobOut)
async def create_template_extraction(
    project_id: str | None = Query(None),
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create an agent discovery job (user-triggered).

    For user-triggered jobs, validates eligibility before creating the job.
    Returns 400 error if validation fails with specific reason.
    """
    if project_id:
        pid = _uuid.UUID(project_id)
    elif user.user.projects:
        pid = user.user.projects[0].project_id
    else:
        raise HTTPException(status_code=400, detail="No project found for user")

    await get_check_pending_job_count(db, str(pid), None, JobType.AGENT_DISCOVERY)

    # Run validation checks for user-triggered agent discovery jobs
    from overmind.tasks.agent_discovery import validate_agent_discovery_eligibility

    (
        is_eligible,
        error_message,
        validation_stats,
    ) = await validate_agent_discovery_eligibility(pid, db)

    if not is_eligible:
        logger.info(f"Agent discovery not eligible for project {pid}: {error_message}")
        raise HTTPException(status_code=400, detail=error_message)

    logger.info(
        f"Creating agent discovery job for project {pid} (user-triggered, validated)"
    )

    job = await create_job(
        db,
        job_type=JobType.AGENT_DISCOVERY.value,
        project_id=pid,
        user_id=user.user.user_id,
        result={
            "parameters": {},
            "validation_stats": validation_stats,  # Include for debugging
        },
    )
    return JobOut.from_model(job)


@router.post("/{prompt_slug}/score", response_model=JobOut)
async def create_prompt_scoring_job(
    prompt_slug: str,
    project_id: str | None = Query(None),
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a judge scoring job (user-triggered).

    For user-triggered jobs, validates eligibility before creating the job.
    Returns 400 error if validation fails with specific reason.
    """
    if project_id:
        pid = project_id
    elif user.user.projects:
        pid = str(user.user.projects[0].project_id)
    else:
        raise HTTPException(status_code=400, detail="No project found for user")

    prompt = await find_latest_prompt(prompt_slug, pid, db)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    await get_check_pending_job_count(db, str(pid), prompt_slug, JobType.JUDGE_SCORING)

    # Run validation checks for user-triggered judge scoring jobs
    from overmind.tasks.evaluations import validate_judge_scoring_eligibility

    (
        is_eligible,
        error_message,
        validation_stats,
    ) = await validate_judge_scoring_eligibility(prompt, db)

    if not is_eligible:
        logger.info(
            f"Judge scoring not eligible for {prompt.prompt_id}: {error_message}"
        )
        raise HTTPException(status_code=400, detail=error_message)

    logger.info(
        f"Creating judge scoring job for {prompt.prompt_id} (user-triggered, validated)"
    )

    job = await create_job(
        db,
        job_type=JobType.JUDGE_SCORING.value,
        project_id=pid,
        prompt_slug=prompt_slug,
        user_id=user.user_id,
        result={
            "parameters": {
                "prompt_id": prompt.prompt_id,
                "project_id": str(prompt.project_id),
                "prompt_slug": prompt_slug,
            },
            "validation_stats": validation_stats,  # Include for debugging
        },
    )
    return JobOut.from_model(job)


@router.post("/{prompt_slug}/tune", response_model=JobOut)
async def create_prompt_tuning_job(
    prompt_slug: str,
    project_id: str | None = Query(None),
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a prompt tuning job (user-triggered).

    For user-triggered jobs, validates eligibility before creating the job.
    Returns 400 error if validation fails with specific reason.
    """
    if project_id:
        pid = project_id
    elif user.user.projects:
        pid = str(user.user.projects[0].project_id)
    else:
        raise HTTPException(status_code=400, detail="No project found for user")

    prompt = await find_latest_prompt(prompt_slug, pid, db)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    await get_check_pending_job_count(db, str(pid), prompt_slug, JobType.PROMPT_TUNING)

    # Run validation checks for user-triggered jobs before creating the job
    from overmind.tasks.prompt_improvement import validate_prompt_tuning_eligibility

    (
        is_eligible,
        error_message,
        validation_stats,
    ) = await validate_prompt_tuning_eligibility(prompt, db)

    if not is_eligible:
        logger.info(
            f"Prompt tuning not eligible for {prompt.prompt_id}: {error_message}"
        )
        raise HTTPException(status_code=400, detail=error_message)

    logger.info(
        f"Creating prompt tuning job for {prompt.prompt_id} (user-triggered, validated)"
    )

    # Validation passed - create the job
    job = await create_job(
        db,
        job_type=JobType.PROMPT_TUNING.value,
        project_id=pid,
        prompt_slug=prompt_slug,
        user_id=user.user_id,
        result={
            "parameters": {
                "prompt_id": prompt.prompt_id,
            },
            "validation_stats": validation_stats,  # Include for debugging
        },
    )
    return JobOut.from_model(job)


@router.post("/{job_id}/trigger")
async def trigger_job(
    job_id: str,
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from overmind.tasks.prompt_improvement import improve_prompt_templates
    from overmind.tasks.evaluations import auto_evaluate_unscored_spans_task
    from overmind.tasks.agent_discovery import run_agent_discovery_task

    job = await get_job_or_404(job_id, user, db)
    if job.status == "running":
        raise HTTPException(status_code=400, detail="Job is already running")

    if job.job_type == JobType.PROMPT_TUNING.value:
        task = improve_prompt_templates.delay()
        job.celery_task_id = task.id
        job.status = "running"
        job.triggered_by_user_id = user.user_id
        await db.commit()
        await db.refresh(job)
        return JobOut.from_model(job)

    if job.job_type == JobType.JUDGE_SCORING.value:
        task = auto_evaluate_unscored_spans_task.delay()
        job.celery_task_id = task.id
        job.status = "running"
        job.triggered_by_user_id = user.user_id
        await db.commit()
        await db.refresh(job)
        return JobOut.from_model(job)

    if job.job_type == JobType.AGENT_DISCOVERY.value:
        task = run_agent_discovery_task.delay(job_id=str(job.job_id))
        job.celery_task_id = task.id
        job.status = "running"
        job.triggered_by_user_id = user.user_id
        await db.commit()
        await db.refresh(job)
        return JobOut.from_model(job)

    if job.job_type == JobType.MODEL_BACKTESTING.value:
        from overmind.tasks.backtesting import run_model_backtesting_task

        params = (job.result or {}).get("parameters", {})
        if not params.get("prompt_id") or not params.get("models"):
            raise HTTPException(
                status_code=400,
                detail="Backtesting job is missing required parameters (prompt_id, models)",
            )
        task = run_model_backtesting_task.delay(
            prompt_id=params["prompt_id"],
            models=params["models"],
            span_count=params.get("span_count", 50),
            user_id=params.get("user_id", str(user.user_id)),
            organisation_id=params.get("organisation_id"),
            job_id=str(job.job_id),
        )
        job.celery_task_id = task.id
        job.status = "running"
        job.triggered_by_user_id = user.user_id
        await db.commit()
        await db.refresh(job)
        return JobOut.from_model(job)

    raise HTTPException(status_code=400, detail="Invalid job_type")


async def get_check_pending_job_count(
    db: AsyncSession, project_id: str, prompt_slug: str, job_type: JobType
) -> int:
    pending_jobs_count_result = await db.execute(
        select(func.count())
        .select_from(Job)
        .where(
            and_(
                Job.project_id == str(project_id),
                Job.prompt_slug == prompt_slug,
                Job.job_type == job_type.value,
                Job.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
            )
        )
    )
    count = pending_jobs_count_result.scalar() or 0
    if count > MAX_PENDING_JOBS_PER_PROMPT_AND_TYPE:
        raise HTTPException(
            status_code=400,
            detail=f"There are already {MAX_PENDING_JOBS_PER_PROMPT_AND_TYPE} jobs in pending state for this prompt and type. Please wait for them to complete or cancel them.",
        )
    return count
