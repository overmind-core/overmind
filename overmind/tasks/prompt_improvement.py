"""
Task to automatically improve prompt templates based on span performance scores.
Runs daily and triggers improvements when specific span count thresholds are reached.
"""

import asyncio
import hashlib
import json
import logging
import uuid as uuid_module
from datetime import datetime, timedelta, timezone
from typing import Any

from celery import shared_task
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, func, cast, Float

from overmind.db.session import get_session_local
from overmind.models.prompts import Prompt
from overmind.models.iam.projects import Project
from overmind.models.suggestions import Suggestion as SuggestionModel
from overmind.models.traces import SpanModel
from overmind.models.jobs import Job
from overmind.api.v1.endpoints.jobs import JobType, JobStatus
from overmind.core.llms import call_llm, normalize_llm_response_output, try_json_parsing
from overmind.core.model_resolver import TaskType, resolve_model
from overmind.tasks.prompts import (
    SUGGESTION_GENERATION_SYSTEM_PROMPT,
    SUGGESTION_GENERATION_PROMPT,
    PROMPT_IMPROVEMENT_SYSTEM_PROMPT,
    PROMPT_IMPROVEMENT_PROMPT,
    TOOL_SUGGESTION_GENERATION_PROMPT,
    TOOL_PROMPT_IMPROVEMENT_PROMPT,
)
from overmind.tasks.task_lock import with_task_lock
from overmind.tasks.evaluations import (
    _format_criteria,
    _evaluate_correctness_with_llm,
)
from overmind.tasks.agentic_span_processor import (
    detect_agentic_span,
    preprocess_span_for_evaluation,
)

logger = logging.getLogger(__name__)

# Thresholds for prompt improvement: 50, 100, 200, 500, 1000, 2000, 3000...
INITIAL_THRESHOLDS = [50, 100, 200, 500, 1000]


def _get_tools_from_span(span: SpanModel) -> list[dict[str, Any]]:
    """Extract tool definitions pre-reconstructed at ingestion time."""
    return (span.metadata_attributes or {}).get("available_tools") or []


def _format_span_examples_text(spans: list[SpanModel], limit: int = 10) -> str:
    """
    Format a list of spans as a human-readable examples string for prompts.

    Includes score, input, output, and any agent feedback.
    """
    examples = []
    for i, span in enumerate(spans[:limit], 1):
        score = (
            span.feedback_score.get("correctness", 0.0) if span.feedback_score else 0.0
        )
        agent_fb = (span.feedback_score or {}).get("agent_feedback", {})
        agent_section = ""
        if isinstance(agent_fb, dict) and agent_fb.get("text"):
            r = agent_fb.get("rating", "unknown")
            agent_section = (
                f"\nUser feedback on Agent output (rating={r}): {agent_fb['text']}"
            )
        examples.append(
            f"\nExample {i} (score: {score:.2f}):\n"
            f"Input: {json.dumps(span.input or {}, indent=2)}\n"
            f"Output: {json.dumps(span.output or {}, indent=2)}"
            f"{agent_section}\n"
        )
    return "\n".join(examples) if examples else "None"


