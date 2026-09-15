"""Official-SDK MCP server and Streamable HTTP application."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from django.conf import settings
from django.http.request import split_domain_port, validate_host
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http import MCP_PROTOCOL_VERSION_HEADER
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import (
    DEFAULT_MAX_REQUEST_BODY_SIZE,
    TransportSecuritySettings,
)
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Message, Receive, Scope, Send

from overbae.services.mcp.auth import MCP_PATH, MCPAuthMiddleware
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import get_context
from overbae.services.mcp.errors import MCPError, error_payload
from overbae.services.mcp.prompts import get_prompt as render_prompt
from overbae.services.mcp.prompts import list_prompts
from overbae.services.mcp.resources import read_resource, resource_list, resource_templates

SERVER_NAME = "overmind-platform"
SERVER_VERSION = "1.0.0"

mcp_server = Server(SERVER_NAME, version=SERVER_VERSION)


@mcp_server.list_tools()
async def _list_tools() -> list[types.Tool]:
    context = get_context()
    scope = context.token.scope if isinstance(context.token.scope, dict) else {}
    permissions = frozenset(scope.get("permission", []))
    return CATALOG.tools(permissions)


@mcp_server.call_tool(validate_input=False)
async def _call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
    context = get_context()
    return await CATALOG.call(name, arguments or {}, context)


@mcp_server.list_resources()
async def _list_resources() -> list[types.Resource]:
    get_context()
    return resource_list()


@mcp_server.list_resource_templates()
async def _list_resource_templates() -> list[types.ResourceTemplate]:
    get_context()
    return resource_templates()


@mcp_server.list_prompts()
async def _list_prompts() -> list[types.Prompt]:
    get_context()
    return list_prompts()


@mcp_server.get_prompt()
async def _get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
    get_context()
    return render_prompt(name, arguments)


@mcp_server.read_resource()
async def _read_resource(uri):
    return await read_resource(uri)


def _allowed_hosts() -> list[str]:
    hosts = list(getattr(settings, "ALLOWED_HOSTS", []))
    if "testserver" not in hosts:
        hosts.append("testserver")
    return hosts


def _transport_security() -> TransportSecuritySettings:
    # SDK host matching is exact and drops Django `*` / `.domain` wildcards.
    # MCPTransportMiddleware already applies Django's rules.
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _json_error(error: MCPError, status_code: int) -> JSONResponse:
    return JSONResponse({"error": error_payload(error)}, status_code=status_code)


def _header(scope: Scope, name: str) -> str | None:
    for key, value in scope.get("headers", []):
        if key.decode("latin-1").lower() == name.lower():
            return value.decode("latin-1")
    return None


def _origin_allowed(origin: str | None) -> bool:
    return not origin or origin in getattr(settings, "CORS_ALLOWED_ORIGINS", [])


def _host_allowed(host: str | None) -> bool:
    if not host:
        return False
    domain, _port = split_domain_port(host)
    return bool(domain and validate_host(domain, _allowed_hosts()))


def _protocol_allowed(version: Any) -> bool:
    return version is None or version in SUPPORTED_PROTOCOL_VERSIONS


async def _read_body(receive: Receive) -> tuple[bytes, Receive]:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > DEFAULT_MAX_REQUEST_BODY_SIZE:
            raise MCPError("invalid_request", "The MCP request body is too large.")
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    body = b"".join(chunks)
    replayed = False

    async def replay() -> Message:
        nonlocal replayed
        if not replayed:
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return body, replay


class MCPTransportMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in {
            MCP_PATH,
            MCP_PATH.rstrip("/"),
        }:
            await self.app(scope, receive, send)
            return

        if not _host_allowed(_header(scope, "host")):
            await _json_error(
                MCPError("invalid_request", "The MCP host is not allowed."),
                421,
            )(scope, receive, send)
            return
        if not _origin_allowed(_header(scope, "origin")):
            await _json_error(
                MCPError("origin_not_allowed", "The MCP origin is not allowed."),
                403,
            )(scope, receive, send)
            return

        header_version = _header(scope, MCP_PROTOCOL_VERSION_HEADER)
        if not _protocol_allowed(header_version):
            await _json_error(
                MCPError(
                    "protocol_version_unsupported",
                    "The MCP protocol version is not supported.",
                ),
                400,
            )(scope, receive, send)
            return

        if scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        content_type = (_header(scope, "content-type") or "").split(";", 1)[0].lower()
        if content_type != "application/json":
            await _json_error(
                MCPError("invalid_request", "MCP requests must use application/json."),
                400,
            )(scope, receive, send)
            return

        try:
            body, replay = await _read_body(receive)
        except MCPError as error:
            await _json_error(error, 413)(scope, receive, send)
            return
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict) and payload.get("method") == "initialize":
            params = payload.get("params")
            requested_version = params.get("protocolVersion") if isinstance(params, dict) else None
            if not _protocol_allowed(requested_version):
                await _json_error(
                    MCPError(
                        "protocol_version_unsupported",
                        "The MCP protocol version is not supported.",
                    ),
                    400,
                )(scope, replay, send)
                return

        await self.app(scope, replay, send)


class _TransportEndpoint:
    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.manager.handle_request(scope, receive, send)


def create_mcp_application() -> Starlette:
    manager = StreamableHTTPSessionManager(
        app=mcp_server,
        json_response=True,
        stateless=True,
        session_idle_timeout=None,
        security_settings=_transport_security(),
    )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    app = Starlette(
        routes=[
            Route(
                MCP_PATH,
                endpoint=_TransportEndpoint(manager),
                methods=["GET", "POST", "DELETE"],
            )
        ],
        middleware=[
            Middleware(MCPAuthMiddleware),
            Middleware(MCPTransportMiddleware),
        ],
        lifespan=lifespan,
    )
    return app
