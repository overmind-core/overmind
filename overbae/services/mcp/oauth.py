from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from rest_framework.exceptions import PermissionDenied, ValidationError

from overbae.models import MCPOAuthClient, MCPOAuthGrant, MCPOAuthToken

SCOPES = ["overmind:read", "overmind:write"]
ACCESS_SECONDS = 3600


def digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def resource_url():
    return settings.MCP_SERVER_URL


def active_tokens():
    return MCPOAuthToken.objects.select_related("grant__user").filter(
        grant__revoked_at__isnull=True,
        grant__user__is_active=True,
        grant__user__is_guest=False,
        grant__parameters__resource=resource_url(),
    )


def authenticate_access(raw):
    return (
        active_tokens()
        .filter(
            access_hash=digest(raw), access_expires_at__gt=timezone.now(), rotated_at__isnull=True
        )
        .first()
    )


def consent_request(raw, user):
    if not user.is_active or user.is_guest:
        raise PermissionDenied("Sign in to an Overmind account to connect.")
    with transaction.atomic():
        grant = (
            MCPOAuthGrant.objects.select_for_update()
            .select_related("client")
            .filter(
                Q(user__isnull=True) | Q(user=user),
                request_hash=digest(raw),
                decided_at__isnull=True,
                expires_at__gt=timezone.now(),
                revoked_at__isnull=True,
            )
            .first()
        )
        if grant is None:
            raise ValidationError("This connection request has expired or was already used.")
        if grant.user_id is None:
            grant.user = user
            grant.save(update_fields=["user"])
        return grant


def decide_consent(raw, user, approve):
    with transaction.atomic():
        grant = consent_request(raw, user)
        now = timezone.now()
        grant.decided_at = now
        params = grant.parameters
        query = {"state": params.get("state")} if params.get("state") is not None else {}
        if approve:
            code = secrets.token_urlsafe(48)
            grant.code_hash = digest(code)
            grant.expires_at = now + timedelta(minutes=2)
            query["code"] = code
        else:
            grant.revoked_at = now
            query["error"] = "access_denied"
        grant.save(update_fields=["decided_at", "code_hash", "expires_at", "revoked_at"])
        return construct_redirect_uri(params["redirect_uri"], **query)


def issue_tokens(grant, scopes):
    now = timezone.now()
    access = "om_oauth_" + secrets.token_urlsafe(48)
    refresh = secrets.token_urlsafe(48)
    MCPOAuthToken.objects.create(
        grant=grant,
        access_hash=digest(access),
        refresh_hash=digest(refresh),
        scopes=scopes,
        access_expires_at=now + timedelta(seconds=ACCESS_SECONDS),
    )
    return OAuthToken(
        access_token=access,
        refresh_token=refresh,
        expires_in=ACCESS_SECONDS,
        scope=" ".join(scopes),
    )


