"""Request-scoped project context for MCP handlers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

from overbae.models import APIToken, Project, User
from overbae.services.mcp.errors import MCPError


@dataclass(frozen=True)
class MCPContext:
    user: User
    token: APIToken
    project: Project
    client_ip: str | None = None

    def has_permission(self, permission: str) -> bool:
        scope = self.token.scope if isinstance(self.token.scope, dict) else {}
        permissions = scope.get("permission")
        return isinstance(permissions, list) and permission in permissions


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
