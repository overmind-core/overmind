import asyncio
import json
import logging
import random
import uuid
from typing import Any
from uuid import UUID

from celery import shared_task
from sqlalchemy import select, and_, or_, func, cast, String
from sqlalchemy.orm.attributes import flag_modified
from pydantic import BaseModel, Field
from overmind.db.session import get_session_local
from overmind.models.traces import SpanModel
from overmind.models.prompts import Prompt, PROMPT_STATUS_ACTIVE
from overmind.models.jobs import Job
from overmind.api.v1.endpoints.jobs import JobType, JobStatus
from overmind.core.llms import call_llm, try_json_parsing
from overmind.core.model_resolver import TaskType, resolve_model
from overmind.tasks.utils.prompts import (
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
from overmind.tasks.utils.task_lock import with_task_lock

logger = logging.getLogger(__name__)

# Max concurrent span evaluations (controls thread + DB connection pressure)
_MAX_CONCURRENT_EVALUATIONS = 10

# Maximum number of spans written per DB transaction in _batch_persist_evaluation_results.
# Keeps individual transactions bounded so a transient error only loses one chunk.
# 200 is well above the auto-evaluation cap of 50 spans/prompt, so the common path
# is still a single commit; larger user-triggered batches are split automatically.
# Named _EVAL_PERSIST_CHUNK_SIZE (not _PERSIST_CHUNK_SIZE) to avoid confusion with
# backtesting.py's _BACKTEST_PERSIST_CHUNK_SIZE, which uses a different value (50).
_EVAL_PERSIST_CHUNK_SIZE = 200

# Maximum scored spans per prompt before the initial agent review is required.
# Once this cap is reached, scoring pauses until the user completes the review.
PRE_REVIEW_SCORED_SPAN_CAP = 30


REASON_SCORE_THRESHOLD = 0.5

# Number of times to retry a failed JSON parse before marking the span as errored.
# call_llm already retries transient network/API errors internally; these retries
# target invalid or unparseable LLM responses (missing "correctness" key, bad JSON, etc.).
_MAX_EVAL_PARSE_RETRIES = 3


class CorrectnessResult(BaseModel):
    """Pydantic model for LLM correctness evaluation response."""

    correctness: float = Field(description="Correctness score from 0 to 1")
    reason: str = Field(
        default="",
        description=f"Brief 1-2 sentence explanation of why the score is low. Populate only when the score < {REASON_SCORE_THRESHOLD}; leave as empty string otherwise.",
    )


def _format_criteria(criteria_rules: list[str]) -> str:
    """Format a list of criteria rules as a readable numbered string."""
    return "\n".join(f"- Rule {i + 1}: {rule}" for i, rule in enumerate(criteria_rules))


def _evaluate_correctness_with_llm(
    input_data: dict[str, Any],
    output_data: dict[str, Any],
    criteria_text: str,
    project_description: str | None = None,
    agent_description: str | None = None,
    span_metadata: dict[str, Any] | None = None,
) -> tuple[float, str | None]:
    """
    Call LLM to evaluate correctness and return a normalized score with optional reason.

    Routes to the appropriate judge prompt based on span type:
    - response_type "tool_calls" → tool selection + argument quality judge
    - response_type "text" + is_agentic → answer faithfulness + completeness judge
    - legacy agentic (no response_type) → existing agentic judge
    - plain non-agentic → existing simple correctness judge

    When the score is below REASON_SCORE_THRESHOLD (0.5), the LLM also returns a brief
    reason explaining the low score. For scores >= 0.5, reason is omitted to save tokens.

    Transient errors (rate limits, overloaded, unavailable) are retried automatically
    inside call_llm with exponential backoff up to a 5-minute deadline.

    Args:
        input_data: The input payload
        output_data: The output payload
        criteria_text: Formatted criteria string
        project_description: Optional project context
        agent_description: Optional agent context
        span_metadata: Optional span metadata for agentic detection and tool resolution

    Returns:
        Tuple of (correctness_score, reason) where correctness_score is between 0.0 and 1.0
        and reason is a brief explanation only present when score < REASON_SCORE_THRESHOLD.

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
    correctness_value = max(0.0, min(1.0, correctness_value))

    # Only surface the reason when score is below threshold
    reason: str | None = None
    if correctness_value < REASON_SCORE_THRESHOLD:
        raw_reason = parsed.get("reason")
        if raw_reason and isinstance(raw_reason, str):
            reason = raw_reason.strip() or None

    return correctness_value, reason


def _extract_span_payload(span: SpanModel) -> dict[str, Any]:
    """Extract input, output, metadata, and input_params from a span model."""
    return {
        "input": span.input or {},
        "output": span.output or {},
        "metadata": span.metadata_attributes or {},
        "input_params": span.input_params or {},
    }


async def _bulk_fetch_criteria(
    prompt_ids: list[str],
) -> dict[str, dict[str, list[str]] | None]:
    """Fetch evaluation_criteria for multiple prompt IDs in two queries.

    Phase 1: one IN-query fetches all Prompt rows that already have criteria.
    Phase 2: for any prompt that is missing criteria, fall back to
    ``ensure_prompt_has_criteria`` (which will generate them) — these are
    rare and handled concurrently with ``return_exceptions=True``.

    This avoids opening N independent DB sessions (one per prompt) when the
    common case is that all prompts already have criteria stored.
    """
    from sqlalchemy import tuple_ as sa_tuple

    criteria_by_prompt: dict[str, dict[str, list[str]] | None] = {}

    if not prompt_ids:
        return criteria_by_prompt

    # Parse all prompt IDs upfront; skip malformed ones
    parsed: list[tuple[str, UUID, int, str]] = []
    for pid in prompt_ids:
        try:
            project_id_str, version, slug = Prompt.parse_prompt_id(pid)
            parsed.append((pid, UUID(project_id_str), version, slug))
        except (ValueError, TypeError) as e:
            logger.warning(f"Skipping malformed prompt_id {pid}: {e}")
            criteria_by_prompt[pid] = None

    if not parsed:
        return criteria_by_prompt

    # Bulk-fetch all Prompt rows in one query
    prompt_keys = [(p_uuid, ver, sl) for _, p_uuid, ver, sl in parsed]
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(Prompt).where(
                sa_tuple(Prompt.project_id, Prompt.version, Prompt.slug).in_(
                    prompt_keys
                )
            )
        )
        prompts_by_key: dict[tuple, Prompt] = {
            (p.project_id, p.version, p.slug): p for p in rows.scalars().all()
        }

    # Separate prompts that already have criteria from those that need generation
    needs_generation: list[str] = []
    for pid, p_uuid, version, slug in parsed:
        prompt = prompts_by_key.get((p_uuid, version, slug))
        if prompt and prompt.evaluation_criteria:
            criteria_by_prompt[pid] = prompt.evaluation_criteria
        else:
            needs_generation.append(pid)

    # Generate criteria for the rare prompts that are missing them (concurrent)
    if needs_generation:
        gen_values = await asyncio.gather(
            *[ensure_prompt_has_criteria(pid) for pid in needs_generation],
            return_exceptions=True,
        )
        for pid, val in zip(needs_generation, gen_values):
            if isinstance(val, BaseException):
                logger.warning(
                    f"Failed to fetch/generate criteria for prompt {pid}: {val}"
                )
                criteria_by_prompt[pid] = None
            else:
                criteria_by_prompt[pid] = val

    return criteria_by_prompt


async def _batch_persist_evaluation_results(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Write evaluation scores and errors in chunked DB transactions.

    Processes up to _EVAL_PERSIST_CHUNK_SIZE results per session/commit.  Each chunk
    fetches its spans with a single IN-query and commits once, so a transient DB
    error only loses one chunk rather than the entire batch.  Each chunk is retried
    once on failure (matching the backtesting.py pattern) before being skipped.

    Returns the same list with a ``stored`` key added to each entry (True if the
    span was found and written, False if it was missing from the DB or the chunk
    failed to commit after retry).
    """
    if not results:
        return []

    all_updated: list[dict[str, Any]] = []
    AsyncSessionLocal = get_session_local()

    for chunk_start in range(0, len(results), _EVAL_PERSIST_CHUNK_SIZE):
        chunk = results[chunk_start : chunk_start + _EVAL_PERSIST_CHUNK_SIZE]
        chunk_ids = [r["span_id"] for r in chunk]
        chunk_end = chunk_start + len(chunk) - 1

        chunk_updated: list[dict[str, Any]] | None = None
        for attempt in range(2):
            try:
                async with AsyncSessionLocal() as session:
                    rows = await session.execute(
                        select(SpanModel).where(SpanModel.span_id.in_(chunk_ids))
                    )
                    spans_by_id: dict[str, SpanModel] = {
                        s.span_id: s for s in rows.scalars().all()
                    }

                    attempt_updated: list[dict[str, Any]] = []
                    for result in chunk:
                        sid = result["span_id"]
                        span = spans_by_id.get(sid)
                        if span is None:
                            logger.warning(f"Span not found in Postgres for id={sid}")
                            attempt_updated.append({**result, "stored": False})
                            continue

                        feedback = dict(span.feedback_score or {})

                        if result.get("eval_error"):
                            feedback["correctness_error"] = result.get(
                                "error", "unknown error"
                            )
                            feedback.pop("correctness", None)
                            feedback.pop("correctness_reason", None)
                        else:
                            feedback["correctness"] = result["correctness"]
                            reason = result.get("reason")
                            if reason is not None:
                                feedback["correctness_reason"] = reason
                            else:
                                # Remove stale reason if span is being re-scored
                                feedback.pop("correctness_reason", None)
                            # Clear any previous evaluation error on successful re-score
                            feedback.pop("correctness_error", None)

                        span.feedback_score = feedback
                        flag_modified(span, "feedback_score")
                        attempt_updated.append({**result, "stored": True})

                    await session.commit()
                chunk_updated = attempt_updated
                break
            except Exception as persist_exc:
                if attempt == 0:
                    logger.warning(
                        f"Evaluation persist failed (items {chunk_start}–{chunk_end}), "
                        f"retrying: {persist_exc}"
                    )
                else:
                    logger.error(
                        f"Evaluation persist failed after retry (items {chunk_start}–"
                        f"{chunk_end}), skipping chunk: {persist_exc}"
                    )

        if chunk_updated is not None:
            all_updated.extend(chunk_updated)
        else:
            # Both attempts failed — mark every span in the chunk as not stored.
            for result in chunk:
                all_updated.append({**result, "stored": False})

    return all_updated


async def _prefetch_prompt_contexts(
    spans: list[SpanModel],
    session=None,
) -> dict[str, tuple[str | None, str | None]]:
    """Pre-fetch (agent_description, project_description) for every unique prompt_id.

    Issues exactly two bulk queries — one IN-query for all needed Prompt rows and
    one IN-query for all needed Project rows — instead of 2×K sequential queries
    (K = number of unique prompt IDs).

    If *session* is provided it is reused directly (no new connection checkout).
    Otherwise a new session is opened and closed internally.

    Returns a dict mapping prompt_id → (agent_description, project_description).
    """
    from overmind.models.iam.projects import Project
    from sqlalchemy import tuple_ as sa_tuple

    prompt_ids_to_fetch = {span.prompt_id for span in spans if span.prompt_id}
    context_by_prompt: dict[str, tuple[str | None, str | None]] = {}

    if not prompt_ids_to_fetch:
        return context_by_prompt

    # Parse all prompt_ids upfront; skip malformed ones
    parsed: list[
        tuple[str, UUID, int, str]
    ] = []  # (prompt_id_str, project_uuid, version, slug)
    for prompt_id_str in prompt_ids_to_fetch:
        try:
            project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id_str)
            parsed.append((prompt_id_str, UUID(project_id_str), version, slug))
        except (ValueError, TypeError) as e:
            logger.error(f"Error parsing prompt_id {prompt_id_str}: {e}")
            context_by_prompt[prompt_id_str] = (None, None)

    if not parsed:
        return context_by_prompt

    async def _run_queries(sess) -> None:
        prompt_keys = [(p_uuid, ver, sl) for _, p_uuid, ver, sl in parsed]
        prompts_result = await sess.execute(
            select(Prompt).where(
                sa_tuple(Prompt.project_id, Prompt.version, Prompt.slug).in_(
                    prompt_keys
                )
            )
        )
        prompts_by_key: dict[tuple, Prompt] = {
            (p.project_id, p.version, p.slug): p for p in prompts_result.scalars().all()
        }

        project_uuids = list({p_uuid for _, p_uuid, _, _ in parsed})
        projects_result = await sess.execute(
            select(Project).where(Project.project_id.in_(project_uuids))
        )
        projects_by_id: dict[UUID, Project] = {
            p.project_id: p for p in projects_result.scalars().all()
        }

        for prompt_id_str, p_uuid, version, slug in parsed:
            prompt = prompts_by_key.get((p_uuid, version, slug))
            agent_description: str | None = None
            if prompt and prompt.agent_description:
                agent_description = prompt.agent_description.get("description")

            project = projects_by_id.get(p_uuid)
            project_description: str | None = None
            if project and project.description:
                project_description = project.description

            context_by_prompt[prompt_id_str] = (agent_description, project_description)

    if session is not None:
        await _run_queries(session)
    else:
        AsyncSessionLocal = get_session_local()
        async with AsyncSessionLocal() as new_session:
            await _run_queries(new_session)

    return context_by_prompt


