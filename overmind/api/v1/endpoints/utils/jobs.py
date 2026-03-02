"""
Utility functions for the jobs endpoint.
"""

import logging
import uuid as _uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from overmind.api.v1.helpers.authentication import AuthenticatedUserOrToken
from overmind.celery_app import celery_app
from overmind.models.jobs import Job
from overmind.models.prompts import Prompt

logger = logging.getLogger(__name__)


def resolve_project_id(
    project_id: str | None, user: AuthenticatedUserOrToken
) -> _uuid.UUID:
    """
    Resolve project_id from query or user's first project.

    Args:
        project_id: Optional project ID string
        user: Authenticated user or token

    Returns:
        UUID of the resolved project

    Raises:
        HTTPException: If no project found
    """
    if project_id:
        return _uuid.UUID(project_id)
    if user.user.projects:
        return user.user.projects[0].project_id
    raise HTTPException(status_code=400, detail="No project found. Provide project_id.")


async def get_job_or_404(
    job_id: _uuid.UUID, user: AuthenticatedUserOrToken, db: AsyncSession
) -> Job:
    """
    Fetch job by ID and verify user has access via project membership.

    Args:
        job_id: UUID of the job
        user: Authenticated user or token
        db: Database session

    Returns:
        Job object

    Raises:
        HTTPException: If job not found or user doesn't have access
    """
    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not await user.is_project_member(job.project_id, db):
        raise HTTPException(status_code=403, detail="Access denied to this job")
    return job


async def cancel_existing_system_jobs(
    db: AsyncSession,
    project_id: _uuid.UUID,
    prompt_slug: str,
    job_type: str,
) -> int:
    """
    Cancel any existing PENDING system jobs for the same scope.
    User-triggered jobs take precedence over system-triggered jobs.

    Args:
        db: Database session
        project_id: Project UUID
        prompt_slug: Prompt slug
        job_type: Job type to cancel

    Returns:
        Number of jobs cancelled
    """
    existing_system_jobs = await db.execute(
        select(Job).where(
            and_(
                Job.project_id == project_id,
                Job.prompt_slug == prompt_slug,
                Job.job_type == job_type,
                Job.status == "pending",
                Job.triggered_by_user_id.is_(None),  # System-triggered only
            )
        )
    )
    system_jobs_to_cancel = existing_system_jobs.scalars().all()

    if system_jobs_to_cancel:
        for system_job in system_jobs_to_cancel:
            system_job.status = "cancelled"
            system_job.result = {
                "reason": "Superseded by user-triggered job",
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
            }
            logger.info(
                f"Cancelled system job {system_job.job_id} - superseded by user-triggered job"
            )
        await db.commit()

    return len(system_jobs_to_cancel)


async def sync_running_job_statuses(db: AsyncSession, project_id) -> None:
    """
    For every job that is still marked 'running' in the DB, check the actual
    Celery task state via the result backend and reconcile.

    This is the authoritative way to close out stale 'running' rows –
    it covers cases where the Celery worker finished but the after-task
    callback never fired (e.g. task was locked-out, worker crashed, etc.).

    Args:
        db: Database session
        project_id: Project UUID to filter jobs
    """
    q = await db.execute(
        select(Job).where(
            and_(
                Job.project_id == project_id,
                Job.status == "running",
                Job.celery_task_id.isnot(None),
            )
        )
    )
    running_jobs = q.scalars().all()

    if not running_jobs:
        return

    changed = False
    for job in running_jobs:
        try:
            result = celery_app.AsyncResult(job.celery_task_id)
            celery_state = (
                result.state
            )  # PENDING, STARTED, SUCCESS, FAILURE, RETRY, REVOKED
            if celery_state in ("SUCCESS",):
                job.status = "completed"
                try:
                    job.result = (
                        result.result
                        if isinstance(result.result, dict)
                        else {"raw": str(result.result)}
                    )
                except Exception:
                    job.result = {"note": "completed (result not serialisable)"}
                changed = True
            elif celery_state in ("FAILURE", "REVOKED"):
                job.status = "failed"
                job.result = {"error": str(result.result)}
                changed = True
            # PENDING / STARTED / RETRY → leave as running
        except Exception as exc:
            logger.debug(
                "Could not check celery state for %s: %s", job.celery_task_id, exc
            )

    if changed:
        await db.commit()


async def create_job(
    db: AsyncSession,
    job_type: str,
    project_id,
    prompt_slug: str | None = None,
    celery_task_id: str | None = None,
    user_id: _uuid.UUID = None,
    result: dict | None = None,
) -> Job:
    """
    Create a new Job row in 'pending' status and return it.

    Jobs are created as 'pending' so the job reconciler can pick them up,
    dispatch the appropriate Celery task, and transition them to 'running'.

    Args:
        db: Database session
        job_type: Type of the job
        project_id: Project UUID
        prompt_slug: Optional prompt slug
        celery_task_id: Optional Celery task ID
        user_id: Optional user UUID who triggered the job
        result: Optional dict with parameters for the reconciler

    Returns:
        Created Job object
    """
    job = Job(
        job_id=_uuid.uuid4(),
        job_type=job_type,
        prompt_slug=prompt_slug,
        project_id=project_id,
        status="pending",
        celery_task_id=celery_task_id,
        triggered_by_user_id=user_id,
        result=result,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Immediately nudge the reconciler so the job starts within seconds
    # rather than waiting for the next scheduled tick (every 30s).
    # If the reconciler is already running, the task lock will skip this invocation
    # and the scheduled tick will pick it up shortly after.
    try:
        celery_app.send_task("job_reconciler.reconcile_pending_jobs")
    except Exception as exc:
        logger.warning(f"Failed to nudge reconciler: {exc}")

    return job


async def find_latest_prompt(slug: str, project_id, db: AsyncSession) -> Prompt | None:
    """
    Find the latest version of a prompt by slug and project.

    Args:
        slug: Prompt slug
        project_id: Project UUID
        db: Database session

    Returns:
        Latest Prompt object or None if not found
    """
    result = await db.execute(
        select(Prompt)
        .where(and_(Prompt.slug == slug, Prompt.project_id == project_id))
        .order_by(Prompt.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
