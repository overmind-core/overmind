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
from posthog import Posthog
from posthog.mcp import PostHogMcpStatelessSessionMiddleware, instrument
from posthog.mcp.types import MCPAnalyticsOptions, UserIdentity
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Message, Receive, Scope, Send

from overbae.services.mcp.auth import MCP_PATH, MCPAuthMiddleware
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import get_context
from overbae.services.mcp.errors import MCPError, error_payload
from overbae.services.mcp.oauth_routes import domain_challenge, oauth_routes
from overbae.services.mcp.prompts import get_prompt as render_prompt
from overbae.services.mcp.prompts import list_prompts
from overbae.services.mcp.resources import read_resource, resource_list, resource_templates

SERVER_NAME = "overmind-platform"
SERVER_VERSION = "6.3.0"

mcp_server = Server(
    SERVER_NAME,
    version=SERVER_VERSION,
    instructions=(
        "Use MCP for Overmind operations and the local CLI for file bytes. Missing tools, credentials or upload failures are not reasons to open a browser; report the connection error. Only open the Console when the user explicitly requests a visual view. "
        "Overmind is an agent improvement platform. Read overmind://interface/current for connected contract identity and workflow rules. Drafts never submit provider work; use explicit preparation and launch operations. Call list_projects first to find "
        "accessible projects and choose the project relevant to the user's request. "
        "Account connections require project_id on each project tool and resource URI; "
        "there is no shared selected-project state. Follow returned resource links. "
        "Read overmind://project/current?project_id=ID for repository provenance and "
        "the ordinary Console URL. Project-scoped API keys remain limited to their project; "
        "do not mix records from a different Console project or put credentials in tool arguments. "
        "MCP provides the integration without a plugin or custom UI. Discover tools, "
        "resources and prompts through their MCP lists; use the current schemas and returned "
        "identifiers rather than inventing endpoint-shaped tools. Native prompts guide longer "
        "workflows; optional skills cover local repository work and clients without prompt support. "
        "Tools return the same data as structured content and JSON text. Treat isError and "
        "structured error fields as failures; follow resource links and poll get_job with "
        "the returned kind and id for asynchronous work. Preserve pagination, truncation, "
        "missing scores, evaluator errors and pinned dataset/model identities in conclusions. "
        "Inspection does not authorize mutations or paid runs. Check readiness and costs "
        "before an authorized evaluation or training launch. The calling coding agent owns dataset interpretation, transformation authoring and semantic work. "
        "The platform has no Workshop planning agent. Discover reusable project revisions with inspect_dataset_workbench; prefer reuse or an attributed derived_from variant. New transformations require a retained Python package; package-free historical revisions are read-only. Upload scripts using overmind dataset pipeline-upload, register with save_dataset_pipeline, validate_dataset_pipeline, preview then run_dataset_pipeline. Each step publishes an immutable cell. Enable saved source bindings explicitly for repeated snapshot rebuilds. import_dataset_version is for genuine external results, not a substitute for retaining transformation code. "
        "When asked to prepare data in Overmind, local files alone are not completion: publish and inspect the requested dataset or report an explicit blocker. Inspect the named capability's contract and examples before choosing target semantics; link it explicitly. Preserve unknown meaning rather than inventing task labels. Separate meaningful preparation stages and review branches from final formatting. Use the returned upload argv and identifiers verbatim, replacing only documented placeholders. "
        "Preview defers absolute min_rows/max_rows bounds to publication; lineage and preserve_rows checks remain enforced. Inspect deferred checks before publishing. Follow poll_after_seconds; queued work alone is not a failure or reason to resubmit. Report format compatibility separately from task suitability, which remains unmeasured without attributable evidence. "
        "Author explicit step IDs and input dependencies. Converge intended branches with inputs=[earlier IDs]; disjoint rows concatenate in declared order, overlapping source_row identities fail. The last step is the output: inspect flow.unconsumed_steps before publication. Training preparation should end in trainable examples with review metadata, not disconnected inspection tables; never invent targets or treat flags as resolved. Script conditions cite their entrypoint line and describe code, not independently verified semantics. Receipts record actual parent cells, counts and fingerprints. Pin sources and use stable request keys. "
        "Use author-dataset-transformation for conditional authoring, retrieval and adaptation. inspect_dataset_workbench(pipeline=revision ID) retrieves an exact recipe and its family history. Only retained packages execute in the approved isolated runtime; other code and semantic/provider work stay in the native environment. Technical format checks do not prove semantic quality or exhaustive/exclusive branches. "
        "Local scanning, file transfers, credentials and repository execution use the CLI "
        "handoffs in the relevant resources/prompts. The server does not edit local files. "
        "When a user wants a visual view, open the ordinary Console for this project; "
        "opening a page does not authorize its write actions."
    ),
)


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


def _identify(_request, _extra) -> UserIdentity | None:
    clerk_user_id = get_context().user.clerk_user_id
    return UserIdentity(distinct_id=clerk_user_id) if clerk_user_id else None


# The low-level server passes injected analytics arguments through to the
# catalog, whose input models forbid extra fields.
ANALYTICS_OPTIONS = MCPAnalyticsOptions(
    context=False,
    enable_conversation_id=False,
    capture_model=False,
    identify=_identify,
)
posthog_client = (
    Posthog(settings.POSTHOG_PROJECT_TOKEN, host=settings.POSTHOG_HOST)
    if settings.POSTHOG_PROJECT_TOKEN
    else None
)
mcp_analytics = (
    instrument(mcp_server, posthog_client, ANALYTICS_OPTIONS) if posthog_client else None
)


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
        if mcp_analytics:
            await mcp_analytics.flush()
            posthog_client.shutdown()

    endpoint = _TransportEndpoint(manager)
    if mcp_analytics:
        endpoint = PostHogMcpStatelessSessionMiddleware(endpoint)

    app = Starlette(
        routes=[
            *oauth_routes(),
            Route("/.well-known/openai-apps-challenge", endpoint=domain_challenge, methods=["GET"]),
            Route(
                MCP_PATH,
                endpoint=endpoint,
                methods=["GET", "POST", "DELETE"],
            ),
        ],
        middleware=[
            Middleware(MCPAuthMiddleware),
            Middleware(MCPTransportMiddleware),
        ],
        lifespan=lifespan,
    )
    return app
