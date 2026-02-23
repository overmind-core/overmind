import logging
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from overmind_core.models.pydantic_models.traces import (
    TraceListResponseModel,
    TraceSpansResponseModel,
)
from overmind_core.api.v1.helpers.authentication import AuthenticatedUserOrToken, get_current_user
from overmind_core.overmind.tracing_helpers import (
    _get_trace_by_id,
    _list_traces,
)
from overmind_core.overmind.trace_filter_backend import AVAILABLE_FIELDS
from overmind_core.utils import to_nano
from overmind_core.api.v1.helpers.permissions import ProjectPermission
from overmind_core.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

_FILTER_DESCRIPTION = f"""\
DRF-style filter expression.  Format: **`field;operator;value`**

Repeat the parameter for multiple conditions — all are AND-ed together.

**Available fields:** {AVAILABLE_FIELDS}

**Operators:**

| Operator | Meaning |
|----------|---------|
| `eq`     | equals |
| `neq`    | not equals |
| `gt` / `gte` | greater than / greater than or equal |
| `lt` / `lte` | less than / less than or equal |
| `like`   | SQL LIKE (use `%` as wildcard) |
| `ilike`  | case-insensitive LIKE |
| `in`     | IN comma-separated list |
| `notin`  | NOT IN comma-separated list |
| `isnull` | IS NULL (`true`) / IS NOT NULL (`false`) |

**Examples:**

```
?filter=status;eq;error
?filter=status_code;eq;2
?filter=duration_ms;gte;100&filter=duration_ms;lte;5000
?filter=operation;ilike;%chat%
?filter=parent_span_id;isnull;true
?filter=span_attr.gen_ai.request.model;eq;gpt-4o
?filter=source;in;overmind_api,langchain
?filter=application_name;ilike;my-%
```
"""


@router.get("/trace/{trace_id}", response_model=TraceSpansResponseModel)
async def get_trace_by_id(
    trace_id: str,
    request: Request,
    project_id: UUID = Query(..., description="Filter by project ID"),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve a specific trace by its trace ID.
    Automatically filters by BusinessId from the current user.
    Returns all spans in the trace with complete data (no truncation).
    """
    authorization_provider = request.app.state.authorization_provider
    organisation_id = current_user.get_organisation_id()
    user_id = current_user.user.user_id
    await authorization_provider.check_permissions(
        user=current_user,
        db=db,
        required_permissions=[ProjectPermission.VIEW_CONTENT.value],
        organisation_id=organisation_id,
        project_id=project_id,
    )

    result = await _get_trace_by_id(trace_id=trace_id, user_id=user_id, db=db)
    logger.info(
        f"Successfully retrieved trace {trace_id} with {result.span_count} spans for project_id={project_id}"
    )
    return result


@router.get("/list", response_model=TraceListResponseModel)
async def list_traces(
    request: Request,
    project_id: UUID = Query(..., description="Filter by project ID"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    root_only: bool = Query(True),
    start_timestamp: datetime | None = Query(None),
    end_timestamp: datetime | None = Query(None),
    prompt_slug: str | None = Query(None),
    prompt_version: str | None = Query(None),
    # TODO: remove all and just keep query, ordering, and root_only, pagination
    # drf like filter=["status;eq;error","duration_ms;gte;100","duration_ms;lte;5000"]
    query: list[str] = Query(default=[], description=_FILTER_DESCRIPTION),
    # drf like ordering=["-start_time_unix_nano","-duration_ms"]
    ordering: list[str] = Query(default=[], description="Ordering of the traces"),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve traces with filtering by project, time window, and arbitrary span/trace fields.

    Use the `filter` parameter for fine-grained querying — see parameter description
    for the full field list, operator table, and copy-paste examples.

    If no timestamps are provided, defaults to the last 15 minutes.
    """
    ordering = validate_ordering(
        ordering, ["name", "timestamp", "duration", "tokens", "cost"]
    )

    authorization_provider = request.app.state.authorization_provider
    organisation_id = current_user.get_organisation_id()
    auth_context = await authorization_provider.check_permissions(
        user=current_user,
        db=db,
        required_permissions=[ProjectPermission.VIEW_CONTENT.value],
        organisation_id=organisation_id,
        project_id=project_id,
    )

    filters = TraceListResponseModel.TraceListFilterModel(
        project_id=project_id,
        prompt_slug=prompt_slug,
        prompt_version=prompt_version,
        limit=limit,
        offset=offset,
        root_only=root_only,
        query=query,
    )
    if start_timestamp:
        filters.start_time_nano = to_nano(start_timestamp)

    if end_timestamp:
        filters.end_time_nano = to_nano(end_timestamp)

    # For non-admin users we only allow viewing their own traces.
    # In core mode (noop auth) auth_context is None → no user-scoping.
    user_permissions = getattr(auth_context, "USER_PERMISSIONS", None)
    if (
        user_permissions is not None
        and ProjectPermission.ADMIN.value not in user_permissions
    ):
        filters.user_id = current_user.user.user_id

    return await _list_traces(db=db, filters=filters)


def validate_ordering(ordering: list[str], allowed: list[str]) -> list[str]:
    """
    Ensure that each field in the ordering list (with or without leading '-')
    is inside the allowed set. Returns a filtered list of valid ordering fields
    (preserving leading '-') and discards invalid fields.
    """
    validated = []
    for field in ordering:
        clean_field = field[1:] if field.startswith("-") else field
        if clean_field in allowed:
            validated.append(field)
    return validated
