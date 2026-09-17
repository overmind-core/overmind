"""API-key authentication and project authorization for MCP."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from django.db import close_old_connections, connections
from rest_framework import exceptions
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from overbae.api.authentication import APITokenBackend
from overbae.models import APIToken, Project
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.errors import MCPError, error_payload

MCP_PATH = "/api/mcp/"


@dataclass(frozen=True)
class _HeaderRequest:
    headers: Headers
    method: str
    META: dict[str, Any]


def _request_for_scope(scope: Scope) -> _HeaderRequest:
    return _HeaderRequest(
        headers=Headers(scope=scope),
        method=scope.get("method", "GET"),
        META={"REMOTE_ADDR": (scope.get("client") or (None, None))[0]},
    )


def _auth_error(detail: str) -> MCPError:
    detail = detail.lower()
    if "inactive" in detail:
        return MCPError("authentication_failed", "The API key is inactive.")
    if "expired" in detail:
        return MCPError("authentication_failed", "The API key has expired.")
    if "user" in detail:
        return MCPError("authentication_failed", "The API key owner is inactive.")
    return MCPError("authentication_failed", "The API key is invalid.")


def _ip_allowed(token: APIToken, client_ip: str | None) -> bool:
    allowed_ips = token.allowed_ips or []
    if not allowed_ips:
        return True
    if not client_ip or not isinstance(allowed_ips, list):
        return False
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    for value in allowed_ips:
        if not isinstance(value, str):
            return False
        try:
            if address in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            return False
    return False


def recycle_connections() -> None:
    close_old_connections()
    for conn in connections.all(initialized_only=True):
        raw = conn.connection
        # psycopg still sits on the wrapper after RDS closes the TCP session.
        if raw is not None and getattr(raw, "closed", 0):
            conn.close()


def _authenticate_sync(scope: Scope) -> MCPContext:
    # Starlette MCP never runs Django's request_started/finished.
    recycle_connections()
    try:
        result = APITokenBackend().authenticate(_request_for_scope(scope))
    except exceptions.AuthenticationFailed as exc:
        raise _auth_error(str(exc.detail)) from None

    if result is None:
        raise MCPError("authentication_required", "An MCP API key is required.")

    user, token = result
    if not isinstance(token, APIToken):
        raise MCPError("authentication_failed", "An MCP API key is required.")

    client_ip = (scope.get("client") or (None, None))[0]
    if not _ip_allowed(token, client_ip):
        raise MCPError("authentication_failed", "The API key is not valid for this address.")

    token_scope = token.scope if isinstance(token.scope, dict) else {}
    resource_ids = token_scope.get("resourceIds")
    if (
        token_scope.get("scope") != "project"
        or not isinstance(resource_ids, list)
        or len(resource_ids) != 1
        or token.project_id is None
        or str(resource_ids[0]) != str(token.project_id)
    ):
        raise MCPError("project_required", "The MCP API key must be pinned to one project.")

    permissions = token_scope.get("permission")
    if (
        not isinstance(permissions, list)
        or not permissions
        or any(permission not in {"read", "write"} for permission in permissions)
    ):
        raise MCPError("permission_denied", "The MCP API key has no MCP permissions.")

    project = (
        Project.objects.filter(
            pk=resource_ids[0],
            is_active=True,
            memberships__user_id=user.pk,
        )
        .distinct()
        .first()
    )
    if project is None:
        raise MCPError("project_required", "The MCP API key is not valid for a project.")

    return MCPContext(user=user, token=token, project=project, client_ip=client_ip)


authenticate_scope = sync_to_async(_authenticate_sync, thread_sensitive=True)
_recycle_connections = sync_to_async(recycle_connections, thread_sensitive=True)


class MCPAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in {MCP_PATH, MCP_PATH.rstrip("/")}:
            await self.app(scope, receive, send)
            return

        try:
            try:
                context = await authenticate_scope(scope)
            except MCPError as error:
                # Cursor treats WWW-Authenticate: Bearer as an OAuth resource and
                # POSTs /register. This surface is X-Api-Key only.
                response = JSONResponse(
                    {"error": error_payload(error)},
                    status_code=401 if error.data.code.startswith("authentication") else 403,
                )
                await response(scope, receive, send)
                return
            with bind_context(context):
                await self.app(scope, receive, send)
        finally:
            await _recycle_connections()
