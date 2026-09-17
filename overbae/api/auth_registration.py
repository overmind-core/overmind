import uuid

from django.db import transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_field
from rest_framework import serializers, status
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.api.serializers import UserOnboardingSerializer
from overbae.auth import clerk_enabled
from overbae.models import SignOnMethod, Subscription, User, UserOnboarding


class ClerkRequired(PermissionDenied):
    default_detail = "Local sign-in is only available when Clerk is disabled."
    default_code = "clerk_required"


class InvalidCredentials(APIException):
    # Not AuthenticationFailed: with authentication_classes=[] DRF rewrites that to 403.
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Invalid email or password."
    default_code = "authentication_failed"


class LocalSessionSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8, max_length=128)

    def validate_email(self, value):
        return value.strip().lower()


class UserMeSerializer(serializers.ModelSerializer):
    has_completed_onboarding = serializers.SerializerMethodField()
    billing_enabled = serializers.SerializerMethodField()
    plan = serializers.SerializerMethodField()
    credits_usd = serializers.SerializerMethodField()
    subscription_end = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "clerk_user_id",
            "email",
            "email_verified",
            "has_completed_onboarding",
            "is_guest",
            "billing_enabled",
            "plan",
            "credits_usd",
            "subscription_end",
        ]
        extra_kwargs = {
            "is_guest": {"read_only": True},
            # Opaque Clerk id — same distinct_id the Console uses in PostHog.
            "clerk_user_id": {"read_only": True},
        }

    def get_has_completed_onboarding(self, user: User) -> bool:
        onboarding = getattr(user, "onboarding", None)
        if onboarding and onboarding.status == "completed":
            return True
        # The wizard sets "in_progress" on mount and creates a project in step
        # one, so the membership shortcut below would flag it complete mid-wizard.
        if onboarding and onboarding.status == "in_progress":
            return False
        # Membership (invited, provisioned, seeded) IS the completed state —
        # otherwise an account that can already see workspaces is sent through
        # the wizard.
        return user.project_memberships.exists()

    def get_billing_enabled(self, user: User) -> bool:
        from overbae.services.billing_provider import get_billing

        return get_billing().enabled

    def get_plan(self, user: User) -> str:
        from overbae.services.plan_limits import is_pro

        return "pro" if is_pro(user) else "free"

    @extend_schema_field(serializers.DecimalField(max_digits=12, decimal_places=4))
    def get_credits_usd(self, user: User):
        from overbae.services.billing_ledger import balance_usd

        return balance_usd(user)

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_subscription_end(self, user: User):
        try:
            return user.subscription.end_date
        except Subscription.DoesNotExist:
            return None


class AuthTokensResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserMeSerializer()


def _tokens_for(user: User) -> dict:
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": UserMeSerializer(user).data,
    }


def local_session(*, email: str, password: str) -> tuple[User, bool]:
    """Sign in an existing password account, or create one on first use.

    Returns ``(user, created)``. Refuses when Clerk is configured so the hosted
    product cannot be bypassed through this path.
    """
    if clerk_enabled():
        raise ClerkRequired()

    # Lazy import: overbae.models is not ready when some callers load this module.
    from overbae.services.project_invites import claim_pending_invites

    user = User.objects.filter(email__iexact=email).first()
    if user is not None:
        if user.is_guest or not user.is_active or not user.has_usable_password():
            raise InvalidCredentials()
        if not user.check_password(password):
            raise InvalidCredentials()
        return user, False

    with transaction.atomic():
        user = User.objects.create_user(
            email=email,
            password=password,
            email_verified=True,
            is_active=True,
            sign_on_method=SignOnMethod.PASSWORD,
            clerk_user_id=f"local_{uuid.uuid4().hex}",
        )
    claim_pending_invites(user)
    return user, True


@extend_schema(
    summary="Local sign-in",
    description=(
        "Email and password session for self-hosted deployments without Clerk. "
        "Creates the account on first use; no email verification."
    ),
    request=LocalSessionSerializer,
    responses={
        200: OpenApiResponse(
            response=AuthTokensResponseSerializer, description="JWT pair and user profile"
        ),
        201: OpenApiResponse(
            response=AuthTokensResponseSerializer, description="Account created and signed in"
        ),
    },
)
class LocalSessionView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LocalSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, created = local_session(
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        return Response(
            _tokens_for(user),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(
    summary="Current user profile",
    description="Returns the authenticated user's profile.",
    responses={200: UserMeSerializer},
)
class UserMeView(APIView):
    def get(self, request):
        return Response(UserMeSerializer(request.user).data)


@extend_schema(
    summary="User onboarding",
    description="Retrieve or update the authenticated user's onboarding record.",
    request=UserOnboardingSerializer,
    responses={200: UserOnboardingSerializer},
)
class OnboardingView(APIView):
    def get(self, request) -> Response:
        onboarding, _ = UserOnboarding.objects.get_or_create(user=request.user)
        return Response(UserOnboardingSerializer(onboarding).data)

    def patch(self, request) -> Response:
        onboarding, _ = UserOnboarding.objects.get_or_create(user=request.user)
        serializer = UserOnboardingSerializer(onboarding, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
