import asyncio
import json
import logging
import random
import uuid
from typing import Any
from uuid import UUID

from celery import shared_task
from litellm import RateLimitError
from sqlalchemy import select, and_, or_, func, cast, String
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    stop_after_delay,
    wait_exponential_jitter,
    before_sleep_log,
    RetryCallState,
)
from overmind.db.session import get_session_local
from overmind.models.traces import SpanModel
from overmind.models.prompts import Prompt
from overmind.models.jobs import Job
from overmind.api.v1.endpoints.jobs import JobType, JobStatus
from overmind.core.llms import call_llm, try_json_parsing
from overmind.core.model_resolver import TaskType, resolve_model
from overmind.tasks.prompts import (
    CORRECTNESS_PROMPT_TEMPLATE,
    CORRECTNESS_SYSTEM_PROMPT,
    AGENTIC_CORRECTNESS_PROMPT_TEMPLATE,
    AGENTIC_CORRECTNESS_SYSTEM_PROMPT,
    DEFAULT_AGENTIC_CRITERIA,
    TOOL_CALL_CORRECTNESS_PROMPT_TEMPLATE,
    TOOL_CALL_CORRECTNESS_SYSTEM_PROMPT,
    TOOL_ANSWER_CORRECTNESS_PROMPT_TEMPLATE,
    TOOL_ANSWER_CORRECTNESS_SYSTEM_PROMPT,
    DEFAULT_TOOL_CALL_CRITERIA,
    DEFAULT_TOOL_ANSWER_CRITERIA,
)
from overmind.tasks.criteria_generator import ensure_prompt_has_criteria
from overmind.tasks.agentic_span_processor import (
    preprocess_span_for_evaluation,
    format_conversation_flow,
    format_intermediate_steps,
    format_final_output,
    extract_tool_call_span_for_evaluation,
    extract_tool_answer_span_for_evaluation,
)
from overmind.tasks.task_lock import with_task_lock

logger = logging.getLogger(__name__)

# Max concurrent span evaluations (controls thread + DB connection pressure)
_MAX_CONCURRENT_EVALUATIONS = 10

# Minimum unscored spans required before judge scoring is eligible
MIN_UNSCORED_SPANS_FOR_SCORING = 10


def _should_retry_llm_call(retry_state: RetryCallState) -> bool:
    """
    Custom retry predicate for LLM calls:
    - RateLimitError (429): keep retrying with backoff until the 5-min stop deadline.
    - Any other error: allow a single retry only (attempt 1 failed → try attempt 2).
    """
    exc = retry_state.outcome.exception()
    if exc is None:
        return False
    if isinstance(exc, RateLimitError):
        return True
    # For non-rate-limit errors: retry once (attempt_number is 1-based)
    return retry_state.attempt_number < 2


class CorrectnessResult(BaseModel):
    """Pydantic model for LLM correctness evaluation response."""

    correctness: float = Field(description="Correctness score from 0 to 1")


def _format_criteria(criteria_rules: list[str]) -> str:
    """Format a list of criteria rules as a readable numbered string."""
    return "\n".join(f"- Rule {i + 1}: {rule}" for i, rule in enumerate(criteria_rules))


