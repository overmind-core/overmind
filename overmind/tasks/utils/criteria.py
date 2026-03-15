"""
Shared helpers for fetching spans and project context used by criteria generation
and the criteria-suggest API endpoint.

These were previously private (_-prefixed) functions in criteria_generator.py.
They are promoted here so the API layer can import them without crossing a
private-function boundary.
"""

import json
import logging
from uuid import UUID

from sqlalchemy import select, and_, desc

from overmind.db.session import get_session_local
from overmind.models.iam.projects import Project
from overmind.models.traces import SpanModel

logger = logging.getLogger(__name__)


async def get_spans_for_prompt(
    prompt_id: str, limit: int = 10, prefer_judge_feedback: bool = True
) -> list[SpanModel]:
    """Fetch spans linked to a prompt.

    Returns the most recent spans (``desc`` order) so criteria suggestions are
    anchored to current agent behaviour rather than old examples.
    Prefers spans with judge_feedback when available.
    Excludes system-generated spans (prompt tuning, backtesting).
    """
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SpanModel)
            .where(
                and_(
                    SpanModel.prompt_id == prompt_id,
                    SpanModel.exclude_system_spans(),
                )
            )
            .order_by(desc(SpanModel.created_at))
            .limit(limit * 2)
        )
        all_spans = list(result.scalars().all())
        if not prefer_judge_feedback or len(all_spans) <= limit:
            return all_spans[:limit]
        with_feedback = [
            s for s in all_spans if (s.feedback_score or {}).get("judge_feedback")
        ]
        without_feedback = [
            s for s in all_spans if not (s.feedback_score or {}).get("judge_feedback")
        ]
        combined = (
            with_feedback[:limit] + without_feedback[: limit - len(with_feedback)]
        )
        return combined[:limit]


async def format_spans_as_examples(
    spans: list[SpanModel], include_judge_feedback: bool = True
) -> str:
    """Format spans into a readable example string for LLM prompts.

    Includes judge feedback when present and ``include_judge_feedback`` is True.
    """
    examples = []
    for i, span in enumerate(spans, 1):
        judge_fb = (
            (span.feedback_score or {}).get("judge_feedback")
            if include_judge_feedback
            else None
        )
        judge_section = ""
        if judge_fb and isinstance(judge_fb, dict):
            rating = judge_fb.get("rating", "unknown")
            text = judge_fb.get("text", "").strip()
            if text:
                judge_section = f"\nUser feedback on Judge (rating={rating}): {text}"
            else:
                judge_section = f"\nUser feedback on Judge: rating={rating}"
        example = f"""
Example {i}:
Input: {json.dumps(span.input or {}, indent=2)}
Output: {json.dumps(span.output or {}, indent=2)}{judge_section}
"""
        examples.append(example)
    return "\n".join(examples)


async def get_project_description(project_id: UUID) -> str:
    """Return the project description, or a generic fallback if unset."""
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if project and project.description:
            return project.description
        return "No project description available."
