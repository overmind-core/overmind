from __future__ import annotations

import hmac
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.config import PageNumberPagination
from overbae.api.ordering import TableOrderingFilter
from overbae.services import sales_metrics

ACTIVE_DAYS = 7
CACHE_SECONDS = 3600


class HasSalesDashboardToken(BasePermission):
    def has_permission(self, request, view) -> bool:
        expected = settings.SALES_DASHBOARD_TOKEN
        scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
        return bool(expected) and scheme == "Bearer" and hmac.compare_digest(presented, expected)


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


class SalesOverviewSerializer(serializers.Serializer):
    accounts_total = serializers.IntegerField()
    signups = DailyCountSerializer(many=True)
    sign_on_methods = serializers.DictField(child=serializers.IntegerField())
    projects_created = DailyCountSerializer(many=True)
    active_accounts_7d = serializers.IntegerField()
    active_accounts_window = serializers.IntegerField()
    subscriptions = serializers.DictField(child=serializers.IntegerField())
    paying_accounts = serializers.IntegerField()
    cancelling_accounts = serializers.IntegerField()
    free_accounts = serializers.IntegerField()
    low_balance_accounts = serializers.IntegerField()
    topups_usd = DailyUsdSerializer(many=True)
    spend_by_service = serializers.DictField(
        child=serializers.DecimalField(max_digits=14, decimal_places=7)
    )
    spans = DailyCountSerializer(many=True)
    traces = DailyCountSerializer(many=True)
    training_jobs = serializers.DictField(child=serializers.IntegerField())
    top_base_models = BaseModelUsageSerializer(many=True)
    live_deployments = serializers.IntegerField()
    inference_calls = DailyCountSerializer(many=True)


class FunnelStepSerializer(serializers.Serializer):
    step = serializers.CharField()
    accounts = serializers.IntegerField()


class SalesFunnelSerializer(serializers.Serializer):
    steps = FunnelStepSerializer(many=True)
    median_hours_to_first_span = serializers.FloatField(allow_null=True)


class SalesAccountSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    date_joined = serializers.DateTimeField()
    sign_on_method = serializers.CharField()
    subscription_status = serializers.CharField(allow_null=True)
    projects = serializers.IntegerField()
    seats = serializers.IntegerField()
    last_span_at = serializers.DateTimeField(allow_null=True)
    spans = serializers.IntegerField()
    spend_usd = serializers.DecimalField(max_digits=14, decimal_places=7)
    topups_usd = serializers.DecimalField(max_digits=14, decimal_places=7)
    balance_usd = serializers.DecimalField(max_digits=14, decimal_places=7)
    training_jobs = serializers.IntegerField()
    live_deployments = serializers.IntegerField()
    stage = serializers.SerializerMethodField()

    def get_stage(self, account) -> str:
        return sales_metrics.stage(account)


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


class SalesAccountActivitySerializer(serializers.Serializer):
    account = SalesAccountSerializer()
    training = TrainingActivitySerializer()
    traces = TraceActivitySerializer()
    inference = InferenceActivitySerializer()
    datasets = DatasetActivitySerializer()
    optimizer = WorkflowActivitySerializer()
    evaluations = WorkflowActivitySerializer()


def _since(request):
    window = WindowSerializer(data=request.query_params)
    window.is_valid(raise_exception=True)
    return timezone.now() - timedelta(days=window.validated_data["days"])


def _cached(request, compute) -> Response:
    """Serve one computation per path and query for an hour, stamped with its freshness."""
    key = f"sales-dashboard:{request.path}?{urlencode(sorted(request.query_params.items()))}"
    payload = cache.get(key)
    if payload is None:
        computed_at = timezone.now()
        payload = {
            **compute(),
            "computed_at": computed_at.isoformat(),
            "refreshes_at": (computed_at + timedelta(seconds=CACHE_SECONDS)).isoformat(),
        }
        cache.set(key, payload, CACHE_SECONDS)
    return Response(payload)


class SalesDashboardView:
    authentication_classes = []
    permission_classes = [HasSalesDashboardToken]


@extend_schema(exclude=True)
class SalesOverviewView(SalesDashboardView, APIView):
    def get(self, request):
        def compute():
            data = sales_metrics.overview(
                _since(request), timezone.now() - timedelta(days=ACTIVE_DAYS)
            )
            return SalesOverviewSerializer(data).data

        return _cached(request, compute)


@extend_schema(exclude=True)
class SalesFunnelView(SalesDashboardView, APIView):
    def get(self, request):
        return _cached(
            request, lambda: SalesFunnelSerializer(sales_metrics.funnel(_since(request))).data
        )


@extend_schema(exclude=True)
class SalesAccountActivityView(SalesDashboardView, APIView):
    def get(self, request, pk: int):
        def compute():
            since = _since(request)
            account = get_object_or_404(sales_metrics.accounts(since), pk=pk)
            return SalesAccountActivitySerializer(
                sales_metrics.account_activity(account, since)
            ).data

        return _cached(request, compute)


@extend_schema(exclude=True)
class SalesAccountsView(SalesDashboardView, generics.ListAPIView):
    serializer_class = SalesAccountSerializer
    pagination_class = PageNumberPagination
    filter_backends = [TableOrderingFilter]
    ordering_fields = [
        "date_joined",
        "email",
        "projects",
        "seats",
        "last_span_at",
        "spans",
        "spend_usd",
        "topups_usd",
        "balance_usd",
        "training_jobs",
        "live_deployments",
    ]
    ordering = ["-date_joined"]

    def get_queryset(self):
        return sales_metrics.accounts(_since(self.request))

    def list(self, request, *args, **kwargs):
        return _cached(request, lambda: super(SalesAccountsView, self).list(request).data)