async def _get_context_for_span(
    span: SpanModel,
    criteria_by_prompt: dict[str, dict[str, list[str]] | None] | None = None,
    context_by_prompt: dict[str, tuple[str | None, str | None]] | None = None,
) -> tuple[str, str | None, str | None]:
    """
    Get evaluation criteria, project description, and agent description for a span.

    Routes to type-specific default criteria based on response_type metadata:
    - "tool_calls" spans → DEFAULT_TOOL_CALL_CRITERIA
    - "text" + is_agentic spans → DEFAULT_TOOL_ANSWER_CRITERIA
    - legacy agentic spans → DEFAULT_AGENTIC_CRITERIA (with tool addendum)
    - plain spans → generic correctness criteria

    When *criteria_by_prompt* is supplied, criteria are looked up there first
    (and stored back on miss) so a batch of spans sharing the same prompt only
    hits the DB once.
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
        # Use pre-fetched criteria when available, falling back to DB
        if criteria_by_prompt is not None and span.prompt_id in criteria_by_prompt:
            criteria_dict = criteria_by_prompt[span.prompt_id]
        else:
            criteria_dict = await ensure_prompt_has_criteria(span.prompt_id)
            if criteria_by_prompt is not None:
                criteria_by_prompt[span.prompt_id] = criteria_dict

        if criteria_dict and "correctness" in criteria_dict:
            rules = criteria_dict["correctness"]
            base_criteria = _format_criteria(rules)
        else:
            logger.warning(f"No criteria found for span {span.span_id}, using defaults")

        # Use pre-fetched agent/project context when available; fall back to a
        # dedicated DB fetch for callers that don't pre-fetch (e.g. standalone use).
        if context_by_prompt is not None and span.prompt_id in context_by_prompt:
            agent_description, project_description = context_by_prompt[span.prompt_id]
        else:
            AsyncSessionLocal = get_session_local()
            async with AsyncSessionLocal() as session:
                try:
                    project_id_str, version, slug = Prompt.parse_prompt_id(
                        span.prompt_id
                    )
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
    span: SpanModel,
    criteria_by_prompt: dict[str, dict[str, list[str]] | None] | None = None,
    context_by_prompt: dict[str, tuple[str | None, str | None]] | None = None,
) -> dict[str, Any]:
    """Evaluate a single span's correctness using LLM.

    Accepts a pre-loaded SpanModel so callers can batch-fetch all spans in one
    DB round-trip before the concurrent fan-out. Does not open any DB session —
    all persistence is delegated to ``_batch_persist_evaluation_results``.
    """
    span_id = span.span_id
    payload = _extract_span_payload(span)

    # Get criteria, project description, and agent description
    (
        evaluation_criteria,
        project_description,
        agent_description,
    ) = await _get_context_for_span(
        span,
        criteria_by_prompt=criteria_by_prompt,
        context_by_prompt=context_by_prompt,
    )

    # Evaluate correctness with parse-failure retries.
    # call_llm already retries transient network/API errors internally via tenacity;
    # these retries target malformed LLM responses (missing key, bad JSON, etc.).
    last_error: Exception | None = None
    correctness_value: float = (
        0.0  # Only for type checker; always overwritten before use
    )
    reason: str | None = None

    for attempt in range(_MAX_EVAL_PARSE_RETRIES):
        try:
            correctness_value, reason = await asyncio.to_thread(
                _evaluate_correctness_with_llm,
                input_data=payload["input"],
                output_data=payload["output"],
                criteria_text=evaluation_criteria,
                project_description=project_description,
                agent_description=agent_description,
                span_metadata=payload["metadata"],
            )
            last_error = None
            break
        except ValueError as exc:
            last_error = exc
            logger.warning(
                f"JSON parse attempt {attempt + 1}/{_MAX_EVAL_PARSE_RETRIES} "
                f"failed for span {span_id}: {exc}"
            )
        except Exception as exc:
            # Non-ValueError (e.g. network timeout) — don't store as eval_error;
            # span stays unscored and will be retried on future runs.
            logger.warning(
                f"Unexpected exception during evaluation for span {span_id}: "
                f"{type(exc).__name__}: {exc}"
            )
            raise
    else:
        # All retries exhausted — the caller's _batch_persist_evaluation_results
        # will store the error so the span is excluded from future scoring runs.
        error_msg = str(last_error)
        logger.error(
            f"Evaluation permanently failed for span {span_id} after "
            f"{_MAX_EVAL_PARSE_RETRIES} attempts: {error_msg}"
        )
        return {"span_id": span_id, "error": error_msg, "eval_error": True}

    return {
        "span_id": span_id,
        "correctness": correctness_value,
        "reason": reason,
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

        AsyncSessionLocal = get_session_local()

        try:
            async with AsyncSessionLocal() as session:
                job_result = await session.execute(
                    select(Job).where(Job.job_id == UUID(job_id))
                )
                job = job_result.scalar_one_or_none()
                if not job:
                    raise ValueError(f"Job {job_id} not found")

                job.status = "running"
                await session.commit()

            # Phase 1: pre-fetch all spans + agent/project context in one session.
            # Criteria are fetched separately via _bulk_fetch_criteria (which issues
            # its own IN-query) so that missing-criteria generation can run concurrently
            # with any other work without holding this session open.
            async with AsyncSessionLocal() as session:
                rows = await session.execute(
                    select(SpanModel).where(SpanModel.span_id.in_(span_ids))
                )
                spans_by_id: dict[str, SpanModel] = {
                    s.span_id: s for s in rows.scalars().all()
                }
                context_by_prompt = await _prefetch_prompt_contexts(
                    list(spans_by_id.values()), session=session
                )
                # Detach spans before the session closes so that column access
                # outside this block is served from in-memory state rather than
                # triggering a lazy-load against a closed session.
                for span in spans_by_id.values():
                    session.expunge(span)

            # Phase 2: bulk-fetch criteria for every unique prompt.
            # _bulk_fetch_criteria uses one IN-query for the common case (criteria
            # already exist) and only falls back to per-prompt generation for the
            # rare prompts that are missing criteria.
            unique_prompt_ids = list(
                {s.prompt_id for s in spans_by_id.values() if s.prompt_id}
            )
            criteria_by_prompt = await _bulk_fetch_criteria(unique_prompt_ids)

            semaphore = asyncio.Semaphore(_MAX_CONCURRENT_EVALUATIONS)

            async def _evaluate_with_limit(span_id: str) -> dict[str, Any]:
                span = spans_by_id.get(span_id)
                if span is None:
                    return {"span_id": span_id, "error": f"Span not found: {span_id}"}
                async with semaphore:
                    try:
                        return await _evaluate_span_correctness(
                            span=span,
                            criteria_by_prompt=criteria_by_prompt,
                            context_by_prompt=context_by_prompt,
                        )
                    except Exception as exc:
                        logger.exception(f"Failed to evaluate span {span_id}")
                        return {"span_id": span_id, "error": str(exc)}

            # Phase 3: fan out all LLM evaluations concurrently (bounded by semaphore).
            # asyncio.gather preserves input order so results stay aligned with span_ids.
            raw_results = await asyncio.gather(
                *[_evaluate_with_limit(span_id) for span_id in span_ids]
            )

            # Persist all scores/errors in a single DB session instead of one per span.
            results = await _batch_persist_evaluation_results(list(raw_results))

            # Count successes: evaluated without error AND written to the DB.
            # stored=False means the span was missing from the DB at persist time;
            # those should not be reported as successfully evaluated.
            success_count = sum(
                1 for r in results if "error" not in r and r.get("stored", True)
            )
            error_count = len(results) - success_count

            result_data = {
                "business_id": business_id,
                "user_id": user_id,
                "count": len(results),
                "success_count": success_count,
                "error_count": error_count,
                "results": list(results),
            }

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

    # Check 2: Pre-review scoring cap — pause scoring once PRE_REVIEW_SCORED_SPAN_CAP is
    # reached so the user reviews and aligns the judge before more spans are evaluated
    agent_desc = prompt.agent_description or {}
    if not agent_desc.get("initial_review_completed"):
        scored_count_q = await session.execute(
            select(func.count(SpanModel.span_id)).where(
                and_(
                    SpanModel.prompt_id == prompt_id,
                    SpanModel.feedback_score.has_key("correctness"),
                    SpanModel.exclude_system_spans(),
                )
            )
        )
        pre_review_scored_count = scored_count_q.scalar() or 0
        stats["pre_review_scored_count"] = pre_review_scored_count

        if pre_review_scored_count >= PRE_REVIEW_SCORED_SPAN_CAP:
            return (
                False,
                "Scoring is paused — please complete the initial agent review to align the judge before more spans are evaluated.",
                stats,
            )

    # Check 3: Find unscored spans for this prompt.
    # The SQL expression below replicates Prompt.prompt_id ("{project_id}_{version}_{slug}").
    # If Prompt.parse_prompt_id ever changes its separator or format, this must be updated too.
    prompt_id_expr = func.concat(
        cast(Prompt.project_id, String),
        "_",
        cast(Prompt.version, String),
        "_",
        Prompt.slug,
    )
    count_result = await session.execute(
        select(func.count(SpanModel.span_id))
        .join(Prompt, SpanModel.prompt_id == prompt_id_expr)
        .where(
            and_(
                Prompt.project_id == project_id,
                Prompt.slug == prompt_slug,
                or_(
                    SpanModel.feedback_score.is_(None),
                    and_(
                        ~SpanModel.feedback_score.has_key("correctness"),
                        ~SpanModel.feedback_score.has_key("correctness_error"),
                    ),
                ),
                Prompt.evaluation_criteria.isnot(None),
                Prompt.evaluation_criteria.has_key("correctness"),
                SpanModel.exclude_system_spans(),
            )
        )
    )
    unscored_count = count_result.scalar() or 0
    stats["unscored_spans_count"] = unscored_count

    if unscored_count == 0:
        return (
            False,
            "No unscored requests found for this agent.",
            stats,
        )

    # Check 4: No existing PENDING/RUNNING judge scoring job
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

    Uses a single JOIN query instead of fetching all spans and then issuing
    one prompt lookup per span (N+1 pattern).
    """
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        # Replicates Prompt.prompt_id ("{project_id}_{version}_{slug}") in SQL.
        # Must stay in sync with Prompt.parse_prompt_id if the format ever changes.
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
                    or_(
                        SpanModel.feedback_score.is_(None),
                        and_(
                            ~SpanModel.feedback_score.has_key("correctness"),
                            ~SpanModel.feedback_score.has_key("correctness_error"),
                        ),
                    ),
                    Prompt.evaluation_criteria.isnot(None),
                    Prompt.evaluation_criteria.has_key("correctness"),
                    SpanModel.exclude_system_spans(),
                )
            )
        )
        return list(result.scalars().all())