def calculate_next_threshold(current_count: int) -> int:
    """
    Returns the next threshold for prompt improvement.
    Sequence: 50, 100, 200, 500, 1000, 2000, 3000, 4000...

    Args:
        current_count: Current scored span count

    Returns:
        Next threshold value
    """
    for threshold in INITIAL_THRESHOLDS:
        if current_count < threshold:
            return threshold

    # After 1000, increment by 1000
    next_thousand = ((current_count // 1000) + 1) * 1000
    return next_thousand


def calculate_previous_last_count(last_improvement_span_count: int) -> int:
    """
    Returns the value to reset last_improvement_span_count to after scoring logic changes.

    Goes back one threshold step so that calculate_next_threshold(result) <=
    last_improvement_span_count, meaning prompt improvement will re-trigger as
    soon as the scoring job completes with the updated criteria.

    Sequence: 0 -> 50 -> 100 -> 200 -> 500 -> 1000 -> 2000 -> 3000 ...
    Example: last ran at 120 spans (crossed threshold 100) -> resets to 50
             so next threshold is 100, and 120 >= 100, so improvement re-triggers.
    """
    if last_improvement_span_count <= 0:
        return 0

    # Build the full threshold sequence up to (and including) last_improvement_span_count
    all_thresholds = [0] + INITIAL_THRESHOLDS[:]

    # Append 1000-increment thresholds for counts beyond the initial sequence
    t = INITIAL_THRESHOLDS[-1] + 1000
    while t <= last_improvement_span_count:
        all_thresholds.append(t)
        t += 1000

    # Collect all thresholds that are <= last_improvement_span_count
    applicable = [t for t in all_thresholds if t <= last_improvement_span_count]

    # Return second-to-last entry (one step back in the sequence)
    if len(applicable) < 2:
        return 0

    return applicable[-2]


def invalidate_prompt_improvement_metadata(prompt: Prompt) -> None:
    """
    Roll back last_improvement_span_count by one threshold step on the prompt object.

    Call this whenever evaluation criteria or agent description changes so that
    prompt improvement can re-trigger immediately using the updated scoring logic.
    The caller is responsible for committing the session after this call.

    The rollback happens at most once per improvement cycle. If the user edits
    criteria multiple times before improvement runs, only the first call takes
    effect — subsequent calls are no-ops because ``criteria_invalidated`` is
    already True. That flag is cleared whenever improvement actually runs
    (success, dedup, or identical-candidate), resetting the cycle.

    If no improvement has run yet (last_improvement_span_count == 0), this is a no-op.
    """
    metadata = dict(prompt.improvement_metadata or {})

    # Already rolled back in this cycle — don't keep decrementing.
    if metadata.get("criteria_invalidated"):
        return

    last_count = metadata.get("last_improvement_span_count", 0)
    new_count = calculate_previous_last_count(last_count)

    if new_count == last_count:
        return

    metadata["criteria_invalidated"] = True
    metadata["last_improvement_span_count"] = new_count
    # Reassigning the attribute (not mutating in-place) is enough for SQLAlchemy
    # to detect the change; no flag_modified needed.
    prompt.improvement_metadata = metadata

    logger.info(
        f"Invalidated improvement metadata for prompt '{prompt.slug}': "
        f"last_improvement_span_count {last_count} -> {new_count}"
    )


async def should_improve_prompt(prompt: Prompt, scored_span_count: int) -> bool:
    """
    Check if prompt should be improved based on threshold logic.

    Args:
        prompt: The Prompt model instance
        scored_span_count: Current count of scored spans

    Returns:
        True if threshold reached and improvement should run
    """
    if scored_span_count == 0:
        return False

    # Get last improvement count from metadata
    last_count = 0
    if prompt.improvement_metadata:
        metadata = prompt.improvement_metadata
        if isinstance(metadata, dict):
            last_count = metadata.get("last_improvement_span_count", 0)

    # Calculate next threshold based on last improvement
    next_threshold = calculate_next_threshold(last_count)

    # Check if we've crossed the threshold
    if scored_span_count >= next_threshold:
        logger.info(
            f"Threshold reached for prompt {prompt.prompt_id}: {scored_span_count} >= {next_threshold} (last: {last_count})"
        )
        return True

    return False


async def is_prompt_used_recently(prompt_id: str, session, days: int = 7) -> bool:
    """
    Check if a prompt has been used in the last N days.

    Args:
        prompt_id: The prompt_id to check
        session: Database session
        days: Number of days to look back (default 7)

    Returns:
        True if spans exist in the timeframe
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

    result = await session.execute(
        select(SpanModel.span_id)
        .where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.created_at >= cutoff_date,
                SpanModel.exclude_system_spans(),
            )
        )
        .limit(1)
    )

    return result.scalar_one_or_none() is not None


async def is_latest_prompt_adopted(
    prompt: Prompt, current_span_count: int, session, adoption_threshold: float = 0.25
) -> tuple[bool, dict[str, Any]]:
    """
    Check if the latest prompt version is being adopted by at least X% of new spans.

    Args:
        prompt: The Prompt model instance
        current_span_count: Current total scored span count
        session: Database session
        adoption_threshold: Minimum adoption rate (default 0.25 = 25%)

    Returns:
        Tuple of (is_adopted, stats_dict)
    """
    # Get last improvement count from metadata
    last_count = 0
    if prompt.improvement_metadata:
        metadata = prompt.improvement_metadata
        if isinstance(metadata, dict):
            last_count = metadata.get("last_improvement_span_count", 0)

    # Calculate new spans since last improvement
    new_spans_count = current_span_count - last_count

    # If no new spans, can't determine adoption
    if new_spans_count <= 0:
        logger.info(
            f"No new spans since last improvement for prompt {prompt.prompt_id}"
        )
        return False, {
            "new_spans_count": 0,
            "spans_using_latest": 0,
            "adoption_rate": 0.0,
        }

    # Get the base prompt_id pattern (project_id and slug, any version)
    latest_prompt_id = prompt.prompt_id  # This is the latest version

    # Count spans created after last improvement that use the latest version
    # We need to find the timestamp of the Nth scored span (where N = last_count)
    # For simplicity, we'll count all spans with the latest prompt_id
    # Exclude system-generated spans (prompt tuning, backtesting)
    result = await session.execute(
        select(func.count(SpanModel.span_id)).where(
            and_(
                SpanModel.prompt_id == latest_prompt_id,
                SpanModel.feedback_score.has_key("correctness"),
                SpanModel.exclude_system_spans(),
            )
        )
    )
    spans_with_latest = result.scalar() or 0

    # Calculate adoption rate
    # Note: This counts ALL spans with the latest version, not just new ones
    # A more accurate approach would track the timestamp of the last improvement
    # and count spans created after that timestamp
    adoption_rate = (
        spans_with_latest / current_span_count if current_span_count > 0 else 0.0
    )

    # Estimate adoption among new spans (conservative approach)
    # If total adoption is high, new spans likely also use latest
    is_adopted = adoption_rate >= adoption_threshold

    stats = {
        "new_spans_count": new_spans_count,
        "spans_using_latest": spans_with_latest,
        "total_span_count": current_span_count,
        "adoption_rate": adoption_rate,
        "threshold": adoption_threshold,
    }

    logger.info(
        f"Adoption check for prompt {prompt.prompt_id}: {adoption_rate * 100:.1f}% ({spans_with_latest}/{current_span_count} spans), threshold: {adoption_threshold * 100:.1f}%"
    )

    return is_adopted, stats


async def fetch_spans_by_score_buckets(
    prompt_id: str, session, per_bucket: int = 15
) -> dict[str, list[SpanModel]]:
    """
    Fetch latest spans from each score bucket.

    Buckets:
    - poor: [0.0-0.2]
    - below_average: [0.2-0.4]
    - average: [0.4-0.6]
    - good: [0.6-0.8]
    - excellent: [0.8-1.0]

    Args:
        prompt_id: The prompt_id to fetch spans for
        session: Database session
        per_bucket: Number of spans to fetch per bucket (default 15)

    Returns:
        Dict mapping bucket names to lists of SpanModels
    """
    buckets = {
        "poor": (0.0, 0.2),
        "below_average": (0.2, 0.4),
        "average": (0.4, 0.6),
        "good": (0.6, 0.8),
        "excellent": (0.8, 1.0),
    }

    results = {}

    for bucket_name, (lower, upper) in buckets.items():
        # For the highest bucket, include 1.0
        if upper == 1.0:
            result = await session.execute(
                select(SpanModel)
                .where(
                    and_(
                        SpanModel.prompt_id == prompt_id,
                        SpanModel.feedback_score.has_key("correctness"),
                        cast(SpanModel.feedback_score["correctness"], Float) >= lower,
                        cast(SpanModel.feedback_score["correctness"], Float) <= upper,
                        SpanModel.exclude_system_spans(),
                    )
                )
                .order_by(SpanModel.created_at.desc())
                .limit(per_bucket)
            )
        else:
            result = await session.execute(
                select(SpanModel)
                .where(
                    and_(
                        SpanModel.prompt_id == prompt_id,
                        SpanModel.feedback_score.has_key("correctness"),
                        cast(SpanModel.feedback_score["correctness"], Float) >= lower,
                        cast(SpanModel.feedback_score["correctness"], Float) < upper,
                        SpanModel.exclude_system_spans(),
                    )
                )
                .order_by(SpanModel.created_at.desc())
                .limit(per_bucket)
            )

        spans = result.scalars().all()
        results[bucket_name] = list(spans)
        logger.info(
            f"Fetched {len(spans)} spans for bucket '{bucket_name}' [{lower:.1f}-{upper:.1f}]"
        )

    return results


# Pydantic response models


class SuggestionResponse(BaseModel):
    suggestions: list[str] = Field(description="List of improvement suggestions")


async def generate_improvement_suggestions(
    poor_spans: list[SpanModel],
    prompt: Prompt,
    project_description: str = "",
    agent_description: str = "",
) -> list[str]:
    """
    Use Claude Sonnet 4.5 to analyze poor performing spans and generate suggestions.

    Routes to the tool-calling suggestion prompt when spans carry response_type
    metadata (new-style tool-calling spans).  Falls back to the existing agentic /
    plain suggestion prompt for legacy spans.

    Args:
        poor_spans: List of spans with score < 0.5
        prompt: The current Prompt model
        project_description: Description of the project for context
        agent_description: Description of the agent for context

    Returns:
        List of suggestion strings
    """
    if not poor_spans:
        logger.info("No poor performing spans to analyze")
        return []

    # Check whether any span uses the new response_type convention
    has_response_type = any(
        (span.metadata_attributes or {}).get("response_type")
        for span in poor_spans[:10]
    )

    if has_response_type:
        return await _generate_tool_improvement_suggestions(poor_spans, prompt)

    # -----------------------------------------------------------------------
    # Legacy path: existing agentic / plain suggestion generation
    # -----------------------------------------------------------------------
    has_agentic_spans = False
    tool_usage_issues = []

    for span in poor_spans[:10]:
        is_agentic = detect_agentic_span(
            input_data=span.input or {},
            output_data=span.output or {},
            metadata=span.metadata_attributes or {},
        )
        if is_agentic:
            has_agentic_spans = True
            processed = preprocess_span_for_evaluation(
                input_data=span.input or {},
                output_data=span.output or {},
                metadata=span.metadata_attributes or {},
            )
            tool_count = processed["metadata"]["tool_calls_count"]
            if tool_count > 0:
                tool_usage_issues.append(
                    {
                        "span_id": span.span_id,
                        "tool_calls": tool_count,
                        "score": span.feedback_score.get("correctness", 0.0)
                        if span.feedback_score
                        else 0.0,
                    }
                )

    poor_examples_text = _format_span_examples_text(poor_spans)

    tool_usage_analysis = ""
    if has_agentic_spans and tool_usage_issues:
        avg_tool_calls = sum(t["tool_calls"] for t in tool_usage_issues) / len(
            tool_usage_issues
        )
        tool_usage_analysis = f"""
<ToolUsageAnalysis>
Detected agentic behavior with tool calls in poor-performing examples.
- {len(tool_usage_issues)} spans with tool usage
- Average {avg_tool_calls:.1f} tool calls per span
- Consider whether the prompt provides adequate guidance for:
  1. When to use which tools
  2. How to interpret tool results
  3. How to synthesize information from multiple tool calls
  4. Tool selection strategy
Note: If tool definitions exist in the prompt, preserve them while improving instructions.
</ToolUsageAnalysis>
"""

    prompt_text = SUGGESTION_GENERATION_PROMPT.format(
        project_description=project_description,
        agent_description=agent_description,
        current_prompt=prompt.prompt,
        poor_examples=poor_examples_text,
        tool_usage_analysis=tool_usage_analysis,
    )

    response, _ = call_llm(
        prompt_text,
        system_prompt=SUGGESTION_GENERATION_SYSTEM_PROMPT,
        model=resolve_model(TaskType.PROMPT_TUNING),
        response_format=SuggestionResponse,
    )

    parsed = try_json_parsing(response)
    suggestions = parsed.get("suggestions", [])

    if not isinstance(suggestions, list):
        logger.warning("Invalid suggestions format received from LLM")
        return []

    logger.info(
        f"Generated {len(suggestions)} improvement suggestions "
        f"(agentic_behavior={has_agentic_spans})"
    )
    return suggestions


async def _generate_tool_improvement_suggestions(
    poor_spans: list[SpanModel], prompt: Prompt
) -> list[str]:
    """
    Generate improvement suggestions for tool-calling agent prompts.

    Splits poor spans into tool-call spans and text spans so the improvement
    LLM can separately identify tool-selection failures vs answer-synthesis failures.
    Tool definitions are passed as read-only context.
    """
    tool_call_spans = [
        s
        for s in poor_spans[:10]
        if (s.metadata_attributes or {}).get("response_type") == "tool_calls"
    ]
    text_spans = [
        s
        for s in poor_spans[:10]
        if (s.metadata_attributes or {}).get("response_type") == "text"
    ]

    # Extract tool definitions from the first available tool-call span
    tool_definitions_json = "No tool definitions available"
    for span in tool_call_spans:
        tools = _get_tools_from_span(span)
        if tools:
            tool_definitions_json = json.dumps(tools, indent=2)
            break

    poor_tool_call_examples = _format_span_examples_text(tool_call_spans)
    poor_text_examples = _format_span_examples_text(text_spans)

    prompt_text = TOOL_SUGGESTION_GENERATION_PROMPT.format(
        current_prompt=prompt.prompt,
        tool_definitions=tool_definitions_json,
        poor_tool_call_examples=poor_tool_call_examples,
        poor_text_examples=poor_text_examples,
    )

    response, _ = call_llm(
        prompt_text,
        system_prompt=SUGGESTION_GENERATION_SYSTEM_PROMPT,
        model=resolve_model(TaskType.PROMPT_TUNING),
        response_format=SuggestionResponse,
    )

    parsed = try_json_parsing(response)
    suggestions = parsed.get("suggestions", [])

    if not isinstance(suggestions, list):
        logger.warning("Invalid tool suggestions format received from LLM")
        return []

    logger.info(
        f"Generated {len(suggestions)} tool-calling improvement suggestions "
        f"(tool_call_spans={len(tool_call_spans)}, text_spans={len(text_spans)})"
    )
    return suggestions


async def improve_prompt_template(
    current_prompt: Prompt,
    suggestions: list[str],
    span_examples: dict[str, list[SpanModel]],
    project_description: str = "",
    agent_description: str = "",
) -> str:
    """
    Use Claude Sonnet 4.5 to create an improved prompt template.

    Routes to the tool-calling improvement prompt when span_examples contain
    response_type metadata (new-style tool-calling spans).  Falls back to
    the existing improvement prompt for legacy spans.

    Args:
        current_prompt: The current Prompt model
        suggestions: List of improvement suggestions
        span_examples: Dict of spans by bucket
        project_description: Description of the project for context
        agent_description: Description of the agent for context

    Returns:
        The new improved prompt string
    """
    all_spans: list[SpanModel] = [
        s for bucket in span_examples.values() for s in bucket
    ]

    has_response_type = any(
        (s.metadata_attributes or {}).get("response_type") for s in all_spans
    )

    if has_response_type:
        return await _improve_tool_prompt_template(
            current_prompt, suggestions, span_examples
        )

    # -----------------------------------------------------------------------
    # Legacy path: existing improvement logic
    # -----------------------------------------------------------------------
    good_spans = span_examples.get("excellent", []) + span_examples.get("good", [])
    good_examples_text = (
        _format_span_examples_text(good_spans) or "No good examples available"
    )

    poor_spans = span_examples.get("poor", []) + span_examples.get("below_average", [])
    poor_examples_text = (
        _format_span_examples_text(poor_spans) or "No poor examples available"
    )

    suggestions_text = (
        "\n".join(f"- {s}" for s in suggestions)
        if suggestions
        else "No specific suggestions available - focus on improving clarity and specificity"
    )

    # Generate improved prompt
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
    prompt_text = PROMPT_IMPROVEMENT_PROMPT.format(
        project_context=project_context,
        agent_context=agent_context,
        current_prompt=current_prompt.prompt,
        suggestions=suggestions_text,
        good_examples=good_examples_text,
        poor_examples=poor_examples_text,
    )

    response, _ = call_llm(
        prompt_text,
        system_prompt=PROMPT_IMPROVEMENT_SYSTEM_PROMPT,
        model=resolve_model(TaskType.PROMPT_TUNING),
    )

    improved_prompt = response.strip()
    logger.info(f"Generated improved prompt ({len(improved_prompt)} chars)")
    return improved_prompt


async def _improve_tool_prompt_template(
    current_prompt: Prompt,
    suggestions: list[str],
    span_examples: dict[str, list[SpanModel]],
) -> str:
    """
    Generate an improved prompt template for tool-calling agent prompts.

    Splits good and poor spans by response_type and passes them to
    TOOL_PROMPT_IMPROVEMENT_PROMPT.  Tool definitions are read-only context.
    """
    good_spans = span_examples.get("excellent", []) + span_examples.get("good", [])
    poor_spans = span_examples.get("poor", []) + span_examples.get("below_average", [])

    good_tool_call_spans = [
        s
        for s in good_spans
        if (s.metadata_attributes or {}).get("response_type") == "tool_calls"
    ]
    good_text_spans = [
        s
        for s in good_spans
        if (s.metadata_attributes or {}).get("response_type") == "text"
    ]
    poor_tool_call_spans = [
        s
        for s in poor_spans
        if (s.metadata_attributes or {}).get("response_type") == "tool_calls"
    ]
    poor_text_spans = [
        s
        for s in poor_spans
        if (s.metadata_attributes or {}).get("response_type") == "text"
    ]

    # Extract tool definitions from any available tool-call span
    tool_definitions_json = "No tool definitions available"
    for span in good_tool_call_spans + poor_tool_call_spans:
        tools = _get_tools_from_span(span)
        if tools:
            tool_definitions_json = json.dumps(tools, indent=2)
            break

    suggestions_text = (
        "\n".join(f"- {s}" for s in suggestions)
        if suggestions
        else "No specific suggestions available - focus on improving clarity and specificity"
    )

    prompt_text = TOOL_PROMPT_IMPROVEMENT_PROMPT.format(
        current_prompt=current_prompt.prompt,
        tool_definitions=tool_definitions_json,
        suggestions=suggestions_text,
        good_tool_call_examples=_format_span_examples_text(good_tool_call_spans)
        or "None",
        good_text_examples=_format_span_examples_text(good_text_spans) or "None",
        poor_tool_call_examples=_format_span_examples_text(poor_tool_call_spans)
        or "None",
        poor_text_examples=_format_span_examples_text(poor_text_spans) or "None",
    )

    response, _ = call_llm(
        prompt_text,
        system_prompt=PROMPT_IMPROVEMENT_SYSTEM_PROMPT,
        model=resolve_model(TaskType.PROMPT_TUNING),
    )

    improved_prompt = response.strip()
    logger.info(f"Generated tool-aware improved prompt ({len(improved_prompt)} chars)")
    return improved_prompt


async def create_prompt_version(
    base_prompt: Prompt,
    new_prompt_string: str,
    span_count: int,
    spans_used: int,
    session,
) -> Prompt:
    """
    Create a new version of the prompt with improvement metadata.

    Args:
        base_prompt: The base Prompt to version from
        new_prompt_string: The improved prompt text
        span_count: Total scored span count at improvement time
        spans_used: Number of spans used for improvement
        session: Database session

    Returns:
        The newly created Prompt instance
    """
    # Calculate hash
    prompt_hash = hashlib.sha256(new_prompt_string.encode()).hexdigest()

    # Check if this exact prompt already exists (deduplication)
    existing = await session.execute(
        select(Prompt)
        .where(
            and_(
                Prompt.project_id == base_prompt.project_id,
                Prompt.slug == base_prompt.slug,
                Prompt.hash == prompt_hash,
            )
        )
        .order_by(Prompt.version.desc())
        .limit(1)
    )
    existing_prompt = existing.scalar_one_or_none()

    if existing_prompt:
        logger.info(
            f"Improved prompt is identical to existing version {existing_prompt.version}, skipping creation"
        )
        return existing_prompt

    # Get max version for this slug and project
    max_version_result = await session.execute(
        select(func.max(Prompt.version)).where(
            and_(
                Prompt.project_id == base_prompt.project_id,
                Prompt.slug == base_prompt.slug,
            )
        )
    )
    max_version = max_version_result.scalar() or 0
    new_version = max_version + 1

    # Build improvement history
    improvement_entry = {
        "span_count": span_count,
        "new_version": new_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "spans_used": spans_used,
    }

    # Get existing history from base prompt
    history = []
    if base_prompt.improvement_metadata and isinstance(
        base_prompt.improvement_metadata, dict
    ):
        existing_history = base_prompt.improvement_metadata.get(
            "improvement_history", []
        )
        if isinstance(existing_history, list):
            history = existing_history

    history.append(improvement_entry)

    improvement_metadata = {
        "last_improvement_span_count": span_count,
        "improvement_history": history,
    }

    # Create new prompt version
    # Copy evaluation criteria from base prompt (don't recreate it)
    evaluation_criteria = base_prompt.evaluation_criteria

    new_prompt = Prompt(
        slug=base_prompt.slug,
        hash=prompt_hash,
        prompt=new_prompt_string,
        project_id=base_prompt.project_id,
        user_id=base_prompt.user_id,
        version=new_version,
        display_name=base_prompt.display_name,
        evaluation_criteria=evaluation_criteria,
        improvement_metadata=improvement_metadata,
        tags=base_prompt.tags,
        is_active=False,  # tuning-created versions await acceptance
    )

    session.add(new_prompt)
    await session.commit()
    await session.refresh(new_prompt)

    logger.info(
        f"Created new prompt version {new_version} for slug='{base_prompt.slug}' project={base_prompt.project_id}"
    )

    return new_prompt


async def generate_outputs_with_new_prompt(
    new_prompt: Any,
    old_spans: list[SpanModel],
) -> list[dict[str, Any]]:
    """
    Generate new outputs using a prompt template with inputs from old spans.

    For each span the function:
    1. Formats the new prompt template with the span's template variables
       (everything in input_params except the ``tools`` key).
    2. Reconstructs the full message list from the original span input,
       replacing only the system message with the new formatted prompt so
       the entire conversation context (user turns, tool calls, tool results)
       is preserved.
    3. Passes the original tool definitions (from input_params[\"tools\"]) to
       the model so tool-calling spans can be replayed correctly.
    4. Handles model responses that contain tool calls instead of plain text
       by serialising them to JSON.

    Args:
        new_prompt: Any object with a ``.prompt`` attribute containing the
            prompt template text (``Prompt`` model or lightweight stand-in).
        old_spans: List of spans from old prompt to use inputs from

    Returns:
        List of dicts with new output, latency, cost, and old_span_id
    """
    from overmind.tasks.agentic_span_processor import _safe_parse_json

    results = []

    for old_span in old_spans:
        try:
            # Get the model used by the old span
            model = old_span.metadata_attributes["gen_ai.response.model"]

            # Parse input_params (may be a JSON string or already a dict)
            raw_params = old_span.input_params or {}
            parsed_params = (
                _safe_parse_json(raw_params)
                if not isinstance(raw_params, dict)
                else raw_params
            )

            tools = (old_span.metadata_attributes or {}).get("available_tools") or []
            input_variables = (
                {k: v for k, v in parsed_params.items() if k != "tools"}
                if isinstance(parsed_params, dict)
                else {}
            )

            # Format the new prompt template with the template variables
            formatted_new_prompt = new_prompt.prompt
            try:
                if input_variables:
                    formatted_new_prompt = new_prompt.prompt.format(**input_variables)
            except KeyError as e:
                logger.warning(
                    f"Could not format prompt with input variables for span "
                    f"{old_span.span_id}: missing key {e}"
                )

            # Reconstruct the message list from the original span input.
            # Only the system message is replaced with the new prompt; every
            # other turn (user, assistant tool-call, tool result) is kept as-is
            # so the model receives the same conversation context.
            span_input = _safe_parse_json(old_span.input)
            if isinstance(span_input, list) and span_input:
                messages: list[dict[str, Any]] = []
                system_replaced = False
                for msg in span_input:
                    if (
                        isinstance(msg, dict)
                        and msg.get("role") == "system"
                        and not system_replaced
                    ):
                        messages.append(
                            {"role": "system", "content": formatted_new_prompt}
                        )
                        system_replaced = True
                    else:
                        messages.append(msg)
                # If there was no system message, prepend the new prompt as one
                if not system_replaced:
                    messages.insert(
                        0, {"role": "system", "content": formatted_new_prompt}
                    )
                call_messages = messages
            else:
                # Fallback for non-list inputs: send as a plain user message
                call_messages = None

            response, stats = call_llm(
                input_text=formatted_new_prompt,  # used only when call_messages is None
                system_prompt=None,
                model=model,
                messages=call_messages,
                tools=tools if tools else None,
            )

            response = normalize_llm_response_output(response)

            results.append(
                {
                    "old_span_id": old_span.span_id,
                    "input": old_span.input or {},
                    "input_params": old_span.input_params or {},
                    "metadata": old_span.metadata_attributes or {},
                    "output": response,
                    "latency_ms": stats["response_ms"],
                    "cost": stats["response_cost"],
                    "prompt_tokens": stats["prompt_tokens"],
                    "completion_tokens": stats["completion_tokens"],
                    "old_span_trace_id": old_span.trace_id,
                    "model": model,
                    "available_tools": tools,
                }
            )

            logger.info(
                f"Generated output for old span {old_span.span_id} using model {model}: "
                f"latency={stats['response_ms']}ms, cost=${stats['response_cost']:.6f}"
            )

        except Exception as exc:
            logger.error(
                f"Failed to generate output for span {old_span.span_id}: {exc}"
            )
            results.append({"old_span_id": old_span.span_id, "error": str(exc)})

    return results


async def create_comparison_spans(
    new_prompt: Prompt,
    generation_results: list[dict[str, Any]],
    session,
    project_description: str | None = None,
    agent_description: str | None = None,
) -> list[SpanModel]:
    """
    Create new spans with the generated outputs using pre-computed or freshly evaluated scores.

    If a result dict contains a ``correctness_score`` key the value is reused
    directly; otherwise the score is evaluated via LLM (requires the prompt to
    carry ``evaluation_criteria``).

    Args:
        new_prompt: The newly created Prompt
        generation_results: Results from generate_outputs_with_new_prompt
            (may include ``correctness_score`` for pre-computed values)
        session: Database session

    Returns:
        List of newly created SpanModels
    """
    import uuid

    # Determine whether we need LLM-based evaluation for any result
    needs_evaluation = any(
        "correctness_score" not in r for r in generation_results if "error" not in r
    )

    criteria_text = None
    if needs_evaluation:
        if (
            not new_prompt.evaluation_criteria
            or "correctness" not in new_prompt.evaluation_criteria
        ):
            logger.warning(
                "New prompt has no evaluation criteria, skipping span creation"
            )
            return []
        criteria_rules = new_prompt.evaluation_criteria["correctness"]
        criteria_text = _format_criteria(criteria_rules)

    new_spans = []

    for result in generation_results:
        if "error" in result:
            continue

        try:
            # Use pre-computed score when available, otherwise evaluate via LLM
            correctness_value = result.get("correctness_score")
            if correctness_value is None:
                correctness_value = _evaluate_correctness_with_llm(
                    input_data=result["input"],
                    output_data=result["output"],
                    criteria_text=criteria_text,
                    project_description=project_description,
                    agent_description=agent_description,
                    span_metadata=result.get("metadata", {}),
                )

            # Create new span
            new_span_id = str(uuid.uuid4())

            # Calculate unix nano timestamps
            now = datetime.now(timezone.utc)
            start_time_nano = int(now.timestamp() * 1_000_000_000)
            end_time_nano = start_time_nano + (result["latency_ms"] * 1_000_000)

            new_span = SpanModel(
                span_id=new_span_id,
                operation="prompt_tuning",  # System-generated span
                start_time_unix_nano=start_time_nano,
                end_time_unix_nano=end_time_nano,
                input=result["input"],
                output=result["output"],
                input_params=result.get("input_params", {}),
                output_params={},
                status_code=1,  # OK status
                metadata_attributes={
                    "old_span_id": result["old_span_id"],
                    "prompt_improvement_test": True,
                    "new_prompt_id": new_prompt.prompt_id,
                    "cost": result["cost"],
                    "prompt_tokens": result.get("prompt_tokens", 0),
                    "completion_tokens": result.get("completion_tokens", 0),
                    "latency_ms": result.get("latency_ms", 0),
                    "gen_ai.response.model": result.get("model", "gpt-5-mini"),
                    "available_tools": result.get("available_tools", []),
                },
                feedback_score={"correctness": correctness_value},
                trace_id=result["old_span_trace_id"],
                prompt_id=new_prompt.prompt_id,
            )

            session.add(new_span)
            new_spans.append(new_span)

            logger.info(
                f"Created comparison span {new_span_id} (old: {result['old_span_id']}, score: {correctness_value:.2f})"
            )

        except Exception as exc:
            logger.error(
                f"Failed to create span for old span {result.get('old_span_id')}: {exc}"
            )

    # Commit all new spans
    await session.commit()

    logger.info(f"Created {len(new_spans)} comparison spans with scores")
    return new_spans


async def calculate_comparison_metrics(
    old_spans: list[SpanModel], new_spans: list[SpanModel]
) -> dict[str, Any]:
    """
    Calculate comparison metrics between old and new prompts.

    Args:
        old_spans: Original spans with old prompt
        new_spans: New spans with new prompt

    Returns:
        Dict with comparison metrics
    """
    # Calculate average scores
    old_scores = [
        span.feedback_score.get("correctness", 0.0)
        for span in old_spans
        if span.feedback_score
    ]
    new_scores = [
        span.feedback_score.get("correctness", 0.0)
        for span in new_spans
        if span.feedback_score
    ]

    avg_old_score = sum(old_scores) / len(old_scores) if old_scores else 0.0
    avg_new_score = sum(new_scores) / len(new_scores) if new_scores else 0.0

    total_old_cost = sum(
        (span.metadata_attributes or {}).get("cost", 0.0) for span in old_spans
    )
    total_new_cost = sum(
        (span.metadata_attributes or {}).get("cost", 0.0) for span in new_spans
    )
    # Old spans are collected user spans — latency comes from timestamps.
    # New spans are synthetic tuning spans — latency is in metadata_attributes.
    avg_old_latency = (
        sum(
            (span.end_time_unix_nano - span.start_time_unix_nano) / 1_000_000
            for span in old_spans
        )
        / len(old_spans)
        if old_spans
        else 0.0
    )
    avg_new_latency = (
        sum((span.metadata_attributes or {}).get("latency_ms", 0) for span in new_spans)
        / len(new_spans)
        if new_spans
        else 0.0
    )

    cost_delta = total_new_cost - total_old_cost
    cost_delta_pct = (cost_delta / total_old_cost * 100) if total_old_cost > 0 else 0.0
    latency_delta_ms = avg_new_latency - avg_old_latency
    latency_delta_pct = (
        (latency_delta_ms / avg_old_latency * 100) if avg_old_latency > 0 else 0.0
    )

    comparison = {
        "old_prompt": {
            "avg_score": avg_old_score,
            "span_count": len(old_spans),
            "total_cost": total_old_cost,
            "avg_latency_ms": avg_old_latency,
        },
        "new_prompt": {
            "avg_score": avg_new_score,
            "span_count": len(new_spans),
            "total_cost": total_new_cost,
            "avg_latency_ms": avg_new_latency,
        },
        "improvement": {
            "score_delta": avg_new_score - avg_old_score,
            "score_delta_pct": ((avg_new_score - avg_old_score) / avg_old_score * 100)
            if avg_old_score > 0
            else 0.0,
            "cost_delta": cost_delta,
            "cost_delta_pct": cost_delta_pct,
            "latency_delta_ms": latency_delta_ms,
            "latency_delta_pct": latency_delta_pct,
        },
    }

    logger.info(
        f"Comparison metrics - Score: {avg_old_score:.2f} -> {avg_new_score:.2f} ({comparison['improvement']['score_delta_pct']:.1f}%)"
        f" | Cost: ${total_old_cost:.6f} -> ${total_new_cost:.6f} (Δ {cost_delta_pct:+.2f}%)"
        f" | Latency: {avg_old_latency:.0f}ms -> {avg_new_latency:.0f}ms (Δ {latency_delta_pct:+.2f}%)"
    )

    return comparison


async def validate_prompt_tuning_eligibility(
    prompt: Prompt, session
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """
    Validate if a prompt is eligible for prompt tuning.

    Used by both user-triggered (API) and system-triggered (Celery beat) paths
    before creating a job record, so that all eligibility logic lives in one place.

    Checks (in order):
        1. Prompt used recently (last 7 days)
        2. Minimum scored span count
        3. Improvement threshold reached
        4. Latest prompt version adoption >= 25%
        5. No existing PENDING/RUNNING prompt tuning job
        6. Spans available for analysis
        7. Evaluation criteria with correctness defined

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
    stats = {}

    # Check 1: Prompt used recently (last 7 days)
    if not await is_prompt_used_recently(prompt_id, session):
        return (
            False,
            "This prompt hasn't had any traffic in the past 7 days. It needs to be actively used before tuning can run.",
            stats,
        )

    # Check 2: Count scored spans (exclude system-generated spans)
    count_result = await session.execute(
        select(func.count(SpanModel.span_id)).where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.feedback_score.has_key("correctness"),
                SpanModel.exclude_system_spans(),
            )
        )
    )
    scored_count = count_result.scalar() or 0
    stats["scored_count"] = scored_count

    # Check 3: Threshold reached
    if not await should_improve_prompt(prompt, scored_count):
        return (
            False,
            "More evaluated requests are needed before tuning can run. Continue using your application — tuning will start automatically when ready.",
            stats,
        )

    # Check 4: Latest version adopted (≥25%)
    is_adopted, adoption_stats = await is_latest_prompt_adopted(
        prompt, scored_count, session
    )
    stats["adoption_stats"] = adoption_stats

    if not is_adopted:
        return (
            False,
            "The latest prompt version hasn't been widely adopted yet. Make sure your application is using the most recent version.",
            stats,
        )

    # Check 5: No existing PENDING/RUNNING job
    existing_job_check = await session.execute(
        select(Job).where(
            and_(
                Job.project_id == prompt.project_id,
                Job.prompt_slug == prompt.slug,
                Job.job_type == JobType.PROMPT_TUNING.value,
                Job.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
            )
        )
    )
    existing_job = existing_job_check.scalar_one_or_none()

    if existing_job:
        return (
            False,
            "A tuning job is already in progress. Please wait for it to finish.",
            stats,
        )

    # Check 6: Spans available for analysis
    span_buckets = await fetch_spans_by_score_buckets(prompt_id, session, per_bucket=15)
    total_spans_fetched = sum(len(spans) for spans in span_buckets.values())
    stats["total_spans_available"] = total_spans_fetched

    if total_spans_fetched == 0:
        return (
            False,
            "Not enough request data is available for analysis yet. Keep using your application and try again later.",
            stats,
        )

    # Check 7: Evaluation criteria exists
    if (
        not prompt.evaluation_criteria
        or "correctness" not in prompt.evaluation_criteria
    ):
        return (
            False,
            "Evaluation criteria haven't been configured yet. Please set up scoring rules before running tuning.",
            stats,
        )

    # All checks passed!
    logger.info(
        f"Prompt {prompt_id} is eligible for tuning: {scored_count} scored spans, "
        f"{total_spans_fetched} spans available for analysis"
    )
    return True, None, stats


async def _check_and_create_prompt_improvement_job(
    prompt: Prompt, session
) -> dict[str, Any] | None:
    """
    Validate a prompt's eligibility for improvement and create a PENDING job if eligible.

    Used by the system-triggered (Celery beat) path only. The user-triggered (API)
    path calls ``validate_prompt_tuning_eligibility`` directly and creates the job
    itself. Both paths share the same eligibility logic through that function.

    Args:
        prompt: The Prompt to check
        session: Database session

    Returns:
        Dict with check results, or None if the prompt is not eligible / was skipped
    """
    prompt_id = prompt.prompt_id

    (
        is_eligible,
        error_message,
        validation_stats,
    ) = await validate_prompt_tuning_eligibility(prompt, session)

    if not is_eligible:
        # Surface low-adoption skips distinctly so the caller can count them.
        if error_message and "widely adopted" in error_message:
            adoption_stats = (
                validation_stats.get("adoption_stats", {}) if validation_stats else {}
            )
            logger.info(
                f"Latest prompt version not sufficiently adopted for {prompt_id}: "
                f"{adoption_stats.get('adoption_rate', 0) * 100:.1f}% < 25%, skipping improvement"
            )
            return {
                "prompt_id": prompt_id,
                "status": "skipped_low_adoption",
                "scored_count": validation_stats.get("scored_count")
                if validation_stats
                else None,
                "adoption_stats": adoption_stats,
            }

        # Surface pre-existing job skips distinctly so the caller can count them.
        if error_message and "already" in error_message:
            logger.info(f"Job already in progress for prompt {prompt_id}, skipping")
            return {
                "prompt_id": prompt_id,
                "status": "job_already_exists",
            }

        logger.info(
            f"Prompt {prompt_id} not eligible for improvement, skipping: {error_message}"
        )
        return None

    # All eligibility checks passed — create a PENDING job entry.
    scored_count = validation_stats.get("scored_count") if validation_stats else None
    try:
        job = Job(
            job_id=uuid_module.uuid4(),
            job_type=JobType.PROMPT_TUNING.value,
            project_id=prompt.project_id,
            prompt_slug=prompt.slug,
            status=JobStatus.PENDING.value,
            result={
                "parameters": {"prompt_id": prompt_id},
                "validation_stats": validation_stats,
            },
            triggered_by_user_id=None,  # Auto-triggered by system
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        logger.info(
            f"Created PENDING job entry for prompt_tuning: {prompt_id}, job_id: {job.job_id}"
        )
        return {
            "prompt_id": prompt_id,
            "status": "job_created",
            "job_id": str(job.job_id),
            "scored_count": scored_count,
        }
    except Exception as e:
        logger.error(f"Failed to create job entry: {e}")
        return None


async def _execute_prompt_improvement(
    prompt_id: str, job_id: str, session
) -> dict[str, Any]:
    """
    Execute the actual prompt improvement work for a job.

    Flow
    ----
    1. Generate an improved prompt template (candidate text).
    2. Select comparison spans across score buckets (max 50).
    3. Generate new outputs using the candidate text & score them.
    4. Compare metrics between old and candidate outputs.
    5. **Always** create comparison spans (with ``operation="prompt_tuning"``
       and ``prompt_improvement_test`` metadata) so that every test run
       leaves an auditable record regardless of outcome.
    6. **Only if at least one metric improves**: persist a new prompt
       version and create a ``Suggestion`` record visible to the user.
    7. If no metric improved the new version is discarded but the
       comparison spans are retained.

    Args:
        prompt_id: The prompt ID to improve
        job_id: The job ID tracking this work
        session: Database session

    Returns:
        Dict with improvement results
    """
    # ------------------------------------------------------------------
    # Setup: load job & prompt
    # ------------------------------------------------------------------
    job_uuid = uuid_module.UUID(job_id)
    job_result = await session.execute(select(Job).where(Job.job_id == job_uuid))
    job = job_result.scalar_one_or_none()

    if not job:
        raise ValueError(f"Job {job_id} not found")

    project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
    prompt_result = await session.execute(
        select(Prompt).where(
            and_(
                Prompt.project_id == uuid_module.UUID(project_id_str),
                Prompt.version == version,
                Prompt.slug == slug,
            )
        )
    )
    prompt = prompt_result.scalar_one_or_none()

    if not prompt:
        raise ValueError(f"Prompt {prompt_id} not found")

    project_result = await session.execute(
        select(Project).where(Project.project_id == uuid_module.UUID(project_id_str))
    )
    project = project_result.scalar_one_or_none()
    project_description = project.description if project else ""
    agent_description = (prompt.agent_description or {}).get("description", "")

    count_result = await session.execute(
        select(func.count(SpanModel.span_id)).where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.feedback_score.has_key("correctness"),
                SpanModel.exclude_system_spans(),
            )
        )
    )
    scored_count = count_result.scalar() or 0

    logger.info(f"Improving prompt {prompt_id} (scored spans: {scored_count})")

    try:
        # --------------------------------------------------------------
        # 1. Fetch spans & generate candidate prompt text
        # --------------------------------------------------------------
        span_buckets = await fetch_spans_by_score_buckets(prompt_id, session)
        total_spans_fetched = sum(len(v) for v in span_buckets.values())

        poor_spans = span_buckets.get("poor", []) + span_buckets.get(
            "below_average", []
        )

        suggestions = []
        if poor_spans:
            suggestions = await generate_improvement_suggestions(
                poor_spans, prompt, project_description, agent_description
            )

        improved_prompt_string = await improve_prompt_template(
            prompt, suggestions, span_buckets, project_description, agent_description
        )

        # Quick deduplication check (hash comparison, no DB write yet)
        candidate_hash = hashlib.sha256(improved_prompt_string.encode()).hexdigest()
        if candidate_hash == prompt.hash:
            logger.info("Candidate prompt identical to current version, skipping")
            # Advance the threshold so we don't immediately re-trigger at the same count.
            # Clear criteria_invalidated so the next criteria change can trigger a
            # fresh rollback.
            prompt.improvement_metadata = {
                k: v
                for k, v in (prompt.improvement_metadata or {}).items()
                if k != "criteria_invalidated"
            } | {"last_improvement_span_count": scored_count}
            if job:
                job.status = JobStatus.CANCELLED.value
                job.result = {
                    "reason": "Improved prompt identical to existing version",
                    "scored_count": scored_count,
                    "spans_analyzed": total_spans_fetched,
                }
            await session.commit()
            return {
                "prompt_id": prompt_id,
                "status": "unchanged",
                "scored_count": scored_count,
                "spans_analyzed": total_spans_fetched,
            }

        # --------------------------------------------------------------
        # 2. Select comparison spans (max 50, prioritise lower scores)
        # --------------------------------------------------------------
        logger.info("Selecting spans from buckets for comparison testing (max 50)")
        comparison_spans: list[SpanModel] = []

        for bucket_name in ["poor", "below_average", "average", "good", "excellent"]:
            bucket_spans = span_buckets.get(bucket_name, [])
            remaining_slots = 50 - len(comparison_spans)
            if remaining_slots <= 0:
                break
            comparison_spans.extend(bucket_spans[:remaining_slots])

        # Check AFTER iterating all buckets
        if not comparison_spans:
            logger.warning("No spans found for comparison across all buckets, skipping")
            if job:
                job.status = JobStatus.CANCELLED.value
                job.result = {
                    "reason": "No scored spans available for comparison testing",
                    "scored_count": scored_count,
                    "spans_analyzed": total_spans_fetched,
                }
                await session.commit()
            return {
                "prompt_id": prompt_id,
                "status": "no_comparison_spans",
                "scored_count": scored_count,
                "spans_analyzed": total_spans_fetched,
            }

        logger.info(f"Selected {len(comparison_spans)} spans for comparison testing")

        # --------------------------------------------------------------
        # 3. Generate outputs with candidate prompt text
        # --------------------------------------------------------------
        # Use a lightweight stand-in so we don't persist the version yet.
        class _CandidatePrompt:
            def __init__(self, text):
                self.prompt = text

        candidate = _CandidatePrompt(improved_prompt_string)

        logger.info(
            f"Generating outputs with candidate prompt for {len(comparison_spans)} spans"
        )
        generation_results = await generate_outputs_with_new_prompt(
            candidate,
            comparison_spans,
        )

        successful_results = [r for r in generation_results if "error" not in r]
        logger.info(f"Successfully generated {len(successful_results)} outputs")

        if not successful_results:
            logger.warning("All output generations failed, aborting")
            if job:
                job.status = JobStatus.FAILED.value
                job.result = {
                    "reason": "All output generations failed",
                    "scored_count": scored_count,
                    "spans_analyzed": total_spans_fetched,
                }
                await session.commit()
            return {
                "prompt_id": prompt_id,
                "status": "generation_failed",
                "scored_count": scored_count,
            }

        # --------------------------------------------------------------
        # 4. Score the new outputs
        # --------------------------------------------------------------
        if (
            not prompt.evaluation_criteria
            or "correctness" not in prompt.evaluation_criteria
        ):
            logger.warning(
                "Prompt has no evaluation criteria – cannot score candidate outputs"
            )
            if job:
                job.status = JobStatus.FAILED.value
                job.result = {"reason": "No evaluation criteria on prompt"}
                await session.commit()
            return {
                "prompt_id": prompt_id,
                "status": "no_criteria",
                "scored_count": scored_count,
            }

        criteria_rules = prompt.evaluation_criteria["correctness"]
        criteria_text = _format_criteria(criteria_rules)

        for result in successful_results:
            try:
                score = _evaluate_correctness_with_llm(
                    input_data=result["input"],
                    output_data=result["output"],
                    criteria_text=criteria_text,
                    project_description=project_description,
                    agent_description=agent_description,
                    span_metadata=result.get("metadata", {}),
                )
                result["correctness_score"] = score
            except Exception as eval_exc:
                logger.error(
                    f"Failed to score output for span {result.get('old_span_id')}: {eval_exc}"
                )
                result["correctness_score"] = None

        scored_results = [
            r for r in successful_results if r.get("correctness_score") is not None
        ]

        if not scored_results:
            logger.warning("No outputs could be scored, aborting")
            if job:
                job.status = JobStatus.FAILED.value
                job.result = {"reason": "Scoring failed for all outputs"}
                await session.commit()
            return {
                "prompt_id": prompt_id,
                "status": "scoring_failed",
                "scored_count": scored_count,
            }

        # --------------------------------------------------------------
        # 5. Compare metrics
        # --------------------------------------------------------------
        old_scores = [
            span.feedback_score.get("correctness", 0.0)
            for span in comparison_spans
            if span.feedback_score
        ]
        new_scores = [r["correctness_score"] for r in scored_results]

        avg_old = sum(old_scores) / len(old_scores) if old_scores else 0.0
        avg_new = sum(new_scores) / len(new_scores) if new_scores else 0.0
        score_delta = avg_new - avg_old
        score_delta_pct = (score_delta / avg_old * 100) if avg_old > 0 else 0.0

        # Also compare cost / latency
        total_new_cost = sum(r.get("cost", 0.0) for r in scored_results)
        avg_new_latency = (
            sum(r.get("latency_ms", 0) for r in scored_results) / len(scored_results)
            if scored_results
            else 0.0
        )

        # Compute old-prompt cost and latency from the original comparison spans.
        # Latency is derived from the span timestamps (not metadata_attributes, which
        # only carries latency_ms for synthetic comparison spans generated during tuning).
        total_old_cost = sum(
            (span.metadata_attributes or {}).get("cost", 0.0)
            for span in comparison_spans
        )
        avg_old_latency = (
            sum(
                (span.end_time_unix_nano - span.start_time_unix_nano) / 1_000_000
                for span in comparison_spans
            )
            / len(comparison_spans)
            if comparison_spans
            else 0.0
        )

        cost_delta = total_new_cost - total_old_cost
        cost_delta_pct = (
            (cost_delta / total_old_cost * 100) if total_old_cost > 0 else 0.0
        )
        latency_delta_ms = avg_new_latency - avg_old_latency
        latency_delta_pct = (
            (latency_delta_ms / avg_old_latency * 100) if avg_old_latency > 0 else 0.0
        )

        comparison_metrics = {
            "old_prompt": {
                "avg_score": round(avg_old, 4),
                "span_count": len(old_scores),
                "total_cost": round(total_old_cost, 6),
                "avg_latency_ms": round(avg_old_latency, 2),
            },
            "new_prompt": {
                "avg_score": round(avg_new, 4),
                "span_count": len(new_scores),
                "total_cost": round(total_new_cost, 6),
                "avg_latency_ms": round(avg_new_latency, 2),
            },
            "improvement": {
                "score_delta": round(score_delta, 4),
                "score_delta_pct": round(score_delta_pct, 2),
                "cost_delta": round(cost_delta, 6),
                "cost_delta_pct": round(cost_delta_pct, 2),
                "latency_delta_ms": round(latency_delta_ms, 2),
                "latency_delta_pct": round(latency_delta_pct, 2),
            },
        }

        logger.info(
            f"Comparison: {avg_old:.4f} → {avg_new:.4f} (Δ {score_delta_pct:+.2f}%)"
            f" | Cost: ${total_old_cost:.6f} → ${total_new_cost:.6f} (Δ {cost_delta_pct:+.2f}%)"
            f" | Latency: {avg_old_latency:.0f}ms → {avg_new_latency:.0f}ms (Δ {latency_delta_pct:+.2f}%)"
        )

        # --------------------------------------------------------------
        # 6. Always create comparison spans for record-keeping
        # --------------------------------------------------------------
        logger.info("Creating comparison spans with pre-computed scores")
        new_comparison_spans = await create_comparison_spans(
            prompt,
            scored_results,
            session,
            project_description=project_description,
            agent_description=agent_description,
        )

        # --------------------------------------------------------------
        # 7. Gate: only create new prompt version & suggestion if improved
        # --------------------------------------------------------------
        if score_delta <= 0:
            logger.info(
                f"No improvement detected for prompt {prompt_id} "
                f"({avg_old:.4f} → {avg_new:.4f}), discarding candidate"
            )
            # Advance the threshold so the scheduler doesn't immediately re-trigger
            # at the same span count on the next run. Mirrors the identical-candidate
            # and dedup paths. Clear criteria_invalidated for the same reason.
            prompt.improvement_metadata = {
                k: v
                for k, v in (prompt.improvement_metadata or {}).items()
                if k != "criteria_invalidated"
            } | {"last_improvement_span_count": scored_count}
            no_improve_result = {
                "prompt_id": prompt_id,
                "status": "no_improvement",
                "scored_count": scored_count,
                "spans_analyzed": total_spans_fetched,
                "suggestions_count": len(suggestions),
                "comparison_test": {
                    "spans_tested": len(comparison_spans),
                    "spans_scored": len(scored_results),
                    "spans_created": len(new_comparison_spans),
                    "metrics": comparison_metrics,
                },
            }
            if job:
                job.status = JobStatus.COMPLETED.value
                job.result = no_improve_result
                await session.commit()
            return no_improve_result

        # --------------------------------------------------------------
        # 8. Improvement confirmed → create new prompt version
        # --------------------------------------------------------------
        new_prompt = await create_prompt_version(
            prompt, improved_prompt_string, scored_count, total_spans_fetched, session
        )

        # Dedup guard (create_prompt_version returns existing if hash matches another version)
        if new_prompt.version == prompt.version:
            logger.info("Prompt version dedup hit after improvement gate, skipping")
            # Advance the threshold since the generated prompt matched an existing version.
            # Clear criteria_invalidated so the next criteria change can trigger a
            # fresh rollback.
            prompt.improvement_metadata = {
                k: v
                for k, v in (prompt.improvement_metadata or {}).items()
                if k != "criteria_invalidated"
            } | {"last_improvement_span_count": scored_count}
            if job:
                job.status = JobStatus.CANCELLED.value
                job.result = {
                    "reason": "Improved prompt identical to existing version",
                    "scored_count": scored_count,
                }
            await session.commit()
            return {
                "prompt_id": prompt_id,
                "status": "unchanged",
                "scored_count": scored_count,
            }

        # --------------------------------------------------------------
        # 9. Create a Suggestion record so the UI surfaces the result
        # --------------------------------------------------------------
        suggestion_title = (
            f"Prompt v{new_prompt.version}: +{score_delta_pct:.1f}% correctness"
        )
        suggestion_description = (
            f"Average correctness improved from {avg_old * 100:.1f} to {avg_new * 100:.1f} "
            f"across {len(scored_results)} test spans. "
        )
        if suggestions:
            suggestion_description += (
                f"Applied {len(suggestions)} improvement suggestion(s)."
            )

        suggestion_record = SuggestionModel(
            prompt_slug=prompt.slug,
            project_id=prompt.project_id,
            job_id=job_uuid,
            title=suggestion_title,
            description=suggestion_description,
            new_prompt_text=improved_prompt_string,
            new_prompt_version=new_prompt.version,
            scores={
                "avg_correctness_old": round(avg_old, 4),
                "avg_correctness_new": round(avg_new, 4),
                "spans_tested": len(comparison_spans),
                "spans_scored": len(scored_results),
                "total_cost_old": round(total_old_cost, 6),
                "total_cost_new": round(total_new_cost, 6),
                "avg_latency_ms_old": round(avg_old_latency, 2),
                "avg_latency_ms_new": round(avg_new_latency, 2),
            },
            status="pending",
        )
        session.add(suggestion_record)
        await session.commit()
        logger.info(
            f"Created suggestion {suggestion_record.suggestion_id} for prompt v{new_prompt.version}"
        )

        # --------------------------------------------------------------
        # 10. Mark job completed
        # --------------------------------------------------------------
        final_result = {
            "prompt_id": prompt_id,
            "new_version": new_prompt.version,
            "status": "improved",
            "scored_count": scored_count,
            "spans_analyzed": total_spans_fetched,
            "suggestions_count": len(suggestions),
            "suggestion_id": str(suggestion_record.suggestion_id),
            "comparison_test": {
                "spans_tested": len(comparison_spans),
                "spans_created": len(new_comparison_spans),
                "metrics": comparison_metrics,
            },
        }

        if job:
            job.status = JobStatus.COMPLETED.value
            job.result = final_result
            await session.commit()
            logger.info(
                f"Updated job entry to completed for prompt_tuning: {prompt_id}"
            )

        return final_result

    except Exception as e:
        # Update job to failed if it was created
        if job:
            try:
                job.status = JobStatus.FAILED.value
                job.result = {"error": str(e)}
                await session.commit()
                logger.info(
                    f"Updated job entry to failed for prompt_tuning: {prompt_id}"
                )
            except Exception as commit_error:
                logger.error(f"Failed to update job status to failed: {commit_error}")
        raise


async def _improve_prompt_templates(
    celery_task_id: str | None = None,
) -> dict[str, Any]:
    """
    Main task logic: Find prompts that need improvement and improve them.

    Args:
        celery_task_id: The Celery task ID for tracking

    Returns:
        Summary statistics
    """
    from overmind.db.session import dispose_engine

    try:
        AsyncSessionLocal = get_session_local()
        async with AsyncSessionLocal() as session:
            # Get all latest prompt versions per (project_id, slug)
            subquery = (
                select(
                    Prompt.project_id,
                    Prompt.slug,
                    func.max(Prompt.version).label("max_version"),
                )
                .group_by(Prompt.project_id, Prompt.slug)
                .subquery()
            )

            result = await session.execute(
                select(Prompt).join(
                    subquery,
                    and_(
                        Prompt.project_id == subquery.c.project_id,
                        Prompt.slug == subquery.c.slug,
                        Prompt.version == subquery.c.max_version,
                    ),
                )
            )

            latest_prompts = result.scalars().all()

            logger.info(f"Found {len(latest_prompts)} latest prompt versions to check")

            job_results = []
            errors = []

            for prompt in latest_prompts:
                try:
                    result = await _check_and_create_prompt_improvement_job(
                        prompt, session
                    )
                    if result:
                        job_results.append(result)
                except Exception as exc:
                    error_msg = f"Failed to check/create job for prompt {prompt.prompt_id}: {str(exc)}"
                    logger.error(error_msg)
                    errors.append(error_msg)

            summary = {
                "status": "success",
                "prompts_checked": len(latest_prompts),
                "jobs_created": len(
                    [i for i in job_results if i.get("status") == "job_created"]
                ),
                "jobs_already_exist": len(
                    [i for i in job_results if i.get("status") == "job_already_exists"]
                ),
                "prompts_skipped_low_adoption": len(
                    [
                        i
                        for i in job_results
                        if i.get("status") == "skipped_low_adoption"
                    ]
                ),
                "job_results": job_results,
                "errors": errors,
            }

            logger.info(
                f"Prompt improvement check complete: {summary['prompts_checked']} checked, {summary['jobs_created']} jobs created, {summary['jobs_already_exist']} jobs already exist, {summary['prompts_skipped_low_adoption']} skipped (low adoption), {len(errors)} errors"
            )

            return summary
    finally:
        # CRITICAL: Dispose of the engine to close all connections
        # This prevents event loop errors when the same worker runs the task again
        await dispose_engine()


@shared_task(name="prompt_improvement.improve_prompt_templates", bind=True)
@with_task_lock(lock_name="prompt_improvement")
def improve_prompt_templates(self) -> dict[str, Any]:
    """
    Celery periodic task to check prompts and create improvement jobs.

    This task runs daily and:
    1. Finds all latest prompt versions
    2. Checks if they're actively used (last 7 days)
    3. Counts scored spans and checks thresholds
    4. Creates PENDING jobs for prompts that meet improvement criteria
    5. Job reconciler will pick up these jobs and execute the actual improvements

    Uses distributed locking to prevent concurrent executions.
    If a previous instance is still running, new executions are cancelled.

    Returns:
        Dict with check results and statistics
    """
    return asyncio.run(_improve_prompt_templates(celery_task_id=self.request.id))


async def _improve_single_prompt_async(prompt_id: str, job_id: str) -> dict[str, Any]:
    """
    Async wrapper for executing a single prompt improvement.

    Args:
        prompt_id: The prompt ID to improve
        job_id: The job ID tracking this work

    Returns:
        Dict with improvement results
    """
    from overmind.db.session import dispose_engine

    try:
        AsyncSessionLocal = get_session_local()
        async with AsyncSessionLocal() as session:
            result = await _execute_prompt_improvement(prompt_id, job_id, session)
            return result
    finally:
        # CRITICAL: Dispose of the engine to close all connections
        await dispose_engine()


@shared_task(name="prompt_improvement.improve_single_prompt", bind=True)
def improve_single_prompt_task(self, prompt_id: str, job_id: str) -> dict[str, Any]:
    """
    Celery task to improve a single prompt (dispatched by job reconciler).

    This task:
    1. Fetches the prompt and job
    2. Analyzes spans and generates improvement suggestions
    3. Creates a new prompt version
    4. Runs comparison tests
    5. Updates the job with results

    Args:
        prompt_id: The prompt ID to improve
        job_id: The job ID tracking this work

    Returns:
        Dict with improvement results
    """
    return asyncio.run(_improve_single_prompt_async(prompt_id, job_id))
