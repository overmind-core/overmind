"""
Core authentication and authorization helper functions.

Enterprise (overmind_backend) extends this with RBAC-aware authentication
that loads additional relationships (organisations, token_roles).
"""

from typing import Any
from collections.abc import Callable
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import bcrypt
import secrets
import hashlib
from fastapi import Depends, Request
from fastapi.security import (
    HTTPBearer,
)
from datetime import timedelta, datetime, timezone
from jose import JWTError, jwt
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from overmind.db.session import get_db
from overmind.db.valkey import get_key, set_key, delete_key
from overmind.models.iam.users import User
from overmind.models.iam import Token
from overmind.models.pydantic_models.user import UserModel
from overmind.models.pydantic_models.token import TokenModel
from overmind.config import settings
from overmind.api.v1.helpers.responses import unauthorized_response
import logging

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


class APITokenHeader:
    def __call__(self, request: Request) -> str | None:
        api_token = request.headers.get("X-API-Token")
        return api_token if api_token else None


api_token_header = APITokenHeader()


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode(
        "utf-8"
    )


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


def generate_api_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str, str]:
    token_bytes = secrets.token_bytes(32)
    token_suffix = token_bytes.hex()
    prefix = settings.api_token_prefix
    full_token = f"{prefix}{token_suffix}"
    token_hash = hash_token(full_token)
    return full_token, token_hash, prefix


def _token_selectinload_options():
    """Build selectinload options for Token queries."""
    return [selectinload(Token.user), selectinload(Token.project)]


def _user_selectinload_options():
    """Build selectinload options for User queries."""
    return [selectinload(User.projects)]


def serialize_token_to_cache(token_record: Token) -> str:
    token_model = TokenModel.model_validate(token_record)
    return token_model.model_dump_json()


def deserialize_token_from_cache(cached_data: str) -> TokenModel:
    token_model = TokenModel.model_validate_json(cached_data)
    return token_model


def serialize_user_to_cache(user_record: User) -> str:
    user_model = UserModel.model_validate(user_record)
    return user_model.model_dump_json()


def deserialize_user_from_cache(cached_data: str) -> UserModel:
    user_model = UserModel.model_validate_json(cached_data)
    return user_model


async def get_token_record(
    token: str, db: AsyncSession, use_cache: bool = True
) -> TokenModel:
    if not token or not token.startswith(settings.api_token_prefix):
        raise unauthorized_response("Invalid or inactive token")

    token_hash = hash_token(token)
    cache_key = f"token:{token_hash}"

    if use_cache:
        cached_data = await get_key(cache_key)
    else:
        cached_data = None

    if cached_data:
        token_model = deserialize_token_from_cache(cached_data)
        if token_hash != token_model.token_hash:
            await delete_key(cache_key)
        else:
            if token_model.expires_at and token_model.expires_at < datetime.now(
                timezone.utc
            ):
                raise unauthorized_response("Token has expired")
            if not token_model.user.is_active:
                raise unauthorized_response("User is inactive")
            return token_model

    result = await db.execute(
        select(Token)
        .options(*_token_selectinload_options())
        .filter(Token.token_hash == token_hash, Token.is_active.is_(True))
    )
    token_record = result.scalar_one_or_none()

    if not token_record:
        raise unauthorized_response("Invalid or inactive token")

    if token_record.expires_at and token_record.expires_at < datetime.now(timezone.utc):
        raise unauthorized_response("Token has expired")

    if not token_record.user.is_active:
        raise unauthorized_response("User is inactive")

    if use_cache:
        if token_record.expires_at:
            ttl_seconds = int(
                (token_record.expires_at - datetime.now(timezone.utc)).total_seconds()
            )
            if ttl_seconds > 0:
                cached_token_data = serialize_token_to_cache(token_record)
                await set_key(cache_key, cached_token_data, ttl=ttl_seconds)
        else:
            cached_token_data = serialize_token_to_cache(token_record)
            await set_key(cache_key, cached_token_data, ttl=3600 * 24)

    token_model = TokenModel.model_validate(token_record)
    return token_model


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    business_id: str


class ApiTokenResponse(BaseModel):
    api_token: str
    message: str


class ApiTokenInfo(BaseModel):
    api_token: str
    username: str
    created_at: str


class AuthenticatedUserOrToken:
    """Container for authenticated user or API token."""

    def __init__(
        self,
        user: UserModel,
        token: TokenModel | None = None,
        clerk_org_id: str | None = None,
        clerk_org_role: str | None = None,
    ):
        self.user = user
        self.user_id = user.user_id
        self.email = user.email
        self.is_active = user.is_active
        self.token = token
        # Clerk org context — set from JWT payload on browser sessions
        self.clerk_org_id = clerk_org_id
        self.clerk_org_role = clerk_org_role

    def get_organisation_id(self) -> str | None:
        """Return the active Clerk organisation_id for this request."""
        if self.token is not None:
            return getattr(self.token, "organisation_id", None)
        return self.clerk_org_id

    async def is_org_member(self, org_id: str, db: AsyncSession) -> bool:
        if self.token is not None:
            return getattr(self.token, "organisation_id", None) == org_id
        return self.clerk_org_id == org_id

    async def is_project_member(self, project_id: UUID, db: AsyncSession) -> bool:
        if self.token is None:
            return project_id in [project.project_id for project in (self.user.projects or [])]
        return project_id == self.token.project_id