@retry(
    retry=_should_retry_llm_call,
    wait=wait_exponential_jitter(initial=1, max=60, jitter=5),
    stop=stop_after_delay(300),  # 5 minute hard cap per LLM call
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def _evaluate_correctness_with_llm(
    input_data: dict[str, Any],
    output_data: dict[str, Any],
    criteria_text: str,
    project_description: str | None = None,
    agent_description: str | None = None,
    span_metadata: dict[str, Any] | None = None,
) -> float:
    """
    Call LLM to evaluate correctness and return a normalized score.

    Routes to the appropriate judge prompt based on span type:
    - response_type "tool_calls" → tool selection + argument quality judge
    - response_type "text" + is_agentic → answer faithfulness + completeness judge
    - legacy agentic (no response_type) → existing agentic judge
    - plain non-agentic → existing simple correctness judge

    Retry strategy:
    - Rate limit errors (429): exponential backoff with jitter (1s → 60s cap),
      retrying until 5-minute deadline is reached.
    - Other errors: single retry only, then re-raise.

    Args:
        input_data: The input payload
        output_data: The output payload
        criteria_text: Formatted criteria string
        project_description: Optional project context
        agent_description: Optional agent context
        span_metadata: Optional span metadata for agentic detection and tool resolution

    Returns:
        Correctness score between 0.0 and 1.0

    Raises:
        ValueError: If LLM response is invalid or missing correctness
    """
    metadata = span_metadata or {}
    response_type = metadata.get("response_type")
    is_agentic = metadata.get("is_agentic", False)

    if response_type == "tool_calls":
        # Evaluate tool selection quality and argument correctness
        components = extract_tool_call_span_for_evaluation(
            input_data=input_data,
            output_data=output_data,
            metadata_attributes=metadata,
        )
        available_tools_str = (
            json.dumps(components["available_tools"], indent=2)
            if components["available_tools"]
            else "No tool definitions available"
        )
        tool_calls_str = (
            json.dumps(components["tool_calls"], indent=2)
            if components["tool_calls"]
            else "No tool calls made"
        )
        prompt = TOOL_CALL_CORRECTNESS_PROMPT_TEMPLATE.format(
            user_query=components["user_query"],
            conversation_history=components["conversation_history"],
            available_tools=available_tools_str,
            tool_calls=tool_calls_str,
            criteria=criteria_text,
        )
        system_prompt = TOOL_CALL_CORRECTNESS_SYSTEM_PROMPT
        logger.info(
            f"Evaluating tool-call span with {len(components['tool_calls'])} tool call(s)"
        )

    elif response_type == "text" and is_agentic:
        # Evaluate faithfulness of the final answer against tool results
        components = extract_tool_answer_span_for_evaluation(
            input_data=input_data,
            output_data=output_data,
        )
        prompt = TOOL_ANSWER_CORRECTNESS_PROMPT_TEMPLATE.format(
            user_query=components["user_query"],
            conversation_flow=components["conversation_flow"],
            final_answer=components["final_answer"],
            criteria=criteria_text,
        )
        system_prompt = TOOL_ANSWER_CORRECTNESS_SYSTEM_PROMPT
        logger.info(
            f"Evaluating tool-answer span with {len(components['tool_results'])} tool result(s)"
        )

    else:
        project_context = (
            f"<ProjectContext>\n{project_description}\n</ProjectContext>\n"
            if project_description
            else ""
        )
        agent_context = (
            f"<AgentContext>\n{agent_description}\n</AgentContext>\n"
            if agent_description
            else ""
        )
        # Legacy path: detect agentic behavior from message structure
        processed = preprocess_span_for_evaluation(
            input_data=input_data,
            output_data=output_data,
            metadata=metadata,
        )

        if processed["is_agentic"]:
            logger.info(
                f"Evaluating agentic span with {processed['metadata']['tool_calls_count']} tool calls"
            )
            conversation_flow = format_conversation_flow(
                processed["conversation_turns"]
            )
            intermediate_steps = format_intermediate_steps(processed["tool_calls"])
            final_output_str = format_final_output(processed["final_output"])
            prompt = AGENTIC_CORRECTNESS_PROMPT_TEMPLATE.format(
                original_query=processed["original_query"],
                conversation_flow=conversation_flow,
                intermediate_steps=intermediate_steps,
                final_output=final_output_str,
                criteria=criteria_text,
            )
            system_prompt = AGENTIC_CORRECTNESS_SYSTEM_PROMPT
        else:
            prompt = CORRECTNESS_PROMPT_TEMPLATE.format(
                project_context=project_context,
                agent_context=agent_context,
                inputs=input_data,
                outputs=output_data,
                criteria=criteria_text,
            )
            system_prompt = CORRECTNESS_SYSTEM_PROMPT

    response, _ = call_llm(
        prompt,
        system_prompt=system_prompt,
        response_format=CorrectnessResult,
        model=resolve_model(TaskType.JUDGE_SCORING),
    )

    parsed = try_json_parsing(response)
    correctness = parsed.get("correctness")
    if correctness is None:
        raise ValueError("LLM response missing correctness key")

    try:
        correctness_value = float(correctness)
    except (TypeError, ValueError) as exc:
        raise ValueError("Correctness score is not a number") from exc

    # Clamp to valid range
    return max(0.0, min(1.0, correctness_value))


def _extract_span_payload(span: SpanModel) -> dict[str, Any]:
    """Extract input, output, metadata, and input_params from a span model."""
    return {
        "input": span.input or {},
        "output": span.output or {},
        "metadata": span.metadata_attributes or {},
        "input_params": span.input_params or {},
    }


async def _store_span_score(span_id: str, correctness: float) -> bool:
    """Store correctness score in span's feedback_score field."""
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SpanModel).where(SpanModel.span_id == span_id)
        )
        span = result.scalar_one_or_none()
        if span is None:
            logger.warning(f"Span not found in Postgres for id={span_id}")
            return False

        feedback = dict(span.feedback_score or {})
        feedback["correctness"] = correctness
        span.feedback_score = feedback
        await session.commit()
        return True


