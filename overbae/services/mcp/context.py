"""Request-scoped account and authorized project context for MCP handlers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from uuid import UUID

from overbae.models import APIToken, MCPOAuthToken, Project, User
from overbae.services.mcp.errors import MCPError


@dataclass(frozen=True)
class MCPContext:
    user: User
    token: APIToken | MCPOAuthToken
    project: Project | None
    client_ip: str | None = None
    inference_base_url: str = ""

    def has_permission(self, permission: str) -> bool:
        scope = self.token.scope if isinstance(self.token.scope, dict) else {}
        permissions = scope.get("permission")
        return isinstance(permissions, list) and permission in permissions


def accessible_projects(context: MCPContext):
    projects = Project.objects.filter(is_active=True, memberships__user_id=context.user.pk)
    if context.token.scope.get("scope") == "project":
        projects = projects.filter(pk=context.token.project_id)
    return projects.distinct().order_by("name", "id")


def project_context(context: MCPContext, project_id: str | None) -> MCPContext:
    # The request authenticator already checked a project-bound credential's membership.
    if project_id is None and context.project is not None:
        return context
    if project_id is None and context.token.scope.get("scope") == "project":
        project_id = str(context.token.project_id)
    try:
        selected_id = UUID(str(project_id))
    except (ValueError, TypeError, AttributeError):
        raise MCPError(
            "project_required", "Pass project_id from list_projects for this operation."
        ) from None
    project = accessible_projects(context).filter(pk=selected_id).first()
    if project is None:
        raise MCPError("project_required", "The project is not accessible to this connection.")
    return replace(context, project=project)


_current_context: ContextVar[MCPContext | None] = ContextVar("mcp_context", default=None)


def get_context() -> MCPContext:
    context = _current_context.get()
    if context is None:
        raise MCPError("authentication_required", "MCP authentication is required.")
    return context


@contextmanager
def bind_context(context: MCPContext) -> Iterator[None]:
    token: Token[MCPContext | None] = _current_context.set(context)
    try:
        yield
    finally:
        _current_context.reset(token)
