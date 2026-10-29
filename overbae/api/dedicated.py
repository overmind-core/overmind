import uuid

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_field,
    extend_schema_view,
)
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.config import PageNumberPagination
from overbae.api.scoping import project_ids_for
from overbae.core.errors import InputValidationError
from overbae.models import DedicatedDeployment, DeployedModel, OperationalRun
from overbae.services import dedicated, dedicated_progress
from overbae.services.dedicated import (
    DEFAULT_IDLE_SECONDS,
    MAX_IDLE_SECONDS,
    MAX_REPLICAS,
    MIN_IDLE_SECONDS,
)


class DedicatedGpuSerializer(serializers.Serializer):
    gpu_type = serializers.CharField()
    credits_per_gpu_hour = serializers.DecimalField(
        max_digits=12, decimal_places=2, allow_null=True
    )
    rates_fetched_at = serializers.CharField(allow_null=True)
    ttft_ms_p50 = serializers.FloatField(allow_null=True)
    tokens_per_second_p50 = serializers.FloatField(allow_null=True)
    latency_ms_p50 = serializers.FloatField(allow_null=True)


class DedicatedStartupLogSerializer(serializers.Serializer):
    stage = serializers.CharField()
    message = serializers.CharField()
    at = serializers.DateTimeField()
    worker = serializers.CharField()
    completed = serializers.FloatField(allow_null=True)
    total = serializers.FloatField(allow_null=True)
    unit = serializers.CharField(allow_null=True)


class DedicatedStartupSerializer(serializers.Serializer):
    operation_id = serializers.UUIDField()
    stage = serializers.CharField()
    label = serializers.CharField()
    completed = serializers.FloatField(allow_null=True)
    total = serializers.FloatField(allow_null=True)
    unit = serializers.CharField(allow_null=True)
    available = serializers.BooleanField()
    observed_at = serializers.DateTimeField(allow_null=True)
    logs = DedicatedStartupLogSerializer(many=True)
    truncated = serializers.BooleanField()


class DedicatedDeploymentListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        rows = list(data)
        runs = OperationalRun.objects.filter(
            kind="dedicated_deployment",
            reference__in=[str(row.pk) for row in rows],
            attempt__in=[row.probe_call_id for row in rows],
        )
        by_attempt = {(run.reference, run.attempt): run for run in runs}
        for row in rows:
            row._startup_operation = by_attempt.get((str(row.pk), row.probe_call_id))
        return super().to_representation(rows)


class DedicatedDeploymentSerializer(serializers.ModelSerializer):
    model_id = serializers.CharField(source="public_model_id")
    model_name = serializers.SerializerMethodField()
    startup = serializers.SerializerMethodField()

    class Meta:
        model = DedicatedDeployment
        list_serializer_class = DedicatedDeploymentListSerializer
        fields = [
            "id",
            "model_id",
            "model_name",
            "deployed_model",
            "gpu_type",
            "serving_shape",
            "status",
            "min_replicas",
            "max_replicas",
            "scale_to_zero",
            "idle_shutdown_seconds",
            "running_replicas",
            "credits_per_gpu_hour",
            "accrued_credits",
            "rates_fetched_at",
            "online_at",
            "stopped_at",
            "error_message",
            "startup",
        ]

    @extend_schema_field(DedicatedStartupSerializer(allow_null=True))
    def get_startup(self, obj):
        return dedicated_progress.summary(obj)

    def get_model_name(self, obj) -> str:
        job = obj.deployed_model.finetuning_job
        if job is not None and job.name:
            return job.name
        return obj.deployed_model.model_id


class DedicatedWriteSerializer(serializers.Serializer):
    gpu_type = serializers.CharField(required=False)
    min_replicas = serializers.IntegerField(min_value=0, max_value=MAX_REPLICAS, required=False)
    max_replicas = serializers.IntegerField(min_value=1, max_value=MAX_REPLICAS, required=False)
    scale_to_zero = serializers.BooleanField(required=False)
    idle_shutdown_seconds = serializers.IntegerField(
        min_value=MIN_IDLE_SECONDS, max_value=MAX_IDLE_SECONDS, required=False
    )


