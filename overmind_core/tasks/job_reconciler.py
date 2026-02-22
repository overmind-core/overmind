"""
Job reconciler - polls for pending jobs and executes them.

This service looks for jobs with status "pending" and starts executing them
by dispatching the appropriate Celery tasks.
"""

import asyncio
import logging
from typing import Any, Dict

from celery import shared_task
from sqlalchemy import select, and_

from overmind_core.api.v1.endpoints.jobs import JobStatus, JobType
from overmind_core.celery_app import celery_app
from overmind_core.db.session import get_session_local
from overmind_core.models.jobs import Job
from overmind_core.tasks.task_lock import with_task_lock

logger = logging.getLogger(__name__)


# Map job types to their corresponding Celery task functions
JOB_TYPE_TO_TASK = {
    JobType.AGENT_DISCOVERY.value: "agent_discovery.run_agent_discovery",
    JobType.JUDGE_SCORING.value: "auto_evaluation.evaluate_prompt_spans",
    JobType.PROMPT_TUNING.value: "prompt_improvement.improve_single_prompt",
    JobType.MODEL_BACKTESTING.value: "backtesting.run_model_backtesting",
}


async def _cleanup_stale_running_jobs(session) -> int:
    """
    Find RUNNING jobs whose Celery tasks have already finished (e.g. because
    the task was locked out and returned 'skipped') and reconcile their status.

    Returns:
        Number of stale jobs cleaned up
    """
    result = await session.execute(
        select(Job).where(
            and_(
                Job.status == JobStatus.RUNNING.value,
                Job.celery_task_id.isnot(None),
            )
        )
    )
    running_jobs = result.scalars().all()

    if not running_jobs:
        return 0

    cleaned = 0
    for job in running_jobs:
        try:
            async_result = celery_app.AsyncResult(job.celery_task_id)
            celery_state = async_result.state

            if celery_state == "SUCCESS":
                task_result = async_result.result
                # Check if the task was skipped due to lock contention
                if (
                    isinstance(task_result, dict)
                    and task_result.get("status") == "skipped"
                ):
                    job.status = JobStatus.FAILED.value
                    job.result = {
                        "error": "Task was skipped because another instance was already running",
                        "celery_result": task_result,
                    }
                    logger.info(
                        f"Cleaned up ghost RUNNING job {job.job_id}: task was lock-skipped"
                    )
                else:
                    job.status = JobStatus.COMPLETED.value
                    job.result = (
                        task_result
                        if isinstance(task_result, dict)
                        else {"raw": str(task_result)}
                    )
                    logger.info(
                        f"Cleaned up stale RUNNING job {job.job_id}: task completed"
                    )
                cleaned += 1
            elif celery_state in ("FAILURE", "REVOKED"):
                job.status = JobStatus.FAILED.value
                job.result = {"error": str(async_result.result)}
                logger.info(
                    f"Cleaned up stale RUNNING job {job.job_id}: task {celery_state.lower()}"
                )
                cleaned += 1
            # PENDING / STARTED / RETRY → leave as running
        except Exception as exc:
            logger.debug(
                "Could not check celery state for job %s (task %s): %s",
                job.job_id,
                job.celery_task_id,
                exc,
            )

    if cleaned:
        await session.commit()
        logger.info(f"Cleaned up {cleaned} stale RUNNING jobs")

    return cleaned


