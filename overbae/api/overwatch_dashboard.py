from __future__ import annotations

import hashlib
import hmac
from contextlib import nullcontext
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.config import PageNumberPagination
from overbae.api.ordering import TableOrderingFilter
from overbae.services import overwatch_insights, user_insights

ACTIVE_DAYS = 7
CACHE_SECONDS = 3600


class HasOverwatchToken(BasePermission):
    def has_permission(self, request, view) -> bool:
        expected = settings.SALES_DASHBOARD_TOKEN
        scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
        return (
            bool(expected)
            and scheme == "Bearer"
            and hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
        )


class WindowSerializer(serializers.Serializer):
    days = serializers.IntegerField(min_value=1, max_value=365, default=30)


class DailyCountSerializer(serializers.Serializer):
    day = serializers.DateTimeField()
    value = serializers.IntegerField()


class DailyUsdSerializer(serializers.Serializer):
    day = serializers.DateTimeField()
    value = serializers.DecimalField(max_digits=14, decimal_places=7)


class BaseModelUsageSerializer(serializers.Serializer):
    base_model = serializers.CharField()
    jobs = serializers.IntegerField()


class OverviewSerializer(serializers.Serializer):
    users_total = serializers.IntegerField()
    signups = DailyCountSerializer(many=True)
    sign_on_methods = serializers.DictField(child=serializers.IntegerField())
    projects_created = DailyCountSerializer(many=True)
    trace_active_users_7d = serializers.IntegerField()
    trace_active_users_window = serializers.IntegerField()
    spans = DailyCountSerializer(many=True)
    traces = DailyCountSerializer(many=True)
    training_jobs = serializers.DictField(child=serializers.IntegerField())
    top_base_models = BaseModelUsageSerializer(many=True)
    live_deployments = serializers.IntegerField()
    inference_calls = DailyCountSerializer(many=True)
    subscriptions = serializers.DictField(child=serializers.IntegerField())
    paying_users = serializers.IntegerField()
    cancelling_users = serializers.IntegerField()
    free_users = serializers.IntegerField()
    low_balance_users = serializers.IntegerField()
    topups_usd = DailyUsdSerializer(many=True)
    spend_usd = DailyUsdSerializer(many=True)
    spend_by_service = serializers.DictField(
        child=serializers.DecimalField(max_digits=14, decimal_places=7)
    )


class ActivationStepSerializer(serializers.Serializer):
    step = serializers.CharField()
    users = serializers.IntegerField()


class ActivationSerializer(serializers.Serializer):
    steps = ActivationStepSerializer(many=True)
    median_hours_to_first_span = serializers.FloatField(allow_null=True)


class ProductUsageSerializer(serializers.Serializer):
    available = serializers.BooleanField()
    reason = serializers.CharField(required=False)
    identity_basis = serializers.CharField(required=False)
    window_start = serializers.DateTimeField(required=False)
    window_end = serializers.DateTimeField(required=False)
    active_users = serializers.IntegerField(required=False)
    totals = serializers.DictField(child=serializers.IntegerField(), required=False)
    daily = serializers.ListField(child=serializers.DictField(), required=False)
    top_mcp_tools = serializers.ListField(child=serializers.DictField(), required=False)
    top_cli_commands = serializers.ListField(child=serializers.DictField(), required=False)
    mcp_tool_errors = serializers.IntegerField(required=False)
    mcp_tool_error_rate = serializers.FloatField(required=False)


class UserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    date_joined = serializers.DateTimeField()
    sign_on_method = serializers.CharField()
    projects = serializers.IntegerField()
    seats = serializers.IntegerField()
    last_span_at = serializers.DateTimeField(allow_null=True)
    spans = serializers.IntegerField()
    training_jobs = serializers.IntegerField()
    live_deployments = serializers.IntegerField()
    stage = serializers.SerializerMethodField()
    subscription_status = serializers.CharField(allow_null=True)
    spend_usd = serializers.DecimalField(max_digits=14, decimal_places=7)
    topups_usd = serializers.DecimalField(max_digits=14, decimal_places=7)
    balance_usd = serializers.DecimalField(max_digits=14, decimal_places=7)

    def get_stage(self, account) -> str:
        return user_insights.stage(account)


class DailyBreakdownSerializer(serializers.Serializer):
    day = serializers.DateTimeField()
    values = serializers.DictField(child=serializers.IntegerField())


class TrainingJobSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    base_model = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)


class TrainingTotalsSerializer(serializers.Serializer):
    started = serializers.IntegerField()
    completed = serializers.IntegerField()
    failed = serializers.IntegerField()
    cancelled = serializers.IntegerField()
    in_progress = serializers.IntegerField()


class TrainingActivitySerializer(serializers.Serializer):
    totals = TrainingTotalsSerializer()
    daily = DailyBreakdownSerializer(many=True)
    recent = TrainingJobSummarySerializer(many=True)


class TraceActivitySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    daily = DailyCountSerializer(many=True)


class InferenceActivitySerializer(serializers.Serializer):
    calls = serializers.IntegerField()
    models = serializers.IntegerField()
    by_model = serializers.DictField(child=serializers.IntegerField())
    daily = DailyCountSerializer(many=True)


class DatasetActivitySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    by_source = serializers.DictField(child=serializers.IntegerField())
    by_intent = serializers.DictField(child=serializers.IntegerField())
    daily = DailyCountSerializer(many=True)


class WorkflowActivitySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    by_status = serializers.DictField(child=serializers.IntegerField())
    daily = DailyCountSerializer(many=True)


class BillingActivitySerializer(serializers.Serializer):
    subscription_status = serializers.CharField(allow_null=True)
    spend_usd = serializers.DecimalField(max_digits=14, decimal_places=7)
    topups_usd = serializers.DecimalField(max_digits=14, decimal_places=7)
    balance_usd = serializers.DecimalField(max_digits=14, decimal_places=7)
    spend_daily = DailyUsdSerializer(many=True)
    topups_daily = DailyUsdSerializer(many=True)


class UserActivitySerializer(serializers.Serializer):
    user = UserSerializer()
    billing = BillingActivitySerializer()
    training = TrainingActivitySerializer()
    traces = TraceActivitySerializer()
    inference = InferenceActivitySerializer()
    datasets = DatasetActivitySerializer()
    optimizer = WorkflowActivitySerializer()
    evaluations = WorkflowActivitySerializer()


def _since(request):
    window = WindowSerializer(data=request.query_params)
    window.is_valid(raise_exception=True)
    return user_insights.window_start(window.validated_data["days"])


def _cached(request, compute, *, database: bool = True) -> Response:
    """Serve one computation per path and query for an hour, stamped with its freshness."""
    # Hash untrusted query strings to keep cache keys bounded. Never cache a token.
    fingerprint = hashlib.sha256(
        f"{request.path}?{urlencode(sorted(request.query_params.items()))}".encode()
    ).hexdigest()
    key = f"overwatch:v2:{fingerprint}"
    payload = cache.get(key)
    if payload is None:
        computed_at = timezone.now()
        try:
            with user_insights.analytics_read_budget() if database else nullcontext():
                data = compute()
        except (DatabaseError, TimeoutError):
            response = Response({"detail": "Analytics are temporarily unavailable."}, status=503)
            response["Cache-Control"] = "private, no-store"
            return response
        ttl = 60 if data.get("available") is False else CACHE_SECONDS
        payload = {
            **data,
            "computed_at": computed_at.isoformat(),
            "refreshes_at": (computed_at + timedelta(seconds=ttl)).isoformat(),
        }
        cache.set(key, payload, ttl)
    response = Response(payload)
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


class OverwatchView:
    authentication_classes = []
    permission_classes = [HasOverwatchToken]


@extend_schema(exclude=True)
class OverviewView(OverwatchView, APIView):
    def get(self, request):
        def compute():
            data = user_insights.overview(_since(request), user_insights.window_start(ACTIVE_DAYS))
            return OverviewSerializer(data).data

        return _cached(request, compute)


@extend_schema(exclude=True)
class ActivationView(OverwatchView, APIView):
    def get(self, request):
        return _cached(
            request, lambda: ActivationSerializer(user_insights.activation(_since(request))).data
        )


@extend_schema(exclude=True)
class ProductUsageView(OverwatchView, APIView):
    def get(self, request):
        window = WindowSerializer(data=request.query_params)
        window.is_valid(raise_exception=True)
        days = window.validated_data["days"]
        return _cached(
            request,
            lambda: ProductUsageSerializer(overwatch_insights.product_usage(days)).data,
            database=False,
        )


@extend_schema(exclude=True)
class UserActivityView(OverwatchView, APIView):
    def get(self, request, pk: int):
        def compute():
            since = _since(request)
            user = get_object_or_404(user_insights.users(since), pk=pk)
            return UserActivitySerializer(user_insights.user_activity(user, since)).data

        return _cached(request, compute)


@extend_schema(exclude=True)
class UsersView(OverwatchView, generics.ListAPIView):
    serializer_class = UserSerializer
    pagination_class = PageNumberPagination
    filter_backends = [TableOrderingFilter]
    ordering_fields = [
        "date_joined",
        "email",
        "projects",
        "seats",
        "last_span_at",
        "spans",
        "training_jobs",
        "live_deployments",
        "subscription_status",
        "spend_usd",
        "topups_usd",
        "balance_usd",
    ]
    ordering = ["-date_joined"]

    def get_queryset(self):
        return user_insights.users(_since(self.request))

    def list(self, request, *args, **kwargs):
        return _cached(request, lambda: super(UsersView, self).list(request).data)
