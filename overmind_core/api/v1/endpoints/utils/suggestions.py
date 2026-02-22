"""
Utility functions for the suggestions endpoint.
"""

import uuid as _uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from overmind_core.api.v1.helpers.authentication import AuthenticatedUserOrToken
from overmind_core.models.suggestions import Suggestion


async def get_suggestion_or_404(
    suggestion_id: _uuid.UUID,
    user: AuthenticatedUserOrToken,
    db: AsyncSession,
) -> Suggestion:
    """
    Fetch suggestion by ID and verify user has access via project membership.

    Args:
        suggestion_id: UUID of the suggestion
        user: Authenticated user or token
        db: Database session

    Returns:
        Suggestion object

    Raises:
        HTTPException: If suggestion not found or user doesn't have access
    """
    result = await db.execute(
        select(Suggestion).where(Suggestion.suggestion_id == suggestion_id)
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if not await user.is_project_member(suggestion.project_id, db):
        raise HTTPException(status_code=403, detail="Access denied to this suggestion")
    return suggestion
