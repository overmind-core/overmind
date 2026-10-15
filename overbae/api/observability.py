from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.scoping import project_ids_for
from overbae.models import Project
from overbae.services.observability_overview import overview


class OverviewQuerySerializer(serializers.Serializer):
    project = serializers.UUIDField()
    days = serializers.ChoiceField(choices=(7, 30, 90), default=7)


class DailyOverviewSerializer(serializers.Serializer):
    day = serializers.DateField()
    runs = serializers.IntegerField()
    error_runs = serializers.IntegerField()
    checks = serializers.IntegerField()
    passed_checks = serializers.IntegerField()
    p95_duration_ms = serializers.FloatField(allow_null=True)
    average_cost_usd = serializers.FloatField(allow_null=True)


class OverviewIssueSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("check", "signal"))
    name = serializers.CharField()
    affected_runs = serializers.IntegerField(allow_null=True)
    occurrences = serializers.IntegerField()
    errors = serializers.IntegerField()
    examples = serializers.ListField(child=serializers.CharField())


class OverviewCapabilitySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    runs = serializers.IntegerField()
    error_runs = serializers.IntegerField()
    checks = serializers.IntegerField()
    failed_checks = serializers.IntegerField()


class OverviewModelSerializer(serializers.Serializer):
    name = serializers.CharField()
    calls = serializers.IntegerField()
    error_calls = serializers.IntegerField()
    average_duration_ms = serializers.FloatField(allow_null=True)
    recorded_cost_usd = serializers.FloatField(allow_null=True)
    example_trace_id = serializers.CharField()


class OverviewToolSerializer(serializers.Serializer):
    name = serializers.CharField()
    calls = serializers.IntegerField()
    error_calls = serializers.IntegerField()
    average_duration_ms = serializers.FloatField(allow_null=True)
    example_trace_id = serializers.CharField()


class ObservabilityOverviewSerializer(serializers.Serializer):
    window_days = serializers.IntegerField()
    span_count = serializers.IntegerField()
    sampled_spans = serializers.IntegerField()
    truncated = serializers.BooleanField()
    latest_received_at = serializers.DateTimeField(allow_null=True)
    runs = serializers.IntegerField()
    sessions = serializers.IntegerField()
    error_runs = serializers.IntegerField()
    checks = serializers.IntegerField()
    failed_checks = serializers.IntegerField()
    check_pass_rate = serializers.FloatField(allow_null=True)
    scored_spans = serializers.IntegerField()
    final_spans = serializers.IntegerField()
    p95_duration_ms = serializers.FloatField(allow_null=True)
    measured_duration_runs = serializers.IntegerField()
    average_cost_usd = serializers.FloatField(allow_null=True)
    measured_cost_runs = serializers.IntegerField()
    daily = DailyOverviewSerializer(many=True)
    issues = OverviewIssueSerializer(many=True)
    capabilities = OverviewCapabilitySerializer(many=True)
    models = OverviewModelSerializer(many=True)
    tools = OverviewToolSerializer(many=True)


class ObservabilityOverviewView(APIView):
    @extend_schema(
        summary="Summarise observed project behaviour",
        parameters=[OverviewQuerySerializer],
        responses={200: ObservabilityOverviewSerializer},
    )
    def get(self, request):
        query = OverviewQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        project = get_object_or_404(
            Project,
            id=query.validated_data["project"],
            id__in=project_ids_for(request.user, request.auth),
        )
        return Response(
            ObservabilityOverviewSerializer(overview(project, query.validated_data["days"])).data
        )
