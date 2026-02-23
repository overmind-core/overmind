"""
Automatic evaluation task that runs periodically to score unscored spans.
"""

import asyncio
import logging
import random
import uuid
from typing import Any
from uuid import UUID

from celery import shared_task
from sqlalchemy import select, and_, or_, func, cast, String

from overmind_core.db.session import get_session_local
from overmind_core.models.traces import SpanModel
from overmind_core.models.prompts import Prompt
from overmind_core.models.jobs import Job
from overmind_core.api.v1.endpoints.jobs import JobType, JobStatus
from overmind_core.tasks.evaluations import _evaluate_span_correctness
from overmind_core.tasks.task_lock import with_task_lock

logger = logging.getLogger(__name__)

# Minimum unscored spans required before judge scoring is eligible
MIN_UNSCORED_SPANS_FOR_SCORING = 10


async def validate_judge_scoring_eligibility(
    prompt: Prompt, session
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """
    Validate if a prompt is eligible for judge scoring.

    This function is used by both user-triggered jobs (before job creation) and
    system-triggered jobs (before job creation in _auto_evaluate_unscored_spans).

    Args:
        prompt: The Prompt to validate
        session: Database session

    Returns:
        Tuple of (is_eligible, error_message, stats)
        - is_eligible: True if all checks pass
        - error_message: Reason if checks fail, None otherwise
        - stats: Dictionary with check results for debugging
    """
    prompt_id = prompt.prompt_id
    project_id = prompt.project_id
    prompt_slug = prompt.slug
    stats = {}

    # Check 1: Evaluation criteria exists
    criteria = prompt.evaluation_criteria
    if (
        not criteria
        or not isinstance(criteria, dict)
        or "correctness" not in criteria
        or not criteria["correctness"]
        or not isinstance(criteria["correctness"], list)
    ):
        return (
            False,
            "Evaluation criteria haven't been configured yet. Please set up scoring rules before running this job.",
            stats,
        )

    # Check 2: Find unscored spans for this prompt
    prompt_id_expr = func.concat(
        cast(Prompt.project_id, String),
        "_",
        cast(Prompt.version, String),
        "_",
        Prompt.slug,
    )
    result = await session.execute(
        select(SpanModel)
        .join(Prompt, SpanModel.prompt_id == prompt_id_expr)
        .where(
            and_(
                Prompt.project_id == project_id,
                Prompt.slug == prompt_slug,
                or_(
                    SpanModel.feedback_score.is_(None),
                    ~SpanModel.feedback_score.has_key("correctness"),
                ),
                Prompt.evaluation_criteria.isnot(None),
                Prompt.evaluation_criteria.has_key("correctness"),
                SpanModel.exclude_system_spans(),
            )
        )
    )
    unscored_spans = result.scalars().all()
    unscored_count = len(unscored_spans)
    stats["unscored_spans_count"] = unscored_count

    if unscored_count < MIN_UNSCORED_SPANS_FOR_SCORING:
        return (
            False,
            "Not enough requests have been collected yet. Keep using your application and try again later.",
            stats,
        )

    # Check 3: No existing PENDING/RUNNING judge scoring job
    existing_job_check = await session.execute(
        select(Job).where(
            and_(
                Job.project_id == project_id,
                Job.prompt_slug == prompt_slug,
                Job.job_type == JobType.JUDGE_SCORING.value,
                Job.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
            )
        )
    )
    existing_job = existing_job_check.scalar_one_or_none()

    if existing_job:
        return (
            False,
            "A scoring job is already in progress. Please wait for it to finish.",
            stats,
        )

    # All checks passed!
    logger.info(
        f"Prompt {prompt_id} is eligible for judge scoring: {unscored_count} unscored spans available"
    )
    return True, None, stats


async def _get_unscored_spans() -> list[SpanModel]:
    """
    Fetch all spans that:
    1. Don't have a correctness score in feedback_score
    2. Are linked to a prompt template (have prompt_id)
    3. The linked prompt has evaluation_criteria with correctness defined
    """
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        # Query for all unscored spans with prompt_id (no limit)
        # Exclude system-generated spans (prompt tuning, backtesting)
        result = await session.execute(
            select(SpanModel).where(
                and_(
                    SpanModel.prompt_id.isnot(None),  # Must be linked to a prompt
                    or_(
                        SpanModel.feedback_score.is_(None),  # No feedback_score at all
                        ~SpanModel.feedback_score.has_key(
                            "correctness"
                        ),  # Or no correctness key
                    ),
                    SpanModel.exclude_system_spans(),
                )
            )
        )
        spans = result.scalars().all()

        # Filter spans whose prompts have correctness criteria
        valid_spans = []
        for span in spans:
            if not span.prompt_id:
                continue

            try:
                # Parse the prompt_id to get components
                project_id_str, version, slug = Prompt.parse_prompt_id(span.prompt_id)
                project_uuid = UUID(project_id_str)
            except (ValueError, TypeError):
                logger.warning(
                    f"Invalid prompt_id format for span {span.span_id}: {span.prompt_id}"
                )
                continue

            # Get the prompt
            prompt_result = await session.execute(
                select(Prompt).where(
                    and_(
                        Prompt.project_id == project_uuid,
                        Prompt.version == version,
                        Prompt.slug == slug,
                    )
                )
            )
            prompt = prompt_result.scalar_one_or_none()

            # Check if prompt has correctness criteria
            if prompt and prompt.evaluation_criteria:
                criteria = prompt.evaluation_criteria
                if isinstance(criteria, dict) and "correctness" in criteria:
                    if criteria["correctness"] and isinstance(
                        criteria["correctness"], list
                    ):
                        valid_spans.append(span)

        return valid_spans


async def _auto_evaluate_unscored_spans(
    celery_task_id: str | None = None,
) -> dict[str, Any]:
    """
    Automatically evaluate unscored spans prompt by prompt.

    Logic:
    - Groups unscored spans by (project_id, prompt_slug)
    - For each prompt with 10+ spans, randomly selects up to 50 spans to evaluate
    - Creates job entries with proper lifecycle tracking (running → completed/failed/cancelled)

    Args:
        celery_task_id: The Celery task ID for tracking
    """
    from overmind_core.db.session import dispose_engine

    AsyncSessionLocal = get_session_local()

    overall_stats = {
        "prompts_checked": 0,
        "total_spans_found": 0,
        "jobs_created": 0,
        "jobs_already_exist": 0,
        "insufficient_spans": 0,
        "errors": [],
    }

    try:
        # Get all unscored spans
        spans = await _get_unscored_spans()

        if not spans:
            logger.info("No unscored spans found to evaluate")
            return {
                "status": "success",
                "prompts_processed": 0,
                "total_spans_found": 0,
                "total_spans_evaluated": 0,
                "errors": [],
            }

        spans_count = len(spans)
        logger.info(f"Found {spans_count} unscored spans across all prompts")

        # Group spans by (project_id, prompt_slug)
        spans_by_prompt = {}
        for span in spans:
            # Get project_id and slug from the span's prompt
            if span.prompt_id:
                try:
                    project_id_str, version, slug = Prompt.parse_prompt_id(
                        span.prompt_id
                    )
                    project_id = UUID(project_id_str)

                    key = (project_id, slug)
                    if key not in spans_by_prompt:
                        spans_by_prompt[key] = []
                    spans_by_prompt[key].append(span)
                except Exception as e:
                    logger.warning(f"Failed to parse prompt_id {span.prompt_id}: {e}")
                    continue

        logger.info(f"Grouped spans into {len(spans_by_prompt)} prompts")

        # Process each prompt - create PENDING jobs for evaluation
        async with AsyncSessionLocal() as db:
            jobs_created = 0
            jobs_already_exist = 0
            insufficient_spans = 0

            for (project_id, prompt_slug), prompt_spans in spans_by_prompt.items():
                try:
                    # Get the latest prompt version first so we can validate
                    prompt_result = await db.execute(
                        select(Prompt)
                        .where(
                            and_(
                                Prompt.project_id == project_id,
                                Prompt.slug == prompt_slug,
                            )
                        )
                        .order_by(Prompt.version.desc())
                        .limit(1)
                    )
                    prompt = prompt_result.scalar_one_or_none()

                    if not prompt:
                        logger.warning(
                            f"Prompt not found: {prompt_slug} (project {project_id})"
                        )
                        continue

                    (
                        is_eligible,
                        error_message,
                        validation_stats,
                    ) = await validate_judge_scoring_eligibility(prompt, db)

                    if not is_eligible:
                        if error_message and "already" in error_message:
                            logger.info(
                                f"Job already in progress for prompt {prompt_slug} (project {project_id}), skipping"
                            )
                            jobs_already_exist += 1
                        else:
                            logger.info(
                                f"Prompt {prompt_slug} (project {project_id}) not eligible for scoring, skipping: {error_message}"
                            )
                            insufficient_spans += 1
                        continue

                    # Create PENDING job
                    job = Job(
                        job_id=uuid.uuid4(),
                        job_type=JobType.JUDGE_SCORING.value,
                        project_id=project_id,
                        prompt_slug=prompt_slug,
                        status=JobStatus.PENDING.value,
                        celery_task_id=celery_task_id,
                        result={
                            "parameters": {
                                "prompt_id": prompt.prompt_id,
                                "project_id": str(project_id),
                                "prompt_slug": prompt_slug,
                            },
                            "validation_stats": validation_stats,
                        },
                        triggered_by_user_id=None,  # Auto-triggered
                    )
                    db.add(job)
                    await db.commit()
                    await db.refresh(job)

                    logger.info(
                        f"Created PENDING job for judge_scoring: project {project_id}, prompt {prompt_slug}, spans: {len(prompt_spans)}, job_id: {job.job_id}"
                    )
                    jobs_created += 1
                    overall_stats["total_spans_found"] += len(prompt_spans)

                except Exception as e:
                    error_msg = f"Failed to create job for prompt {prompt_slug} (project {project_id}): {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    overall_stats["errors"].append(error_msg)

            overall_stats["prompts_checked"] = len(spans_by_prompt)
            overall_stats["jobs_created"] = jobs_created
            overall_stats["jobs_already_exist"] = jobs_already_exist
            overall_stats["insufficient_spans"] = insufficient_spans

        logger.info(
            f"Auto-evaluation check complete: {overall_stats['prompts_checked']} prompts checked, "
            f"{overall_stats['jobs_created']} jobs created, "
            f"{overall_stats['jobs_already_exist']} jobs already exist, "
            f"{overall_stats['insufficient_spans']} had insufficient spans, "
            f"{len(overall_stats['errors'])} errors"
        )

        return overall_stats

    except Exception as exc:
        logger.error(f"Auto-evaluation failed: {exc}", exc_info=True)
        overall_stats["errors"].append(str(exc))
        return overall_stats
    finally:
        # CRITICAL: Dispose of the engine to close all connections
        # This prevents event loop errors when the same worker runs the task again
        await dispose_engine()


@shared_task(name="auto_evaluation.evaluate_unscored_spans", bind=True)
@with_task_lock(lock_name="auto_evaluate_unscored_spans")
def auto_evaluate_unscored_spans_task(self) -> dict[str, Any]:
    """
    Celery periodic task to check unscored spans and create evaluation jobs.

    This task runs every 10 minutes and:
    1. Finds spans without correctness scores
    2. Filters for spans linked to prompts with evaluation criteria
    3. Groups spans by (project_id, prompt_slug)
    4. For each prompt with 10+ spans, creates a PENDING job
    5. Job reconciler will pick up these jobs and execute the actual evaluations

    Uses distributed locking to prevent concurrent executions.
    If a previous instance is still running, new executions are cancelled.

    Returns:
        Dict with check results and statistics
    """
    return asyncio.run(_auto_evaluate_unscored_spans(celery_task_id=self.request.id))


async def _execute_prompt_spans_evaluation(
    prompt_id: str, project_id: str, prompt_slug: str, job_id: str
) -> dict[str, Any]:
    """
    Execute the actual span evaluation for a job.

    Args:
        prompt_id: The prompt ID
        project_id: The project ID
        prompt_slug: The prompt slug
        job_id: The job ID tracking this work

    Returns:
        Dict with evaluation stats
    """
    from overmind_core.db.session import dispose_engine

    AsyncSessionLocal = get_session_local()

    try:
        async with AsyncSessionLocal() as db:
            # Get the job
            job_uuid = uuid.UUID(job_id)
            job_result = await db.execute(select(Job).where(Job.job_id == job_uuid))
            job = job_result.scalar_one_or_none()

            if not job:
                raise ValueError(f"Job {job_id} not found")

            logger.info(
                f"Evaluating spans for prompt {prompt_slug} (project {project_id})"
            )

            try:
                # Find unscored spans for this prompt
                # Build a SQL expression equivalent to the Prompt.prompt_id @property:
                # f"{project_id}_{version}_{slug}"
                prompt_id_expr = func.concat(
                    cast(Prompt.project_id, String),
                    "_",
                    cast(Prompt.version, String),
                    "_",
                    Prompt.slug,
                )
                result = await db.execute(
                    select(SpanModel)
                    .join(Prompt, SpanModel.prompt_id == prompt_id_expr)
                    .where(
                        and_(
                            Prompt.project_id == UUID(project_id),
                            Prompt.slug == prompt_slug,
                            or_(
                                SpanModel.feedback_score.is_(None),
                                ~SpanModel.feedback_score.has_key("correctness"),
                            ),
                            Prompt.evaluation_criteria.isnot(None),
                            Prompt.evaluation_criteria.has_key("correctness"),
                            SpanModel.exclude_system_spans(),
                        )
                    )
                )
                prompt_spans = result.scalars().all()

                # Randomly select up to 50 spans to evaluate
                spans_to_evaluate = random.sample(
                    prompt_spans, min(50, len(prompt_spans))
                )
                logger.info(
                    f"Prompt {prompt_slug} (project {project_id}): Randomly selected {len(spans_to_evaluate)}/{len(prompt_spans)} spans to evaluate"
                )

                # Evaluate each selected span
                evaluated_count = 0
                errors = []

                for span in spans_to_evaluate:
                    try:
                        await _evaluate_span_correctness(span_id=span.span_id)
                        evaluated_count += 1
                        logger.info(f"Successfully evaluated span {span.span_id}")
                    except Exception as exc:
                        error_msg = (
                            f"Failed to evaluate span {span.span_id}: {str(exc)}"
                        )
                        logger.error(error_msg)
                        errors.append(error_msg)

                # Update job to completed
                job.status = JobStatus.COMPLETED.value
                job.result = {
                    "spans_found": len(prompt_spans),
                    "spans_evaluated": evaluated_count,
                    "spans_selected": len(spans_to_evaluate),
                    "errors": errors,
                }
                await db.commit()
                logger.info(
                    f"Prompt {prompt_slug} (project {project_id}): Evaluated {evaluated_count}/{len(spans_to_evaluate)} spans successfully"
                )

                return {
                    "project_id": str(project_id),
                    "prompt_slug": prompt_slug,
                    "status": "completed",
                    "spans_found": len(prompt_spans),
                    "spans_evaluated": evaluated_count,
                    "errors": errors,
                }

            except Exception as e:
                error_msg = f"Error evaluating prompt {prompt_slug} (project {project_id}): {str(e)}"
                logger.error(error_msg, exc_info=True)

                # Update job to failed
                job.status = JobStatus.FAILED.value
                job.result = {"error": str(e)}
                await db.commit()

                return {
                    "project_id": str(project_id),
                    "prompt_slug": prompt_slug,
                    "status": "failed",
                    "error": str(e),
                }
    finally:
        # CRITICAL: Dispose of the engine to close all connections
        await dispose_engine()


@shared_task(name="auto_evaluation.evaluate_prompt_spans", bind=True)
def evaluate_prompt_spans_task(
    self, prompt_id: str, project_id: str, prompt_slug: str, job_id: str
) -> dict[str, Any]:
    """
    Celery task to evaluate spans for a single prompt (dispatched by job reconciler).

    This task:
    1. Fetches unscored spans for the prompt
    2. Randomly selects up to 50 spans to evaluate
    3. Evaluates them using the prompt's criteria
    4. Stores the scores in feedback_score
    5. Updates the job with results

    Args:
        prompt_id: The prompt ID
        project_id: The project ID
        prompt_slug: The prompt slug
        job_id: The job ID tracking this work

    Returns:
        Dict with evaluation stats
    """
    return asyncio.run(
        _execute_prompt_spans_evaluation(prompt_id, project_id, prompt_slug, job_id)
    )
