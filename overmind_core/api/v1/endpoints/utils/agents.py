"""
Utility functions for the agents endpoint.
"""

import logging
from typing import List

from sqlalchemy import and_, cast, func, select, Float
from sqlalchemy.ext.asyncio import AsyncSession

from overmind_core.models.prompts import Prompt
from overmind_core.models.traces import SpanModel
from overmind_core.utils import calculate_llm_usage_cost

logger = logging.getLogger(__name__)


def humanise_slug(slug: str) -> str:
    """
    Convert a slug to a human-readable name.

    Args:
        slug: The slug to humanise (e.g., "my-agent_name")

    Returns:
        Human-readable name (e.g., "My Agent Name")
    """
    return slug.replace("-", " ").replace("_", " ").title()


async def get_analytics_for_prompt(
    prompt_id: str,
    project_id,
    db: AsyncSession,
) -> dict:
    """
    Build aggregate + hourly analytics for a single prompt.

    Args:
        prompt_id: The ID of the prompt to get analytics for
        project_id: The project ID
        db: Database session

    Returns:
        Dictionary containing analytics data with keys:
        - total_spans: Total number of spans
        - scored_spans: Number of spans with feedback scores
        - avg_score: Average feedback score
        - avg_latency_ms: Average latency in milliseconds
        - total_estimated_cost: Estimated cost
        - hourly: List of hourly buckets with metrics
    """
    from datetime import datetime

    # ---- total spans (exclude system-generated spans) ----
    total_q = await db.execute(
        select(func.count(SpanModel.span_id)).where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.exclude_system_spans(),
            )
        )
    )
    total_spans = total_q.scalar() or 0

    # ---- scored spans + avg score ----
    scored_q = await db.execute(
        select(
            func.count(SpanModel.span_id),
            func.avg(cast(SpanModel.feedback_score["correctness"], Float)),
        ).where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.feedback_score.has_key("correctness"),
                SpanModel.exclude_system_spans(),
            )
        )
    )
    scored_row = scored_q.one()
    scored_spans = scored_row[0] or 0
    avg_score = round(float(scored_row[1]), 4) if scored_row[1] is not None else None

    # ---- avg latency (ms) ----
    latency_q = await db.execute(
        select(
            func.avg(
                (SpanModel.end_time_unix_nano - SpanModel.start_time_unix_nano)
                / 1_000_000.0
            )
        ).where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.exclude_system_spans(),
            )
        )
    )
    avg_latency_ms = latency_q.scalar()
    if avg_latency_ms is not None:
        avg_latency_ms = round(float(avg_latency_ms), 2)

    # ---- total estimated cost ----
    # Fetch all spans to estimate cost (we limit to last 1000 for performance)
    cost_spans_q = await db.execute(
        select(SpanModel.metadata_attributes)
        .where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.exclude_system_spans(),
            )
        )
        .order_by(SpanModel.created_at.desc())
        .limit(1000)
    )
    cost_rows = cost_spans_q.all()
    total_cost = 0.0
    for row in cost_rows:
        total_cost += calculate_llm_usage_cost(
            row[0].get("gen_ai.request.model", ""),
            row[0].get("gen_ai.usage.input_tokens", 0),
            row[0].get("gen_ai.usage.output_tokens", 0),
        )
    # Scale up if we have more spans than we sampled
    if total_spans > len(cost_rows) and len(cost_rows) > 0:
        total_cost = total_cost * (total_spans / len(cost_rows))
    total_cost = round(total_cost, 6)

    # ---- hourly buckets ----
    hourly_q = await db.execute(
        select(
            func.date_trunc("hour", SpanModel.created_at).label("hour"),
            func.count(SpanModel.span_id).label("cnt"),
            func.avg(cast(SpanModel.feedback_score["correctness"], Float)).label(
                "avg_score"
            ),
            func.avg(
                (SpanModel.end_time_unix_nano - SpanModel.start_time_unix_nano)
                / 1_000_000.0
            ).label("avg_lat"),
        )
        .where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.exclude_system_spans(),
            )
        )
        .group_by("hour")
        .order_by("hour")
    )
    hourly_rows = hourly_q.all()

    hourly_buckets: List[dict] = []
    for row in hourly_rows:
        bucket_hour: datetime = row[0]
        bucket_count = row[1] or 0
        bucket_score = round(float(row[2]), 4) if row[2] is not None else None
        bucket_lat = round(float(row[3]), 2) if row[3] is not None else None
        # Rough cost per bucket
        if total_spans > 0:
            bucket_cost = round(total_cost * (bucket_count / total_spans), 6)
        else:
            bucket_cost = 0.0
        hourly_buckets.append(
            {
                "hour": bucket_hour.isoformat() if bucket_hour else "",
                "avg_score": bucket_score,
                "span_count": bucket_count,
                "avg_latency_ms": bucket_lat,
                "estimated_cost": bucket_cost,
            }
        )

    return {
        "total_spans": total_spans,
        "scored_spans": scored_spans,
        "avg_score": avg_score,
        "avg_latency_ms": avg_latency_ms,
        "total_estimated_cost": total_cost,
        "hourly": hourly_buckets,
    }


async def get_latest_prompts_for_project(project_id, db: AsyncSession) -> List[Prompt]:
    """
    Return the latest version of each prompt slug in a project.

    Args:
        project_id: The project UUID
        db: Database session

    Returns:
        List of Prompt objects representing the latest version of each prompt slug
    """
    subq = (
        select(
            Prompt.project_id,
            Prompt.slug,
            func.max(Prompt.version).label("max_version"),
        )
        .where(Prompt.project_id == project_id)
        .group_by(Prompt.project_id, Prompt.slug)
        .subquery()
    )
    result = await db.execute(
        select(Prompt).join(
            subq,
            and_(
                Prompt.project_id == subq.c.project_id,
                Prompt.slug == subq.c.slug,
                Prompt.version == subq.c.max_version,
            ),
        )
    )
    return list(result.scalars().all())