async def _get_context_for_span(
    span: SpanModel,
) -> tuple[str, str | None, str | None]:
    """
    Get evaluation criteria, project description, and agent description for a span.

    Routes to type-specific default criteria based on response_type metadata:
    - "tool_calls" spans → DEFAULT_TOOL_CALL_CRITERIA
    - "text" + is_agentic spans → DEFAULT_TOOL_ANSWER_CRITERIA
    - legacy agentic spans → DEFAULT_AGENTIC_CRITERIA (with tool addendum)
    - plain spans → generic correctness criteria
    """
    from overmind.models.iam.projects import Project

    project_description = None
    agent_description = None
    metadata = span.metadata_attributes or {}
    response_type = metadata.get("response_type")
    is_agentic = metadata.get("is_agentic", False)

    # For legacy agentic spans without response_type, detect from message structure
    if not response_type and not is_agentic:
        processed = preprocess_span_for_evaluation(
            input_data=span.input or {},
            output_data=span.output or {},
            metadata=metadata,
        )
        is_agentic = processed["is_agentic"]

    # If span has a linked prompt, fetch criteria from prompt
    base_criteria = None

    if span.prompt_id:
        criteria_dict = await ensure_prompt_has_criteria(span.prompt_id)
        if criteria_dict and "correctness" in criteria_dict:
            rules = criteria_dict["correctness"]
            base_criteria = _format_criteria(rules)
        else:
            logger.warning(f"No criteria found for span {span.span_id}, using defaults")

        # Fetch agent description and project description from DB
        AsyncSessionLocal = get_session_local()
        async with AsyncSessionLocal() as session:
            try:
                project_id_str, version, slug = Prompt.parse_prompt_id(span.prompt_id)
                project_uuid = UUID(project_id_str)

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
                if prompt and prompt.agent_description:
                    agent_description = prompt.agent_description.get("description")

                project_result = await session.execute(
                    select(Project).where(Project.project_id == project_uuid)
                )
                project = project_result.scalar_one_or_none()
                if project and project.description:
                    project_description = project.description

            except (ValueError, TypeError) as e:
                logger.error(f"Error getting context for span: {e}")
    else:
        logger.warning(f"No prompt_id found for span {span.span_id}, using defaults")

    # Fallback to type-specific default criteria
    if not base_criteria:
        if response_type == "tool_calls":
            base_criteria = DEFAULT_TOOL_CALL_CRITERIA
        elif response_type == "text" and is_agentic:
            base_criteria = DEFAULT_TOOL_ANSWER_CRITERIA
        elif is_agentic:
            base_criteria = DEFAULT_AGENTIC_CRITERIA
        else:
            base_criteria = """- Provides accurate and complete information
- Contains no factual errors
- Addresses all parts of the question
- Is logically consistent
- Uses precise and accurate terminology"""

    # For legacy agentic spans (no response_type), append tool criteria if missing
    if is_agentic and not response_type and "tool" not in base_criteria.lower():
        agentic_addendum = (
            "\n\nAdditional criteria for tool-using agents:\n"
            + DEFAULT_AGENTIC_CRITERIA
        )
        base_criteria = base_criteria + agentic_addendum

    return base_criteria, project_description, agent_description


