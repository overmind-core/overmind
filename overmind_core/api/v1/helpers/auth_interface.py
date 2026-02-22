"""
Auth/Authz interface protocols.

These Protocols define the authentication and authorization contracts — the
seam between ``overmind_core`` (open / standalone) and ``overmind_backend``
(enterprise).

Core provides basic implementations; enterprise plugs in its RBAC
implementation.  Both are registered on ``app.state`` at startup:

* ``app.state.authentication_provider`` — ``AuthenticationProvider``
* ``app.state.authorization_provider`` — ``AuthorizationProvider``
* ``app.state.org_policy_provider``    — defined in ``policy_interface.py``
"""

from typing import Any, List, Optional, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Authenticated context
# ---------------------------------------------------------------------------


@runtime_checkable
class AuthenticatedContext(Protocol):
    """Represents an authenticated caller — either a user session or an API token."""

    user_id: UUID
    project_id: Optional[UUID]  # set when auth is via token
    token_id: Optional[UUID]  # set when auth is via token


# ---------------------------------------------------------------------------
# Authentication provider
# ---------------------------------------------------------------------------


@runtime_checkable
class AuthenticationProvider(Protocol):
    """Authenticates an incoming request and returns context."""

    async def authenticate(
        self,
        request: Any,
        db: AsyncSession,
        use_cache: bool = True,
    ) -> Any:
        """Return an ``AuthenticatedContext``-compatible object or raise 401."""
        ...


# ---------------------------------------------------------------------------
# Authorization provider
# ---------------------------------------------------------------------------


@runtime_checkable
class AuthorizationProvider(Protocol):
    """Checks whether the authenticated caller can perform an action."""

    async def check_permissions(
        self,
        user: Any,
        db: AsyncSession,
        required_permissions: List[str],
        organisation_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
        mode: str = "all",
    ) -> Any:
        """Raise HTTP 403 if denied; return authorization context if allowed."""
        ...


# ---------------------------------------------------------------------------
# Core (noop) implementations — used by overmind_core standalone
# ---------------------------------------------------------------------------


class NoopAuthorizationProvider:
    """Core default: no RBAC — every authenticated request is authorized."""

    async def check_permissions(
        self,
        user: Any,
        db: AsyncSession,
        required_permissions: List[str],
        organisation_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
        mode: str = "all",
    ) -> None:
        # In core mode every authenticated user is authorized.
        return None