class MCPOAuthProvider:
    @sync_to_async(thread_sensitive=True)
    def get_client(self, client_id):
        client = MCPOAuthClient.objects.filter(pk=client_id).first()
        return OAuthClientInformationFull.model_validate(client.metadata) if client else None

    @sync_to_async(thread_sensitive=True)
    def register_client(self, client_info):
        if client_info.token_endpoint_auth_method != "none":
            raise RegistrationError(
                "invalid_client_metadata", "Use a public client with PKCE (auth method none)."
            )
        if not client_info.redirect_uris or len(client_info.redirect_uris) > 10:
            raise RegistrationError(
                "invalid_redirect_uri", "Register between one and ten redirect URIs."
            )
        for uri in client_info.redirect_uris:
            parsed = urlsplit(str(uri))
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if (
                (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
                or parsed.fragment
                or parsed.username
                or parsed.password
                or "*" in str(uri)
            ):
                raise RegistrationError(
                    "invalid_redirect_uri",
                    "Redirect URIs must use HTTPS or local loopback HTTP, without credentials, fragments or wildcards.",
                )
        MCPOAuthClient.objects.create(
            id=client_info.client_id, metadata=client_info.model_dump(mode="json")
        )

    @sync_to_async(thread_sensitive=True)
    def authorize(self, client, params):
        if params.resource != resource_url():
            raise AuthorizeError("invalid_request", "The resource must match this MCP server URL.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge):
            raise AuthorizeError("invalid_request", "A SHA-256 PKCE challenge is required.")
        scopes = params.scopes or ["overmind:read"]
        if not set(scopes).issubset(SCOPES):
            raise AuthorizeError("invalid_scope", "Unsupported scope.")
        raw = secrets.token_urlsafe(48)
        MCPOAuthGrant.objects.create(
            client_id=client.client_id,
            request_hash=digest(raw),
            parameters=params.model_dump(mode="json"),
            scopes=scopes,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        return settings.FRONTEND_URL.rstrip("/") + "/oauth/authorize?" + urlencode({"request": raw})

    @sync_to_async(thread_sensitive=True)
    def load_authorization_code(self, client, authorization_code):
        grant = MCPOAuthGrant.objects.filter(
            client_id=client.client_id,
            code_hash=digest(authorization_code),
            revoked_at__isnull=True,
            code_used_at__isnull=True,
            user__is_active=True,
            user__is_guest=False,
            parameters__resource=resource_url(),
        ).first()
        if grant is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            client_id=client.client_id,
            scopes=grant.scopes,
            expires_at=grant.expires_at.timestamp(),
            subject=str(grant.user_id),
            **{
                key: grant.parameters[key]
                for key in [
                    "code_challenge",
                    "redirect_uri",
                    "redirect_uri_provided_explicitly",
                    "resource",
                ]
            },
        )

    @sync_to_async(thread_sensitive=True)
    def exchange_authorization_code(self, client, authorization_code):
        with transaction.atomic():
            grant = (
                MCPOAuthGrant.objects.select_for_update()
                .filter(
                    client_id=client.client_id,
                    code_hash=digest(authorization_code.code),
                    revoked_at__isnull=True,
                    code_used_at__isnull=True,
                    expires_at__gt=timezone.now(),
                    user__is_active=True,
                    user__is_guest=False,
                    parameters__resource=resource_url(),
                )
                .first()
            )
            if grant is None:
                raise TokenError("invalid_grant", "The authorization code is no longer valid.")
            grant.code_used_at = timezone.now()
            grant.save(update_fields=["code_used_at"])
            return issue_tokens(grant, grant.scopes)

    @sync_to_async(thread_sensitive=True)
    def load_refresh_token(self, client, refresh_token):
        token = (
            active_tokens()
            .filter(grant__client_id=client.client_id, refresh_hash=digest(refresh_token))
            .first()
        )
        if token is None:
            return None
        if token.rotated_at:
            MCPOAuthGrant.objects.filter(pk=token.grant_id).update(revoked_at=timezone.now())
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=token.scopes,
            expires_at=None,
            resource=resource_url(),
            subject=str(token.grant.user_id),
        )

    @sync_to_async(thread_sensitive=True)
    def exchange_refresh_token(self, client, refresh_token, scopes):
        result = None
        with transaction.atomic():
            token = (
                active_tokens()
                .select_for_update()
                .filter(
                    grant__client_id=client.client_id,
                    refresh_hash=digest(refresh_token.token),
                )
                .first()
            )
            if token and token.rotated_at:
                MCPOAuthGrant.objects.filter(pk=token.grant_id).update(revoked_at=timezone.now())
            elif token and set(scopes).issubset(token.scopes):
                token.rotated_at = timezone.now()
                token.save(update_fields=["rotated_at"])
                result = issue_tokens(token.grant, scopes)
        # Commit replay revocation even though the token response is an error.
        if result is None:
            raise TokenError("invalid_grant", "The refresh token is no longer valid.")
        return result

    @sync_to_async(thread_sensitive=True)
    def load_access_token(self, raw):
        token = authenticate_access(raw)
        if token is None:
            return None
        return AccessToken(
            token=raw,
            client_id=token.grant.client_id,
            scopes=token.scopes,
            expires_at=int(token.access_expires_at.timestamp()),
            resource=resource_url(),
            subject=str(token.grant.user_id),
        )

    @sync_to_async(thread_sensitive=True)
    def revoke_token(self, token):
        token_hash = digest(token.token)
        grants = MCPOAuthToken.objects.filter(
            Q(access_hash=token_hash) | Q(refresh_hash=token_hash), grant__client_id=token.client_id
        ).values_list("grant_id", flat=True)
        MCPOAuthGrant.objects.filter(pk__in=grants).update(revoked_at=timezone.now())