async def _evaluate_span_correctness(
    span_id: str,
) -> dict[str, Any]:
    """Evaluate a single span's correctness using LLM."""
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        # Get span
        result = await session.execute(
            select(SpanModel).where(SpanModel.span_id == span_id)
        )
        span = result.scalar_one_or_none()

        if span is None:
            raise ValueError(f"Span not found: {span_id}")

        payload = _extract_span_payload(span)

    # Get criteria, project description, and agent description
    (
        evaluation_criteria,
        project_description,
        agent_description,
    ) = await _get_context_for_span(span)

    # Evaluate correctness - run sync LLM call (with retries) in a thread
    # so it doesn't block the event loop and other spans can progress concurrently
    correctness_value = await asyncio.to_thread(
        _evaluate_correctness_with_llm,
        input_data=payload["input"],
        output_data=payload["output"],
        criteria_text=evaluation_criteria,
        project_description=project_description,
        agent_description=agent_description,
        span_metadata=payload["metadata"],
    )

    stored = await _store_span_score(span_id, correctness_value)

    return {
        "span_id": span_id,
        "correctness": correctness_value,
        "stored": stored,
    }


@shared_task(name="evaluations.evaluate_spans")
def evaluate_spans_task(
    span_ids: list[str],
    business_id: str | None = None,
    user_id: str | None = None,
    *,
    job_id: str,
) -> dict[str, Any]:
    """
    Evaluate multiple spans based on provided criteria or prompt template criteria.

    If criteria is None, will fetch criteria from the prompt template linked to each span,
    or auto-generate criteria if the prompt doesn't have any.

    Updates the job status to completed/failed when done.
    """

    async def _run_evaluations():
        from overmind.db.session import dispose_engine

        try:
            AsyncSessionLocal = get_session_local()
            async with AsyncSessionLocal() as session:
                job_result = await session.execute(
                    select(Job).where(Job.job_id == UUID(job_id))
                )
                job = job_result.scalar_one_or_none()
                if not job:
                    raise ValueError(f"Job {job_id} not found")

                job.status = "running"
                await session.commit()

            semaphore = asyncio.Semaphore(_MAX_CONCURRENT_EVALUATIONS)

            async def _evaluate_with_limit(span_id: str) -> dict[str, Any]:
                async with semaphore:
                    try:
                        return await _evaluate_span_correctness(span_id=span_id)
                    except BaseException as exc:
                        logger.error(f"Failed to evaluate span {span_id}: {exc}")
                        return {"span_id": span_id, "error": str(exc)}

            # Fan out all span evaluations concurrently (bounded by semaphore).
            # asyncio.gather preserves input order so results stay aligned with span_ids.
            results = await asyncio.gather(
                *[_evaluate_with_limit(span_id) for span_id in span_ids]
            )

            # Count successes and failures
            success_count = sum(1 for r in results if "error" not in r)
            error_count = len(results) - success_count

            result_data = {
                "business_id": business_id,
                "user_id": user_id,
                "count": len(results),
                "success_count": success_count,
                "error_count": error_count,
                "results": list(results),
            }

            AsyncSessionLocal = get_session_local()
            async with AsyncSessionLocal() as session:
                job_result = await session.execute(
                    select(Job).where(Job.job_id == UUID(job_id))
                )
                job = job_result.scalar_one_or_none()
                if job:
                    total = len(results)
                    if success_count == 0:
                        job.status = JobStatus.FAILED.value
                        logger.error(
                            f"Job {job_id} failed: 0/{total} spans evaluated successfully"
                        )
                    elif success_count < total:
                        job.status = JobStatus.PARTIALLY_COMPLETED.value
                        logger.warning(
                            f"Job {job_id} partially completed: {success_count}/{total} spans evaluated successfully"
                        )
                    else:
                        job.status = JobStatus.COMPLETED.value
                        logger.info(
                            f"Job {job_id} completed: {success_count}/{total} spans evaluated successfully"
                        )
                    job.result = {
                        "spans_evaluated": success_count,
                        "spans_failed": error_count,
                        "total_spans": total,
                    }
                    await session.commit()

            return result_data

        except BaseException as exc:
            try:
                AsyncSessionLocal = get_session_local()
                async with AsyncSessionLocal() as session:
                    job_result = await session.execute(
                        select(Job).where(Job.job_id == UUID(job_id))
                    )
                    job = job_result.scalar_one_or_none()
                    if job:
                        job.status = "failed"
                        job.result = {"error": str(exc)}
                        await session.commit()
                        logger.error(f"Updated job {job_id} to failed: {exc}")
            except BaseException as job_update_exc:
                logger.error(f"Failed to update job status: {job_update_exc}")

            raise

        finally:
            # Safety net: if the job is still "running" here (e.g. CancelledError or
            # KeyboardInterrupt bypassed the except block above), mark it failed so it
            # never gets stuck in "running" permanently.
            try:
                AsyncSessionLocal = get_session_local()
                async with AsyncSessionLocal() as session:
                    job_result = await session.execute(
                        select(Job).where(Job.job_id == UUID(job_id))
                    )
                    job = job_result.scalar_one_or_none()
                    if job and job.status == "running":
                        job.status = "failed"
                        job.result = {"error": "Task was cancelled or interrupted"}
                        await session.commit()
                        logger.error(
                            f"Job {job_id} was still 'running' at cleanup — marked as failed"
                        )
            except BaseException as cleanup_exc:
                logger.error(
                    f"Failed to update job status in finally block: {cleanup_exc}"
                )

            # CRITICAL: Dispose of the engine to close all connections
            # This prevents event loop errors when the same worker runs the task again
            await dispose_engine()

    return asyncio.run(_run_evaluations())


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
    from overmind.db.session import dispose_engine

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
    from overmind.db.session import dispose_engine

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

                # Determine final status based on how many spans were evaluated
                selected = len(spans_to_evaluate)
                if evaluated_count == 0:
                    job.status = JobStatus.FAILED.value
                    logger.error(
                        f"Prompt {prompt_slug} (project {project_id}): 0/{selected} spans evaluated — marking job as failed"
                    )
                elif evaluated_count < selected:
                    job.status = JobStatus.PARTIALLY_COMPLETED.value
                    logger.warning(
                        f"Prompt {prompt_slug} (project {project_id}): {evaluated_count}/{selected} spans evaluated — marking job as partially completed"
                    )
                else:
                    job.status = JobStatus.COMPLETED.value
                    logger.info(
                        f"Prompt {prompt_slug} (project {project_id}): {evaluated_count}/{selected} spans evaluated successfully"
                    )
                job.result = {
                    "spans_found": len(prompt_spans),
                    "spans_evaluated": evaluated_count,
                    "spans_selected": selected,
                    "errors": errors,
                }
                await db.commit()

                final_status = (
                    "failed"
                    if evaluated_count == 0
                    else (
                        "partially_completed"
                        if evaluated_count < selected
                        else "completed"
                    )
                )
                return {
                    "project_id": str(project_id),
                    "prompt_slug": prompt_slug,
                    "status": final_status,
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
