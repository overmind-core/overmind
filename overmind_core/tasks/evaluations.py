import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from celery import shared_task
from litellm import RateLimitError
from sqlalchemy import select, and_
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    stop_after_delay,
    wait_exponential_jitter,
    before_sleep_log,
    RetryCallState,
)
from overmind_core.db.session import get_session_local
from overmind_core.models.traces import SpanModel
from overmind_core.models.jobs import Job
from overmind_core.overmind.llms import call_llm, try_json_parsing
from overmind_core.overmind.model_resolver import TaskType, resolve_model
from overmind_core.tasks.prompts import (
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
from overmind_core.tasks.criteria_generator import ensure_prompt_has_criteria
from overmind_core.tasks.agentic_span_processor import (
    preprocess_span_for_evaluation,
    format_conversation_flow,
    format_intermediate_steps,
    format_final_output,
    extract_tool_call_span_for_evaluation,
    extract_tool_answer_span_for_evaluation,
)

logger = logging.getLogger(__name__)

# Max concurrent span evaluations (controls thread + DB connection pressure)
_MAX_CONCURRENT_EVALUATIONS = 10


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


def _format_criteria(criteria_rules: List[str]) -> str:
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
    input_data: Dict[str, Any],
    output_data: Dict[str, Any],
    criteria_text: str,
    project_description: Optional[str] = None,
    agent_description: Optional[str] = None,
    span_metadata: Optional[Dict[str, Any]] = None,
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


def _extract_span_payload(span: SpanModel) -> Dict[str, Any]:
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
) -> tuple[str, Optional[str], Optional[str]]:
    """
    Get evaluation criteria, project description, and agent description for a span.

    Routes to type-specific default criteria based on response_type metadata:
    - "tool_calls" spans → DEFAULT_TOOL_CALL_CRITERIA
    - "text" + is_agentic spans → DEFAULT_TOOL_ANSWER_CRITERIA
    - legacy agentic spans → DEFAULT_AGENTIC_CRITERIA (with tool addendum)
    - plain spans → generic correctness criteria
    """
    from overmind_core.models.iam.projects import Project
    from overmind_core.models.prompts import Prompt
    from uuid import UUID

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
) -> Dict[str, Any]:
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
    span_ids: List[str],
    business_id: Optional[str] = None,
    user_id: Optional[str] = None,
    *,
    job_id: str,
) -> Dict[str, Any]:
    """
    Evaluate multiple spans based on provided criteria or prompt template criteria.

    If criteria is None, will fetch criteria from the prompt template linked to each span,
    or auto-generate criteria if the prompt doesn't have any.

    Updates the job status to completed/failed when done.
    """

    async def _run_evaluations():
        from overmind_core.db.session import dispose_engine
        from uuid import UUID

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

            async def _evaluate_with_limit(span_id: str) -> Dict[str, Any]:
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
                    job.status = "completed"
                    job.result = {
                        "spans_evaluated": success_count,
                        "spans_failed": error_count,
                        "total_spans": len(results),
                    }
                    await session.commit()
                    logger.info(
                        f"Updated job {job_id} to completed: {success_count}/{len(results)} spans evaluated successfully"
                    )

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
