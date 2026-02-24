"""
Job cleanup - deletes terminal-state jobs older than 24 hours.

Runs daily via Celery Beat to prune completed, failed, and cancelled jobs
of specific types to prevent unbounded table growth.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy import and_, delete

from overmind.api.v1.endpoints.jobs import JobStatus, JobType
from overmind.db.session import dispose_engine, get_session_local
from overmind.models.jobs import Job

logger = logging.getLogger(__name__)

# Terminal states - safe to delete (won't affect running or pending work)
TERMINAL_STATUSES = (
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
)

# Job types to clean up by default (all user-facing and system job types)
DEFAULT_JOB_TYPES_TO_CLEAN = [
    JobType.AGENT_DISCOVERY.value,
    JobType.JUDGE_SCORING.value,
    JobType.PROMPT_TUNING.value,
    JobType.MODEL_BACKTESTING.value,
]

# Age threshold: 24 hours
CLEANUP_AGE_HOURS = 24


async def _cleanup_old_jobs(
    job_types: list[str] | None = None,
    older_than_hours: int = CLEANUP_AGE_HOURS,
) -> dict:
    """
    Delete jobs in terminal states (completed, failed, cancelled) that are
    older than the specified threshold.

    Args:
        job_types: Job types to clean. If None, uses DEFAULT_JOB_TYPES_TO_CLEAN.
        older_than_hours: Delete jobs older than this many hours.

    Returns:
        Dict with cleanup statistics.
    """
    types_to_clean = job_types or DEFAULT_JOB_TYPES_TO_CLEAN
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)

    try:
        AsyncSessionLocal = get_session_local()
        async with AsyncSessionLocal() as session:
            delete_stmt = delete(Job).where(
                and_(
                    Job.job_type.in_(types_to_clean),
                    Job.status.in_(TERMINAL_STATUSES),
                    Job.created_at < cutoff,
                    Job.triggered_by_user_id.is_(None),
                )
            )
            result = await session.execute(delete_stmt)
            await session.commit()
            count = result.rowcount or 0

            if count == 0:
                logger.info("Job cleanup: no jobs to delete")
            else:
                logger.info(
                    f"Job cleanup: deleted {count} jobs (types={types_to_clean}, older_than={older_than_hours}h)"
                )

            return {"deleted": count, "cutoff": cutoff.isoformat()}

    except Exception as exc:
        logger.error(f"Job cleanup failed: {exc}", exc_info=True)
        raise
    finally:
        await dispose_engine()


@shared_task(name="job_cleanup.cleanup_old_jobs")
def cleanup_old_jobs(
    job_types: list[str] | None = None,
    older_than_hours: int = CLEANUP_AGE_HOURS,
) -> dict:
    """
    Celery task to delete terminal-state jobs older than 24 hours.

    Runs daily via Celery Beat. Only deletes jobs that are completed,
    failed, or cancelled - never pending or running.

    Args:
        job_types: Optional list of job types to clean (e.g. ["judge_scoring", "prompt_tuning"]).
                   If None, cleans all job types.
        older_than_hours: Age threshold in hours. Default 24.

    Returns:
        Dict with deleted count and cutoff timestamp.
    """
    return asyncio.run(
        _cleanup_old_jobs(job_types=job_types, older_than_hours=older_than_hours)
    )
