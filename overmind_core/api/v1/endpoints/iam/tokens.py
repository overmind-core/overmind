"""
Core IAM – API token CRUD.

Simplified token management with no Organisation scoping, no token roles.
Auth is verified by checking that the user owns the project the token belongs to.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from overmind_core.api.v1.helpers.authentication import (
    AuthenticatedUserOrToken,
    generate_token,
    get_current_user,
)
from overmind_core.api.v1.helpers.responses import (
    conflict_response,
    forbidden_response,
    not_found_response,
    success_response,
)
from overmind_core.db.session import get_db
from overmind_core.db.valkey import delete_key
from overmind_core.models.iam.tokens import Token
from overmind_core.models.pydantic_models.core_models import CoreTokenModel

router = APIRouter(prefix="/tokens", tags=["Tokens"])


# ── request / response schemas ────────────────────────────────────────────


class CreateTokenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    project_id: UUID
    expires_in_days: Optional[int] = Field(None, ge=1, le=365)
    allowed_ips: Optional[List[str]] = None


class CreateTokenResponse(BaseModel):
    token_id: UUID
    name: str
    description: Optional[str] = None
    project_id: UUID
    token: str
    prefix: str
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class UpdateTokenRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    is_active: Optional[bool] = None
    expires_at: Optional[datetime] = None
    allowed_ips: Optional[List[str]] = None


class TokenListResponse(BaseModel):
    tokens: List[CoreTokenModel]
    total_count: int


# ── helpers ───────────────────────────────────────────────────────────────


async def _verify_project_ownership(
    project_id: UUID,
    current_user: AuthenticatedUserOrToken,
    db: AsyncSession,
) -> None:
    """Verify the user has access to the project."""
    if not await current_user.is_project_member(project_id, db):
        raise forbidden_response("Access denied to this project")


async def _get_token_or_404(
    token_id: UUID,
    current_user: AuthenticatedUserOrToken,
    db: AsyncSession,
) -> Token:
    """Load a token and verify the user owns its project."""
    result = await db.execute(select(Token).where(Token.token_id == token_id))
    token = result.scalar_one_or_none()
    if not token:
        raise not_found_response("Token not found")

    if not await current_user.is_project_member(token.project_id, db):
        raise forbidden_response("Access denied to this token")

    return token


# ── endpoints ─────────────────────────────────────────────────────────────


@router.post("/", response_model=CreateTokenResponse)
async def create_token(
    request: CreateTokenRequest,
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new API token for a project. Returns the plain-text token once."""
    await _verify_project_ownership(request.project_id, current_user, db)

    existing = await db.execute(
        select(Token).where(
            and_(
                Token.user_id == current_user.user_id,
                Token.project_id == request.project_id,
                Token.name == request.name,
            )
        )
    )
    if existing.scalar_one_or_none():
        raise conflict_response("A token with this name already exists in this project")

    full_token, token_hash, prefix = generate_token()

    expires_at = None
    if request.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=request.expires_in_days
        )

    new_token = Token(
        name=request.name,
        description=request.description,
        user_id=current_user.user_id,
        project_id=request.project_id,
        token_hash=token_hash,
        prefix=prefix,
        is_active=True,
        expires_at=expires_at,
        allowed_ips=request.allowed_ips,
    )
    db.add(new_token)
    await db.commit()

    return CreateTokenResponse(
        token_id=new_token.token_id,
        name=new_token.name,
        description=new_token.description,
        project_id=new_token.project_id,
        token=full_token,
        prefix=new_token.prefix,
        expires_at=new_token.expires_at,
        created_at=new_token.created_at,
    )


@router.get("/", response_model=TokenListResponse)
async def list_tokens(
    project_id: UUID = Query(..., description="Project to list tokens for"),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all tokens for a project."""
    await _verify_project_ownership(project_id, current_user, db)

    result = await db.execute(
        select(Token)
        .where(Token.project_id == project_id)
        .order_by(Token.created_at.desc())
    )
    tokens = result.scalars().all()

    return TokenListResponse(
        tokens=[CoreTokenModel.model_validate(t) for t in tokens],
        total_count=len(tokens),
    )


@router.get("/{token_id}", response_model=CoreTokenModel)
async def get_token(
    token_id: UUID,
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single token by ID."""
    token = await _get_token_or_404(token_id, current_user, db)
    return CoreTokenModel.model_validate(token)


@router.put("/{token_id}", response_model=CoreTokenModel)
async def update_token(
    token_id: UUID,
    request: UpdateTokenRequest,
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a token's properties."""
    token = await _get_token_or_404(token_id, current_user, db)

    if request.name is not None:
        token.name = request.name
    if request.description is not None:
        token.description = request.description
    if request.is_active is not None:
        token.is_active = request.is_active
    if request.expires_at is not None:
        token.expires_at = request.expires_at
    if request.allowed_ips is not None:
        token.allowed_ips = request.allowed_ips

    await db.commit()
    await db.refresh(token)

    await delete_key(f"token:{token.token_hash}")

    return CoreTokenModel.model_validate(token)


@router.delete("/{token_id}")
async def delete_token(
    token_id: UUID,
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a token."""
    token = await _get_token_or_404(token_id, current_user, db)

    await delete_key(f"token:{token.token_hash}")
    await db.delete(token)
    await db.commit()

    return success_response(message="Token deleted successfully")
