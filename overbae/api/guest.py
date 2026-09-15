"""Guest sessions: create an SDK project, then sign in with Clerk to keep the workspace."""

from __future__ import annotations

import logging
import uuid

import httpx
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.api.auth_registration import UserMeSerializer
from overbae.auth import ClerkAuthentication
from overbae.models import IntegrationType, Project, ProjectMembership, User, UserOnboarding

logger = logging.getLogger(__name__)


class GuestStartThrottle(AnonRateThrottle):
    rate = "5/hour"
    scope = "guest_start"


class GuestSessionSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    project_id = serializers.UUIDField()
    user = UserMeSerializer()


class GuestClaimSerializer(serializers.Serializer):
    clerk_token = serializers.CharField()


class GuestClaimResultSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    user = UserMeSerializer()


def start_guest_session() -> tuple[User, Project, str, str]:
    suffix = uuid.uuid4().hex
    with transaction.atomic():
        user = User.objects.create_user(
            email=f"guest-{suffix}@guest.invalid",
            password=None,
            clerk_user_id=f"guest_{suffix}",
            is_guest=True,
            projects_limit=1,
        )
        project = Project.objects.create(
            name="My product",
            slug=f"guest-{suffix[:12]}",
            integration_type=IntegrationType.SDK,
        )
        ProjectMembership.objects.create(user=user, project=project)
        UserOnboarding.objects.create(user=user, status="completed")
    refresh = RefreshToken.for_user(user)
    logger.info(
        "guest_started",
        extra={"event": "guest_started", "user_id": str(user.pk), "project_id": str(project.id)},
    )
    return user, project, str(refresh.access_token), str(refresh)


def claim_guest(*, guest: User, clerk_token: str) -> tuple[User, Project]:
    """Move the guest's workspace to the Clerk account behind ``clerk_token``.

    The guest row is deactivated, never converted: the Clerk account may already
    exist, and the daily sweep removes inactive guests. Memberships are the only
    guest-owned rows — the gate blocks every other write — and ``projects_limit``
    gates project creation only, so a claim never fails on it.
    """
    if not guest.is_guest:
        raise PermissionDenied("This session is already an account.")
    probe = httpx.Request(
        "GET", "https://clerk.invalid/", headers={"Authorization": f"Bearer {clerk_token}"}
    )
    result = ClerkAuthentication().authenticate(probe)
    if result is None:
        raise AuthenticationFailed("Invalid sign-in token.")
    owner, _payload = result
    if not owner.is_active:
        raise AuthenticationFailed("This account is inactive.")
    with transaction.atomic():
        memberships = list(ProjectMembership.objects.filter(user=guest).select_related("project"))
        if not memberships:
            raise PermissionDenied("No workspace to claim.")
        for membership in memberships:
            ProjectMembership.objects.get_or_create(user=owner, project=membership.project)
        ProjectMembership.objects.filter(user=guest).delete()
        guest.is_active = False
        guest.save(update_fields=["is_active"])
    project = memberships[0].project
    logger.info(
        "guest_claimed",
        extra={
            "event": "guest_claimed",
            "guest_id": str(guest.pk),
            "user_id": str(owner.pk),
            "project_id": str(project.id),
        },
    )
    return owner, project


class GuestStartView(APIView):
    # Anonymous by construction, so the per-address throttle applies to every caller.
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [GuestStartThrottle]

    @extend_schema(
        summary="Start a guest session",
        request=None,
        responses={201: GuestSessionSerializer},
    )
    def post(self, request):  # noqa: ARG002
        user, project, access, refresh = start_guest_session()
        return Response(
            GuestSessionSerializer(
                {"access": access, "refresh": refresh, "project_id": project.id, "user": user}
            ).data,
            status=status.HTTP_201_CREATED,
        )


class GuestClaimView(APIView):
    guest_allowed = True

    @extend_schema(
        summary="Claim a guest workspace with a Clerk account",
        request=GuestClaimSerializer,
        responses={200: GuestClaimResultSerializer},
    )
    def post(self, request):
        if not getattr(request.user, "is_guest", False):
            raise PermissionDenied("This session is already an account.")
        ser = GuestClaimSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        owner, project = claim_guest(
            guest=request.user, clerk_token=ser.validated_data["clerk_token"]
        )
        return Response(GuestClaimResultSerializer({"project_id": project.id, "user": owner}).data)