async def authenticate_user(username: str, password: str, db: AsyncSession):
    result = await db.execute(select(User).filter(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


async def validate_jwt_token(
    jwt_token: str, db: AsyncSession, use_cache: bool = True
) -> UserModel:
    try:
        payload = jwt.decode(jwt_token, settings.secret_key, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise unauthorized_response("No user id found in token")

        exp_timestamp = payload.get("exp")

        cache_key = f"user:{user_id}"
        if use_cache:
            cached_data = await get_key(cache_key)
        else:
            cached_data = None

        if cached_data:
            user_model = deserialize_user_from_cache(cached_data)
            if str(user_model.user_id) != user_id:
                await delete_key(cache_key)
            else:
                if not user_model.is_active:
                    raise unauthorized_response("Invalid or inactive user")
                if not user_model.is_verified and settings.require_email_verification:
                    raise unauthorized_response("User is not verified")
                return user_model

        result = await db.execute(
            select(User)
            .options(*_user_selectinload_options())
            .filter(User.user_id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise unauthorized_response("Invalid or inactive user")

        if not user.is_verified and settings.require_email_verification:
            raise unauthorized_response("User is not verified")

        if use_cache:
            if exp_timestamp:
                ttl_seconds = int(
                    exp_timestamp - datetime.now(timezone.utc).timestamp()
                )
                if ttl_seconds > 0:
                    cached_user_data = serialize_user_to_cache(user)
                    await set_key(cache_key, cached_user_data, ttl=ttl_seconds)
            else:
                cached_user_data = serialize_user_to_cache(user)
                await set_key(cache_key, cached_user_data, ttl=900)

        user_model = UserModel.model_validate(user)
        return user_model
    except JWTError:
        raise unauthorized_response("Invalid JWT")


class RBACAuthenticationProvider:
    """
    Authentication provider that supports both API token and JWT auth.

    Named "RBAC" for compatibility with enterprise, but in core mode
    there is no actual RBAC permission checking — every authenticated
    request is authorized.
    """

    async def authenticate(
        self,
        request: Any,
        db: AsyncSession,
        use_cache: bool = True,
    ) -> AuthenticatedUserOrToken:
        api_token: str | None = request.headers.get("X-API-Token")
        jwt_token: str | None = None
        auth_header: str | None = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            jwt_token = auth_header[7:]

        return await _authenticate_with_tokens(
            api_token=api_token,
            jwt_token=jwt_token,
            db=db,
            use_cache=use_cache,
        )


async def _authenticate_with_tokens(
    api_token: str | None,
    jwt_token: str | None,
    db: AsyncSession,
    use_cache: bool = True,
) -> AuthenticatedUserOrToken:
    if api_token:
        token_model = await get_token_record(api_token, db, use_cache=use_cache)
        return AuthenticatedUserOrToken(user=token_model.user, token=token_model)

    if jwt_token:
        # EE: check if jwt is from clerk, and if so populate user model accordingly
        user_model = await validate_jwt_token(jwt_token, db, use_cache=use_cache)
        return AuthenticatedUserOrToken(user=user_model)

    raise unauthorized_response("No authentication method found")


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthenticatedUserOrToken:
    provider = getattr(request.app.state, "authentication_provider", None)
    if provider is not None:
        return await provider.authenticate(request, db)

    api_token = request.headers.get("X-API-Token")
    auth_header = request.headers.get("Authorization")
    jwt_token = (
        auth_header[7:]
        if auth_header and auth_header.lower().startswith("bearer ")
        else None
    )
    return await _authenticate_with_tokens(api_token, jwt_token, db)


def get_current_user_factory(use_cache: bool = True) -> Callable:
    async def _get_current_user(
        request: Request,
        db: AsyncSession = Depends(get_db),
    ) -> AuthenticatedUserOrToken:
        provider = getattr(request.app.state, "authentication_provider", None)
        if provider is not None:
            return await provider.authenticate(request, db, use_cache=use_cache)

        api_token = request.headers.get("X-API-Token")
        auth_header = request.headers.get("Authorization")
        jwt_token = (
            auth_header[7:]
            if auth_header and auth_header.lower().startswith("bearer ")
            else None
        )
        return await _authenticate_with_tokens(
            api_token, jwt_token, db, use_cache=use_cache
        )

    return _get_current_user
