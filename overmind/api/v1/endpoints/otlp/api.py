import logging
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from overmind.db.session import get_db
from overmind.api.v1.helpers.authentication import (
    get_current_user,
    AuthenticatedUserOrToken,
)

from .transformers import create_trace
from .auth import is_valid_backend_user

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/create")
async def create_trace_endpoint(
    request: Request,
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Endpoint to receive OTLP traces over HTTP/protobuf.
    """
    return await create_trace(
        request=request,
        project_id=current_user.token.project_id,
        business_id=current_user.token.organisation_id,
        user_id=current_user.token.user_id,
        db=db,
    )


@router.post("/create-backend-trace")
async def create_backend_trace_endpoint(
    request: Request,
    is_authorized_backend_user: dict = Depends(is_valid_backend_user),
    db: AsyncSession = Depends(get_db),
):
    # current user will be a special admin user - probably not even a user but a hardcoded token (we can get it via EKS secret thing)
    # and then just extract "current_user" from the request body as this will be provided by the backend when calling this endpoint
    if not is_authorized_backend_user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # backend user may bundle spans from different projects and businesses, so we will rely on span attributes to provide correct
    # project_id and business_id for each span
    return await create_trace(
        request=request,
        project_id=None,
        business_id=None,
        user_id=None,
        db=db,
    )


@router.post("/create-chat-trace")
async def create_chat_trace_endpoint():
    raise HTTPException(
        status_code=400,
        detail="deprecated, please contact support at support@overmindlab.ai",
    )