async def _execute_pending_jobs() -> Dict[str, Any]:
    """
    Find all pending jobs and execute them by dispatching Celery tasks.

    Returns:
        Dict with execution results and statistics
    """
    from overmind_core.db.session import dispose_engine

    try:
        AsyncSessionLocal = get_session_local()
        async with AsyncSessionLocal() as session:
            # Clean up ghost RUNNING jobs before processing pending ones
            await _cleanup_stale_running_jobs(session)

            # Find all pending jobs (both user-triggered and system-triggered)
            result = await session.execute(
                select(Job)
                .where(Job.status == JobStatus.PENDING.value)
                .order_by(Job.created_at)
            )
            pending_jobs = result.scalars().all()

            if not pending_jobs:
                logger.info("No pending jobs found")
                return {
                    "status": "success",
                    "jobs_found": 0,
                    "jobs_executed": 0,
                    "jobs_failed": 0,
                    "errors": [],
                }

            logger.info(f"Found {len(pending_jobs)} pending jobs")

            executed_count = 0
            failed_count = 0
            errors = []

            for job in pending_jobs:
                try:
                    # Refresh job from database to catch any status changes (e.g., cancellations)
                    await session.refresh(job)

                    # Skip if job was cancelled (e.g., superseded by user-triggered job)
                    if job.status != JobStatus.PENDING.value:
                        logger.info(
                            f"Skipping job {job.job_id}: Status changed to {job.status}"
                        )
                        continue

                    # Get the task name for this job type (may be overridden later for JUDGE_SCORING)
                    task_name = JOB_TYPE_TO_TASK.get(job.job_type)

                    if task_name is None:
                        error_msg = f"Unknown job type: {job.job_type}"
                        logger.error(error_msg)
                        job.status = JobStatus.FAILED.value
                        job.result = {"error": error_msg}
                        failed_count += 1
                        errors.append(f"Job {job.job_id}: {error_msg}")
                        await session.commit()
                        continue

                    # Check if there's already a running job of the same type
                    # For AGENT_DISCOVERY: only one can run globally at a time
                    # For prompt-specific jobs: check by (project_id, prompt_slug, job_type)
                    # For project-wide jobs: check by (project_id, job_type)
                    if job.job_type == JobType.AGENT_DISCOVERY.value:
                        # Global check for agent discovery - only one can run at a time
                        running_check = await session.execute(
                            select(Job).where(
                                and_(
                                    Job.job_type == JobType.AGENT_DISCOVERY.value,
                                    Job.status == JobStatus.RUNNING.value,
                                    Job.job_id != job.job_id,
                                )
                            )
                        )
                    elif job.prompt_slug:
                        # For prompt-specific jobs, check by prompt_slug
                        running_check = await session.execute(
                            select(Job).where(
                                and_(
                                    Job.project_id == job.project_id,
                                    Job.prompt_slug == job.prompt_slug,
                                    Job.job_type == job.job_type,
                                    Job.status == JobStatus.RUNNING.value,
                                    Job.job_id != job.job_id,
                                )
                            )
                        )
                    else:
                        # For project-wide jobs, check by project_id and job_type
                        running_check = await session.execute(
                            select(Job).where(
                                and_(
                                    Job.project_id == job.project_id,
                                    Job.job_type == job.job_type,
                                    Job.status == JobStatus.RUNNING.value,
                                    Job.job_id != job.job_id,
                                )
                            )
                        )

                    if running_check.scalar_one_or_none():
                        logger.info(
                            f"Skipping job {job.job_id}: Another {job.job_type} job is already running"
                        )
                        continue

                    # Prepare task arguments based on job type
                    task_kwargs = {}

                    if job.job_type == JobType.AGENT_DISCOVERY.value:
                        task_kwargs = {
                            "job_id": str(job.job_id),
                        }

                    elif job.job_type == JobType.MODEL_BACKTESTING.value:
                        # Extract parameters from job.result
                        if not job.result or "parameters" not in job.result:
                            error_msg = "Missing parameters for model_backtesting job"
                            logger.error(error_msg)
                            job.status = JobStatus.FAILED.value
                            job.result = {"error": error_msg}
                            failed_count += 1
                            errors.append(f"Job {job.job_id}: {error_msg}")
                            await session.commit()
                            continue

                        params = job.result["parameters"]
                        task_kwargs = {
                            "prompt_id": params.get("prompt_id"),
                            "models": params.get("models"),
                            "span_count": params.get("span_count"),
                            "user_id": params.get("user_id"),
                            "organisation_id": params.get("organisation_id"),
                            "job_id": str(job.job_id),
                        }

                    elif job.job_type == JobType.PROMPT_TUNING.value:
                        # Extract parameters from job.result
                        if not job.result or "parameters" not in job.result:
                            error_msg = "Missing parameters for prompt_tuning job"
                            logger.error(error_msg)
                            job.status = JobStatus.FAILED.value
                            job.result = {"error": error_msg}
                            failed_count += 1
                            errors.append(f"Job {job.job_id}: {error_msg}")
                            await session.commit()
                            continue

                        params = job.result["parameters"]
                        task_kwargs = {
                            "prompt_id": params.get("prompt_id"),
                            "job_id": str(job.job_id),
                        }

                    elif job.job_type == JobType.JUDGE_SCORING.value:
                        # Extract parameters from job.result
                        if not job.result or "parameters" not in job.result:
                            error_msg = "Missing parameters for judge_scoring job"
                            logger.error(error_msg)
                            job.status = JobStatus.FAILED.value
                            job.result = {"error": error_msg}
                            failed_count += 1
                            errors.append(f"Job {job.job_id}: {error_msg}")
                            await session.commit()
                            continue

                        params = job.result["parameters"]

                        # Check if span_ids are provided (user-triggered specific spans evaluation)
                        if "span_ids" in params and params["span_ids"]:
                            # Use evaluate_spans task for specific spans
                            task_name = "evaluations.evaluate_spans"
                            task_kwargs = {
                                "span_ids": params.get("span_ids"),
                                "business_id": params.get("business_id"),
                                "user_id": params.get("user_id"),
                                "job_id": str(job.job_id),
                            }
                        else:
                            # Use evaluate_prompt_spans task for prompt-based evaluation
                            task_kwargs = {
                                "prompt_id": params.get("prompt_id"),
                                "project_id": str(job.project_id),
                                "prompt_slug": job.prompt_slug,
                                "job_id": str(job.job_id),
                            }

                    # Dispatch the Celery task
                    task = celery_app.send_task(task_name, kwargs=task_kwargs)
                    celery_task_id = task.id

                    # Update job status to running and store celery_task_id
                    job.status = JobStatus.RUNNING.value
                    job.celery_task_id = celery_task_id
                    await session.commit()
                    await session.refresh(job)

                    logger.info(
                        f"Started job {job.job_id} ({job.job_type}) with Celery task {celery_task_id}"
                    )
                    executed_count += 1

                except Exception as exc:
                    error_msg = f"Failed to execute job {job.job_id}: {str(exc)}"
                    logger.error(error_msg, exc_info=True)
                    job.status = JobStatus.FAILED.value
                    job.result = {"error": str(exc)}
                    await session.commit()
                    failed_count += 1
                    errors.append(error_msg)

            result = {
                "status": "success",
                "jobs_found": len(pending_jobs),
                "jobs_executed": executed_count,
                "jobs_failed": failed_count,
                "errors": errors,
            }

            logger.info(
                f"Job reconciler complete: {executed_count} executed, {failed_count} failed"
            )

            return result

    except Exception as exc:
        logger.error(f"Job reconciler failed: {exc}", exc_info=True)
        return {
            "status": "error",
            "error": str(exc),
            "jobs_found": 0,
            "jobs_executed": 0,
            "jobs_failed": 0,
            "errors": [str(exc)],
        }
    finally:
        # CRITICAL: Dispose of the engine to close all connections
        # This prevents event loop errors when the same worker runs the task again
        await dispose_engine()


@shared_task(name="job_reconciler.reconcile_pending_jobs")
@with_task_lock(lock_name="job_reconciler")
def reconcile_pending_jobs() -> Dict[str, Any]:
    """
    Celery periodic task to reconcile pending jobs.

    This task runs periodically and:
    1. Finds all jobs with status "pending"
    2. Maps job types to their corresponding Celery tasks
    3. Dispatches the tasks and updates job status to "running"
    4. Handles errors and marks failed jobs appropriately

    Uses distributed locking to prevent concurrent executions.
    If a previous instance is still running, new executions are cancelled.

    Returns:
        Dict with reconciliation results and statistics
    """
    return asyncio.run(_execute_pending_jobs())