async def _auto_evaluate_unscored_spans(
    celery_task_id: str | None = None,
) -> dict[str, Any]:
    """
    Automatically evaluate unscored spans prompt by prompt.

    Logic:
    - Groups unscored spans by (project_id, prompt_slug)
    - For each eligible prompt, randomly selects up to 50 spans to evaluate
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
                    # Get the active prompt version for eligibility validation.
                    # Prefer is_active=True; fall back to max version for legacy data.
                    active_result = await db.execute(
                        select(Prompt).where(
                            and_(
                                Prompt.project_id == project_id,
                                Prompt.slug == prompt_slug,
                                Prompt.status == PROMPT_STATUS_ACTIVE,
                            )
                        )
                    )
                    prompt = active_result.scalar_one_or_none()
                    if not prompt:
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


@shared_task(name="evaluations.evaluate_unscored_spans", bind=True)
@with_task_lock(lock_name="auto_evaluate_unscored_spans")
def auto_evaluate_unscored_spans_task(self) -> dict[str, Any]:
    """
    Celery periodic task to check unscored spans and create evaluation jobs.

    This task runs every 10 minutes and:
    1. Finds spans without correctness scores
    2. Filters for spans linked to prompts with evaluation criteria
    3. Groups spans by (project_id, prompt_slug)
    4. For each eligible prompt with unscored spans, creates a PENDING job
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
                # Find unscored spans for this prompt.
                # Replicates Prompt.prompt_id ("{project_id}_{version}_{slug}") in SQL.
                # Must stay in sync with Prompt.parse_prompt_id if the format ever changes.
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
                                and_(
                                    ~SpanModel.feedback_score.has_key("correctness"),
                                    ~SpanModel.feedback_score.has_key(
                                        "correctness_error"
                                    ),
                                ),
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

                # Pre-fetch criteria + context once for this prompt so every
                # span in the fan-out opens zero DB sessions for reads.
                criteria_by_prompt: dict[str, dict[str, list[str]] | None] = {}
                criteria_by_prompt[prompt_id] = await ensure_prompt_has_criteria(
                    prompt_id
                )
                context_by_prompt = await _prefetch_prompt_contexts(spans_to_evaluate)

                # Fan out span evaluations concurrently (bounded by semaphore)
                semaphore = asyncio.Semaphore(_MAX_CONCURRENT_EVALUATIONS)

                async def _evaluate_with_limit(span: SpanModel) -> dict[str, Any]:
                    async with semaphore:
                        try:
                            return await _evaluate_span_correctness(
                                span=span,
                                criteria_by_prompt=criteria_by_prompt,
                                context_by_prompt=context_by_prompt,
                            )
                        except Exception as exc:
                            logger.exception(f"Failed to evaluate span {span.span_id}")
                            return {"span_id": span.span_id, "error": str(exc)}

                raw_results = await asyncio.gather(
                    *[_evaluate_with_limit(span) for span in spans_to_evaluate]
                )

                # Persist all scores/errors in a single DB session instead of one per span.
                results = await _batch_persist_evaluation_results(list(raw_results))

                evaluated_count = sum(
                    1 for r in results if "error" not in r and r.get("stored", True)
                )
                errors = [
                    f"Failed to evaluate span {r['span_id']}: {r['error']}"
                    for r in results
                    if "error" in r or not r.get("stored", True)
                ]

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


@shared_task(name="evaluations.evaluate_prompt_spans", bind=True)
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
