from django.db import transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_field
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.api.serializers import UserOnboardingSerializer
from overbae.models import Subscription, User, UserOnboarding


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8, max_length=128)
    password_confirm = serializers.CharField(write_only=True, min_length=8, max_length=128)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value.strip()).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value.strip().lower()

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm", None)
        password = validated_data.pop("password")
        with transaction.atomic():
            user = User.objects.create_user(
                email=validated_data["email"],
                password=password,
                email_verified=True,
                is_active=True,
            )
            return user


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


@extend_schema(
    summary="Register",
    description="Create an account with email and password.",
    request=RegisterSerializer,
    responses={
        201: OpenApiResponse(
            response=AuthTokensResponseSerializer, description="JWT pair and user profile"
        )
    },
)
class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserMeSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
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
