from __future__ import annotations

import hmac
from datetime import timedelta

from django.conf import settings
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


def _since(request):
    window = WindowSerializer(data=request.query_params)
    window.is_valid(raise_exception=True)
    return timezone.now() - timedelta(days=window.validated_data["days"])


class SalesDashboardView:
    authentication_classes = []
    permission_classes = [HasSalesDashboardToken]


@extend_schema(exclude=True)
class SalesOverviewView(SalesDashboardView, APIView):
    def get(self, request):
        data = sales_metrics.overview(_since(request), timezone.now() - timedelta(days=ACTIVE_DAYS))
        return Response(SalesOverviewSerializer(data).data)


@extend_schema(exclude=True)
class SalesFunnelView(SalesDashboardView, APIView):
    def get(self, request):
        return Response(SalesFunnelSerializer(sales_metrics.funnel(_since(request))).data)


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
