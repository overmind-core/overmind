import logging

from clerk_backend_api import AuthenticateRequestOptions, Clerk, authenticate_request
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import APIException, AuthenticationFailed

logger = logging.getLogger(__name__)

User = get_user_model()


class AuthServiceUnavailable(APIException):
    status_code = 503
    default_detail = "Sign-in service is temporarily unavailable. Try again shortly."
    default_code = "auth_unavailable"


class ClerkAuthentication(BaseAuthentication):
    """Validates Clerk JWTs, and on first sign-in creates or links a local User
    by the Clerk user's email address.
    """

    def authenticate(self, request):
        if not settings.CLERK_API_SECRET_KEY:
            return None
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None

        try:
            request_state = authenticate_request(
                request,
                AuthenticateRequestOptions(
                    secret_key=settings.CLERK_API_SECRET_KEY,
                    authorized_parties=settings.CLERK_AUTHORIZED_PARTIES,
                ),
            )
        except Exception:
            logger.exception("Clerk token verification failed")
            return None

        if not request_state.is_signed_in:
            return None

        clerk_user_id = request_state.payload["sub"]

        user = User.objects.filter(clerk_user_id=clerk_user_id).first()
        if user:
            return (user, request_state.payload)

        user = self._provision_user(clerk_user_id)
        return (user, request_state.payload)

    def authenticate_header(self, request):
        return "Bearer"

    def _provision_user(self, clerk_user_id: str):
        """Create or link a local User for a Clerk user ID. The token is already
        verified, and this runs on a new user's very first authenticated request,
        so failures must be explained rather than 500.
        """
        try:
            with Clerk(bearer_auth=settings.CLERK_API_SECRET_KEY) as clerk:
                clerk_user = clerk.users.get(user_id=clerk_user_id)
        except Exception as exc:
            logger.exception("Clerk user lookup failed for %s", clerk_user_id)
            raise AuthServiceUnavailable() from exc

        if not clerk_user or not clerk_user.email_addresses:
            raise AuthenticationFailed(
                "Your sign-in account has no email address. Contact support.",
                code="no_email",
            )
        email = clerk_user.email_addresses[0].email_address
        first_name = clerk_user.first_name or ""
        last_name = clerk_user.last_name or ""

        # Lazy import: overbae.models is not ready when this module loads.
        from overbae.services.project_invites import claim_pending_invites

        # iexact: a legacy account may differ from the Clerk address only in
        # case; an exact match would create a duplicate user.
        user = User.objects.filter(email__iexact=email).first()
        if user:
            user.clerk_user_id = clerk_user_id
            user.save(update_fields=["clerk_user_id"])
            claim_pending_invites(user)
            return user

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=email,
                    password=None,
                    clerk_user_id=clerk_user_id,
                    first_name=first_name,
                    last_name=last_name,
                )
        except IntegrityError:
            # Two first-requests raced; the other one won — use its row.
            user = (
                User.objects.filter(clerk_user_id=clerk_user_id).first()
                or User.objects.filter(email=email).first()
            )
            if user is None:
                raise
        claim_pending_invites(user)
        return user
