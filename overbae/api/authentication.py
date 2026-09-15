from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import SAFE_METHODS
from rest_framework_simplejwt.authentication import JWTAuthentication

from overbae.models import APIToken

GUEST_UPGRADE_CODE = "guest_upgrade_required"


class GuestUpgradeRequired(exceptions.PermissionDenied):
    default_detail = "Create an account to continue."
    default_code = GUEST_UPGRADE_CODE


class GuestJWTAuthentication(JWTAuthentication):
    """simplejwt, plus the guest gate: a guest may read, and write only to a view that
    sets ``guest_allowed = True``.

    Lives in the authenticator, not a permission class or middleware: a view's own
    ``permission_classes`` replaces the defaults, and a guest identity exists only
    through this token, so nothing can present one without passing here.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, _token = result
        if user.is_guest and request.method not in SAFE_METHODS:
            view = (request.parser_context or {}).get("view")
            if not getattr(view, "guest_allowed", False):
                raise GuestUpgradeRequired()
        return result


def _looks_like_jwt(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 3 and all(parts)


class APITokenBackend(BaseAuthentication):
    """``X-Api-Key: <key>`` or ``Authorization: Bearer <key>``.

    Must stay ahead of JWT in the auth class list: an unmatched JWT-shaped value
    returns None so simplejwt gets its turn.
    """

    def authenticate(self, request):
        raw = self._extract_key(request)
        if raw is None:
            return None

        digest = APIToken.hash_raw_key(raw)
        try:
            token = APIToken.objects.select_related("user").get(token_hash=digest)
        except APIToken.DoesNotExist:
            if _looks_like_jwt(raw):
                return None
            raise exceptions.AuthenticationFailed("Invalid API key.")  # noqa: B904

        if not token.is_active:
            raise exceptions.AuthenticationFailed("API key is inactive.")
        if token.expires_at and token.expires_at < timezone.now():
            raise exceptions.AuthenticationFailed("API key has expired.")
        if not token.user.is_active:
            raise exceptions.AuthenticationFailed("User inactive or deleted.")

        APIToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())

        return (token.user, token)

    def _extract_key(self, request) -> str | None:
        header: str = request.headers.get("X-Api-Key")
        if header:
            return header.strip() or None

        auth: str = request.headers.get("Authorization")
        if auth:
            parts = auth.split(None, 1)
            if len(parts) == 2 and parts[0].lower() in ("bearer", "api-key"):
                return parts[1].strip() or None

        return None
