import logging
from fastapi import HTTPException
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, func, or_, select
from overmind_core.models.traces import SpanModel, TraceModel
from overmind_core.utils import to_nano
from overmind_core.overmind.trace_filter_backend import build_conditions

from overmind_core.models.pydantic_models.traces import (
    TraceSpansResponseModel,
    SpanResponseModel,
    TraceListResponseModel,
)

logger = logging.getLogger(__name__)


async def _get_trace_by_id(
    trace_id: str, user_id: str, db: AsyncSession
) -> TraceSpansResponseModel:
    # get user id so we know user has access to the trace

    _trace = await db.execute(
        select(TraceModel).where(
            and_(TraceModel.trace_id == trace_id, TraceModel.user_id == user_id)
        )
    )

    trace_obj = _trace.scalars().first()
    if not trace_obj:
        raise HTTPException(
            status_code=404,
            detail=f"Trace with ID {trace_id} not found or not accessible.",
        )

    query = (
        select(SpanModel)
        .where(and_(SpanModel.trace_id == trace_id, SpanModel.exclude_system_spans()))
        .order_by(SpanModel.start_time_unix_nano.asc())
    )
    result = await db.execute(query)
    span_objs = result.scalars().all()

    if not span_objs:
        raise HTTPException(
            status_code=404,
            detail=f"Trace with ID {trace_id} not found or not accessible.",
        )

    # Ensure input is valid dictionary or instance of SpanResponseModel
    spans = []
    for span_obj in span_objs:
        try:
            model_instance = SpanResponseModel.from_orm_obj(span_obj, trace_obj)
            spans.append(model_instance)
        except Exception as e:
            logger.error(f"failed to serialize span: {e}")

    return TraceSpansResponseModel(
        trace_id=str(trace_id), spans=spans, span_count=len(spans)
    )


async def _list_traces(
    *,
    db: AsyncSession,
    filters: TraceListResponseModel.TraceListFilterModel,
) -> TraceListResponseModel:
    # Handle timestamp defaults (last 15 minutes if not provided)
    now = datetime.now(timezone.utc)
    if filters.end_time_nano is None:
        filters.end_time_nano = to_nano(now)

    if filters.start_time_nano is None:
        filters.start_time_nano = (
            filters.end_time_nano - 900_000_000_000
        )  # 15 minutes in nanoseconds

    # ── Always-on conditions (security + time window) ────────────────────────
    conditions = [
        TraceModel.project_id == filters.project_id,
        SpanModel.start_time_unix_nano >= filters.start_time_nano,
        SpanModel.start_time_unix_nano <= filters.end_time_nano,
        SpanModel.exclude_system_spans(),
    ]

    if filters.user_id:
        conditions.append(TraceModel.user_id == filters.user_id)

    if filters.root_only:
        conditions.append(
            or_(SpanModel.parent_span_id.is_(None), SpanModel.parent_span_id == "")
        )

    # ── Prompt shorthand (compound key logic) ────────────────────────────────
    if filters.prompt_slug:
        if filters.prompt_version:
            conditions.append(
                SpanModel.prompt_id
                == f"{filters.project_id}_{filters.prompt_version}_{filters.prompt_slug}"
            )
        else:
            conditions.append(
                SpanModel.prompt_id.like(
                    f"{filters.project_id}_%_{filters.prompt_slug}"
                )
            )

    # ── Generic DRF-style filters ─────────────────────────────────────────────
    conditions.extend(build_conditions(filters.query))

    stmt = (
        select(SpanModel, TraceModel)
        .join(TraceModel, SpanModel.trace_id == TraceModel.trace_id)
        .where(and_(*conditions))
        .order_by(SpanModel.start_time_unix_nano.desc())
    )
    query = stmt.limit(filters.limit).offset(filters.offset)
    result = await db.execute(query)
    total_count = await db.scalar(select(func.count()).select_from(stmt))
    res = result.all()
    span_objs, trace_objs = zip(*res) if res else ([], [])

    return TraceListResponseModel(
        traces=[
            SpanResponseModel.from_orm_obj(span_obj, trace_obj)
            for span_obj, trace_obj in zip(span_objs, trace_objs)
        ],
        count=total_count,
        limit=filters.limit,
        offset=filters.offset,
        filters=filters,
    )