def _model(request, deployment_id) -> DeployedModel:
    return get_object_or_404(
        DeployedModel,
        pk=deployment_id,
        project_id__in=project_ids_for(request.user, request.auth),
    )


def _invalid(exc: InputValidationError):
    return Response({"detail": exc.detail}, status=400)


class DedicatedGpusView(APIView):
    @extend_schema(responses=DedicatedGpuSerializer(many=True), summary="Dedicated GPU options")
    def get(self, request, deployment_id):
        deployed = _model(request, deployment_id)
        return Response(DedicatedGpuSerializer(dedicated.gpu_offers(deployed), many=True).data)


class DedicatedDeploymentView(APIView):
    @extend_schema(responses=DedicatedDeploymentSerializer, summary="Current dedicated deployment")
    def get(self, request, deployment_id):
        deployed = _model(request, deployment_id)
        row = dedicated.latest_for(deployed)
        if row is None:
            return Response(None)
        return Response(DedicatedDeploymentSerializer(row).data)

    @extend_schema(
        request=DedicatedWriteSerializer,
        responses=DedicatedDeploymentSerializer,
        summary="Start a dedicated deployment",
    )
    def post(self, request, deployment_id):
        deployed = _model(request, deployment_id)
        body = DedicatedWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        if "gpu_type" not in data:
            return Response({"detail": "Choose a GPU."}, status=400)
        try:
            row = dedicated.start(
                deployed,
                request.user,
                gpu_type=data["gpu_type"],
                min_replicas=data.get("min_replicas", 1),
                max_replicas=data.get("max_replicas", 1),
                scale_to_zero=data.get("scale_to_zero", False),
                idle_shutdown_seconds=data.get("idle_shutdown_seconds", DEFAULT_IDLE_SECONDS),
            )
        except InputValidationError as exc:
            return _invalid(exc)
        return Response(DedicatedDeploymentSerializer(row).data, status=201)

    @extend_schema(
        request=DedicatedWriteSerializer,
        responses=DedicatedDeploymentSerializer,
        summary="Update dedicated replica bounds",
    )
    def patch(self, request, deployment_id):
        deployed = _model(request, deployment_id)
        row = dedicated.live_for(deployed)
        if row is None:
            return Response({"detail": "No dedicated deployment is running."}, status=404)
        body = DedicatedWriteSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        try:
            row = dedicated.update(row, **body.validated_data)
        except InputValidationError as exc:
            return _invalid(exc)
        return Response(DedicatedDeploymentSerializer(row).data)


class DedicatedStopView(APIView):
    @extend_schema(
        request=None, responses=DedicatedDeploymentSerializer, summary="Stop dedicated deployment"
    )
    def post(self, request, deployment_id):
        deployed = _model(request, deployment_id)
        row = dedicated.live_for(deployed)
        if row is None:
            return Response({"detail": "No dedicated deployment is running."}, status=404)
        return Response(DedicatedDeploymentSerializer(dedicated.stop(row)).data)


@extend_schema_view(
    get=extend_schema(
        summary="Dedicated deployments",
        parameters=[
            OpenApiParameter(name="project", type=uuid.UUID, location=OpenApiParameter.QUERY),
        ],
    )
)
class DedicatedDeploymentListView(ListAPIView):
    serializer_class = DedicatedDeploymentSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DedicatedDeployment.objects.none()
        rows = dedicated.latest_rows(project_ids_for(self.request.user, self.request.auth))
        project = self.request.query_params.get("project")
        if not project:
            return rows
        try:
            project_id = uuid.UUID(project)
        except ValueError:
            return DedicatedDeployment.objects.none()
        return rows.filter(project_id=project_id)
