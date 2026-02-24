"""
Core IAM – minimal user management.

Provides login, profile retrieval, and password change.
No signup (single auto-provisioned user), no password-reset email,
no multi-user listing, no impersonation.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from overmind.api.v1.helpers.authentication import (
    AuthenticatedUserOrToken,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from overmind.api.v1.helpers.responses import error_response, success_response
from overmind.db.session import get_db
from overmind.models.pydantic_models.core_models import CoreUserModel

router = APIRouter(prefix="/users", tags=["Users"])


# ── request / response schemas ────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user: CoreUserModel


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ── endpoints ─────────────────────────────────────────────────────────────


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate with email + password and receive a JWT."""
    from overmind.models.iam.users import User
    from sqlalchemy import select

    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise error_response("Invalid email or password", status_code=401)

    if not verify_password(request.password, user.hashed_password):
        raise error_response("Invalid email or password", status_code=401)

    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    access_token = create_access_token(
        data={"sub": str(user.user_id)},
        expires_delta=timedelta(hours=24),
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user=CoreUserModel(
            user_id=user.user_id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            created_at=user.created_at,
        ),
    )


@router.get("/me", response_model=CoreUserModel)
async def get_me(
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
):
    """Return the authenticated user's profile."""
    u = current_user.user
    return CoreUserModel(
        user_id=u.user_id,
        email=u.email,
        full_name=u.full_name,
        is_active=u.is_active,
        created_at=u.created_at,
    )


@router.put("/me/password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change the current user's password."""
    from overmind.models.iam.users import User
    from sqlalchemy import select

    from overmind.db.valkey import delete_key

    result = await db.execute(select(User).where(User.user_id == current_user.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise error_response("User not found", status_code=404)

    if not verify_password(request.current_password, user.hashed_password):
        raise error_response("Current password is incorrect", status_code=400)

    user.hashed_password = hash_password(request.new_password)
    await db.commit()

    await delete_key(f"user:{current_user.user_id}")

    return success_response(message="Password changed successfully")
