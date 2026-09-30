import json
import re
from urllib.parse import urlsplit

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.routes import build_metadata, cors_middleware, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import RequestBodyLimitMiddleware
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from overbae.services.mcp.oauth import SCOPES, MCPOAuthProvider, resource_url


def oauth_error(error, description, status=400):
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@sync_to_async(thread_sensitive=True)
def within_rate_limit(request, bucket, limit):
    key = f"mcp-oauth:{bucket}:{request.client.host if request.client else 'unknown'}"
    cache.add(key, 0, timeout=60)
    return cache.incr(key) <= limit


def oauth_routes():
    if not settings.MCP_SERVER_URL:
        return []
    parsed = urlsplit(resource_url())
    if (
        (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
        or parsed.path != "/api/mcp/"
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ImproperlyConfigured(
            "MCP_SERVER_URL must be the HTTPS /api/mcp/ URL (HTTP loopback is allowed)."
        )
    issuer = AnyHttpUrl(f"{parsed.scheme}://{parsed.netloc}")
    provider = MCPOAuthProvider()
    registration = ClientRegistrationOptions(
        enabled=True, valid_scopes=SCOPES, default_scopes=["overmind:read"]
    )
    metadata = build_metadata(issuer, None, registration, RevocationOptions(enabled=True))
    metadata.token_endpoint_auth_methods_supported = ["none"]
    metadata.revocation_endpoint_auth_methods_supported = ["none"]
    authenticator = ClientAuthenticator(provider)

    async def register(request):
        if not await within_rate_limit(request, "register", 30):
            return oauth_error("invalid_request", "Too many registration requests.", 429)
        try:
            return await RegistrationHandler(provider, registration).handle(request)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return oauth_error(
                "invalid_client_metadata", "A JSON registration document is required."
            )

    async def authorize(request):
        if not await within_rate_limit(request, "authorize", 60):
            return oauth_error("invalid_request", "Too many authorization requests.", 429)
        params = request.query_params if request.method == "GET" else await request.form()
        if len(params) != len(params.multi_items()):
            return oauth_error(
                "invalid_request", "Repeated authorization parameters are not supported."
            )
        return await AuthorizationHandler(provider).handle(request)

    async def token(request):
        if not await within_rate_limit(request, "token", 120):
            return oauth_error("invalid_request", "Too many token requests.", 429)
        form = await request.form()
        if len(form) != len(form.multi_items()) or form.get("resource") != resource_url():
            return oauth_error(
                "invalid_request", "The resource must match this MCP server URL exactly."
            )
        if form.get("grant_type") == "authorization_code" and not re.fullmatch(
            r"[A-Za-z0-9._~-]{43,128}", str(form.get("code_verifier", ""))
        ):
            return oauth_error("invalid_request", "A valid PKCE verifier is required.")
        return await TokenHandler(provider, authenticator).handle(request)

    async def revoke(request):
        # The SDK revocation schema requires client_secret even for public clients.
        form = await request.form()
        client = await provider.get_client(str(form.get("client_id", "")))
        if client is None:
            return oauth_error("invalid_client", "Unknown client.", 401)
        if len(form) != len(form.multi_items()) or not isinstance(form.get("token"), str):
            return oauth_error("invalid_request", "A token is required.")
        loaded = await provider.load_access_token(form["token"])
        if loaded is None:
            loaded = await provider.load_refresh_token(client, form["token"])
        if loaded and loaded.client_id == client.client_id:
            await provider.revoke_token(loaded)
        return Response(status_code=200, headers={"Cache-Control": "no-store"})

    routes = create_protected_resource_routes(
        AnyHttpUrl(resource_url()), [issuer], SCOPES, "Overmind"
    )
    for path, handler, methods in [
        (
            "/.well-known/oauth-authorization-server",
            MetadataHandler(metadata).handle,
            ["GET", "OPTIONS"],
        ),
        ("/register", register, ["POST", "OPTIONS"]),
        ("/authorize", authorize, ["GET", "POST"]),
        ("/token", token, ["POST", "OPTIONS"]),
        ("/revoke", revoke, ["POST", "OPTIONS"]),
    ]:
        routes.append(
            Route(
                path,
                endpoint=RequestBodyLimitMiddleware(cors_middleware(handler, methods), 16384),
                methods=methods,
            )
        )
    return routes


async def domain_challenge(request):
    challenge = settings.OPENAI_APPS_CHALLENGE
    return PlainTextResponse(challenge, status_code=200 if challenge else 404)
