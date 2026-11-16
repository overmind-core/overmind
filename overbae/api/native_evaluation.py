import logging

from django.db.models import Prefetch
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.response import Response

from overbae.api.credit_gate import require_credits
from overbae.api.scoping import project_ids_for
from overbae.core.errors import InputValidationError
from overbae.models import Cell, FinetuningJob, NativeEvaluationPlan, Project
from overbae.services.datasets import use
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.decision_providers import catalog
from overbae.services.native_evaluation import (
    create_plan,
    delete,
    directory,
    launch,
    pause,
    prepare,
    resume,
    reuse_predictions,
)
from overbae.services.native_evaluation_monitor import inspect
from overbae.tasks.native_evaluation import advance_plan

logger = logging.getLogger(__name__)


class EvaluationReportNegotiation(DefaultContentNegotiation):
    def select_renderer(self, request, renderers, format_suffix=None):
        # The report's format selects a file, not DRF's response renderer.
        return super().select_renderer(request, renderers, format_suffix="json")


class NativeEvaluationRequestSerializer(serializers.Serializer):
    calibration_cell = serializers.UUIDField()
    final_cell = serializers.UUIDField()


class NativeEvaluationSuiteSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    version = serializers.CharField()
    title = serializers.CharField()
    rows = serializers.IntegerField()
    dataset_id = serializers.UUIDField()
    dataset_name = serializers.CharField()
    capability_id = serializers.UUIDField(allow_null=True)
    capability_name = serializers.CharField()

    def to_representation(self, cell):
        dataset = cell.dataset
        return super().to_representation(
            {
                **use.describe(cell, chain=dataset.prefetched_chain()),
                "dataset_name": dataset.name,
                "capability_id": dataset.capability_id,
                "capability_name": dataset.capability.name if dataset.capability_id else "",
            }
        )


class NativeEvaluationSerializer(serializers.ModelSerializer):
    final_suite = NativeEvaluationSuiteSerializer(source="final_cell", read_only=True)
    calibration_suite = NativeEvaluationSuiteSerializer(
        source="calibration_cell", read_only=True, allow_null=True
    )

    class Meta:
        model = NativeEvaluationPlan
        fields = [
            "id",
            "project",
            "name",
            "request_key",
            "final_cell",
            "calibration_cell",
            "final_suite",
            "calibration_suite",
            "job",
            "state",
            "config",
            "calls",
            "calibration",
            "results",
            "error",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DecisionParticipantSerializer(serializers.Serializer):
    key = serializers.RegexField(r"^[a-z][a-z0-9-]{0,47}$")
    name = serializers.CharField(max_length=255)
    kind = serializers.ChoiceField(choices=["foundation", "trained", "external"])
    job = serializers.UUIDField(required=False)
    model = serializers.CharField(max_length=255, required=False)
    served_model = serializers.CharField(max_length=255, required=False, allow_blank=True)


class DecisionComparisonRequestSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    request_key = serializers.CharField(max_length=128)
    final_cell = serializers.UUIDField()
    calibration_cell = serializers.UUIDField(required=False, allow_null=True)
    participants = DecisionParticipantSerializer(many=True)
    baseline = serializers.CharField(max_length=48)
    bootstrap_samples = serializers.IntegerField(min_value=2, max_value=10000, default=1000)
    seed = serializers.IntegerField(min_value=0, max_value=4294967295, default=73491)
    inference = serializers.JSONField(required=False)


class EvaluationResumeSerializer(serializers.Serializer):
    stage = serializers.CharField(required=False)
    call_id = serializers.CharField(required=False, max_length=255)


class EvaluationReuseSerializer(serializers.Serializer):
    source_evaluation = serializers.UUIDField()
    participant = serializers.CharField(max_length=64)
    source_participant = serializers.CharField(max_length=64)


class EvaluationReportSerializer(serializers.Serializer):
    format = serializers.ChoiceField(choices=["json", "md"], default="json")


class DecisionModelOptionSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=["foundation", "trained", "external"])
    qualification = serializers.CharField()


class DecisionCatalogRequestSerializer(serializers.Serializer):
    project = serializers.UUIDField()


class NativeMonitorQuerySerializer(serializers.Serializer):
    view = serializers.ChoiceField(choices=["samples", "labels"], default="samples")
    ordering = serializers.CharField(max_length=128, default="label")
    role = serializers.ChoiceField(choices=["final", "calibration"], default="final")
    offset = serializers.IntegerField(min_value=0, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=25)
    key = serializers.CharField(max_length=128, required=False)
    search = serializers.CharField(max_length=256, required=False, allow_blank=True)
    participant = serializers.CharField(max_length=64, required=False)
    outcome = serializers.ChoiceField(
        choices=["all", "correct", "incorrect", "missing", "unscored"], default="all"
    )


class NativeLiveMetricSerializer(serializers.Serializer):
    mean = serializers.FloatField()
    decisions = serializers.IntegerField()


class NativeBenchmarkProgressSerializer(serializers.Serializer):
    name = serializers.CharField()
    expected = serializers.IntegerField()
    received = serializers.IntegerField()
    scored = serializers.IntegerField()
    invalid = serializers.IntegerField()
    incompatible = serializers.IntegerField()
    raw = serializers.DictField(child=NativeLiveMetricSerializer())
    calibrated = serializers.DictField(child=NativeLiveMetricSerializer())


class NativeModelProgressSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()
    expected = serializers.IntegerField()
    received = serializers.IntegerField()
    scored = serializers.IntegerField()
    invalid = serializers.IntegerField()
    incompatible = serializers.IntegerField()
    benchmarks = NativeBenchmarkProgressSerializer(many=True)


class NativeCallProgressSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()
    state = serializers.CharField()
    stage = serializers.CharField()
    progress = serializers.JSONField()
    observed_at = serializers.DateTimeField(allow_null=True)
    error = serializers.CharField(allow_blank=True)
    progress_error = serializers.CharField(allow_blank=True)


class NativeSamplePredictionSerializer(serializers.Serializer):
    raw = serializers.JSONField(allow_null=True)
    calibrated = serializers.JSONField(allow_null=True)
    metrics = serializers.DictField(child=serializers.FloatField())
    calibrated_metrics = serializers.DictField(child=serializers.FloatField())
    error = serializers.CharField(allow_blank=True)


class NativeSampleSerializer(serializers.Serializer):
    key = serializers.CharField()
    index = serializers.IntegerField()
    benchmark = serializers.CharField()
    input = serializers.JSONField(allow_null=True)
    reference = serializers.JSONField(allow_null=True)
    error = serializers.CharField(allow_blank=True)
    predictions = serializers.DictField(child=NativeSamplePredictionSerializer())


class NativeLabelScoreSerializer(serializers.Serializer):
    support = serializers.IntegerField()
    predicted = serializers.IntegerField()
    correct = serializers.IntegerField()
    precision = serializers.FloatField(allow_null=True)
    recall = serializers.FloatField(allow_null=True)
    f1 = serializers.FloatField(allow_null=True)


class NativeLabelMetricsSerializer(serializers.Serializer):
    benchmark = serializers.CharField()
    label = serializers.CharField()
    index = serializers.IntegerField()
    expected = serializers.IntegerField()
    models = serializers.DictField(child=NativeLabelScoreSerializer())


class NativeMonitorSerializer(serializers.Serializer):
    evaluation = serializers.UUIDField()
    evaluation_state = serializers.CharField()
    role = serializers.CharField()
    state = serializers.CharField()
    error = serializers.CharField(allow_blank=True)
    updated_at = serializers.DateTimeField(allow_null=True)
    provisional = serializers.BooleanField()
    calibration_fitted = serializers.BooleanField()
    arms = NativeModelProgressSerializer(many=True)
    calls = NativeCallProgressSerializer(many=True)
    count = serializers.IntegerField()
    offset = serializers.IntegerField()
    limit = serializers.IntegerField()
    samples = NativeSampleSerializer(many=True)
    view = serializers.CharField()
    labels = NativeLabelMetricsSerializer(many=True)


class NativeEvaluationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = NativeEvaluationSerializer
    filterset_fields = ["project", "state"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return NativeEvaluationPlan.objects.none()
        queryset = (
            NativeEvaluationPlan.objects.filter(
                project_id__in=project_ids_for(self.request.user, self.request.auth)
            )
            .select_related(
                "final_cell__dataset__capability", "calibration_cell__dataset__capability"
            )
            .order_by("-created_at")
        )
        if self.action == "list":
            # Actions freeze cells after loading the plan, so only the list reuses prefetched cells.
            version_cells = Cell.objects.only("id", "dataset_id", "position", "used_at")
            queryset = queryset.prefetch_related(
                *(
                    Prefetch(f"{relation}__dataset__cells", queryset=version_cells)
                    for relation in ("final_cell", "calibration_cell")
                )
            )
        return (
            queryset if self.action in {"destroy", "report"} else queryset.filter(deleted_at=None)
        )

    def perform_destroy(self, instance):
        try:
            delete(instance)
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None

    @extend_schema(parameters=[NativeMonitorQuerySerializer], responses=NativeMonitorSerializer)
    @action(detail=True, methods=["get"], pagination_class=None, filter_backends=[])
    def monitor(self, request, pk=None):
        query = NativeMonitorQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            result = inspect(self.get_object(), **query.validated_data)
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        return Response(NativeMonitorSerializer(result).data)

    @extend_schema(
        parameters=[DecisionCatalogRequestSerializer],
        responses=DecisionModelOptionSerializer(many=True),
    )
    @action(detail=False, methods=["get"], pagination_class=None, filter_backends=[])
    def catalog(self, request):
        body = DecisionCatalogRequestSerializer(data=request.query_params)
        body.is_valid(raise_exception=True)
        project = get_object_or_404(
            Project,
            pk=body.validated_data["project"],
            pk__in=project_ids_for(request.user, request.auth),
        )
        return Response(DecisionModelOptionSerializer(catalog(project), many=True).data)

    @extend_schema(
        request=DecisionComparisonRequestSerializer, responses={201: NativeEvaluationSerializer}
    )
    def create(self, request):
        body = DecisionComparisonRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = dict(body.validated_data)
        project = get_object_or_404(
            Project, pk=data.pop("project"), pk__in=project_ids_for(request.user, request.auth)
        )
        for field in ("final_cell", "calibration_cell"):
            if data.get(field):
                data[field] = get_object_or_404(
                    Cell.objects.select_related("dataset"), pk=data[field], dataset__project=project
                )
        for participant in data["participants"]:
            if participant.get("job"):
                participant["job"] = str(
                    get_object_or_404(FinetuningJob, pk=participant["job"], project=project).pk
                )
        try:
            plan = create_plan(project, triggered_by=request.user, **data)
        except (InputValidationError, DatasetError) as exc:
            raise ValidationError(exc.detail) from None
        except ValueError:
            logger.exception("Workflow validation failed")
            raise ValidationError("The comparison configuration could not be validated") from None
        return Response(NativeEvaluationSerializer(plan).data, status=201)

    @extend_schema(request=None, responses={202: NativeEvaluationSerializer})
    @action(detail=True, methods=["post"], url_path="launch")
    def launch_evaluation(self, request, pk=None):
        require_credits(request.user)
        plan = launch(self.get_object(), user=request.user)
        return Response(NativeEvaluationSerializer(plan).data, status=202)

    @extend_schema(request=None, responses=NativeEvaluationSerializer)
    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        return Response(NativeEvaluationSerializer(pause(self.get_object())).data)

    @extend_schema(request=None, responses={202: NativeEvaluationSerializer})
    @action(detail=True, methods=["post"])
    def prepare(self, request, pk=None):
        return Response(NativeEvaluationSerializer(prepare(self.get_object())).data, status=202)

    @extend_schema(request=EvaluationReuseSerializer, responses=NativeEvaluationSerializer)
    @action(detail=True, methods=["post"], url_path="reuse")
    def reuse(self, request, pk=None):
        body = EvaluationReuseSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = dict(body.validated_data)
        plan = self.get_object()
        source = get_object_or_404(
            NativeEvaluationPlan, pk=values.pop("source_evaluation"), project_id=plan.project_id
        )
        try:
            plan = reuse_predictions(plan, source=source, **values)
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        except ValueError:
            logger.exception("Workflow validation failed")
            raise ValidationError(
                "The workflow configuration could not be validated; inspect the selected inputs and recipe"
            ) from None
        return Response(NativeEvaluationSerializer(plan).data)

    @extend_schema(request=EvaluationResumeSerializer, responses={202: NativeEvaluationSerializer})
    @action(detail=True, methods=["post"], url_path="resume")
    def resume_evaluation(self, request, pk=None):
        body = EvaluationResumeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            plan = resume(self.get_object(), **body.validated_data)
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        except ValueError:
            logger.exception("Workflow validation failed")
            raise ValidationError(
                "The workflow configuration could not be validated; inspect the selected inputs and recipe"
            ) from None
        advance_plan.delay(str(plan.pk))
        return Response(NativeEvaluationSerializer(plan).data, status=202)

    @extend_schema(
        parameters=[EvaluationReportSerializer],
        responses={(200, "application/octet-stream"): bytes},
    )
    @action(
        detail=True,
        methods=["get"],
        content_negotiation_class=EvaluationReportNegotiation,
    )
    def report(self, request, pk=None):
        plan = self.get_object()
        options = EvaluationReportSerializer(data=request.query_params)
        options.is_valid(raise_exception=True)
        if plan.state != "completed":
            raise ValidationError("The comparison report is not complete")
        path = directory(plan, "report") / ("results." + options.validated_data["format"])
        if not path.is_file():
            raise ValidationError("The report artifact is unavailable")
        return FileResponse(path.open("rb"), as_attachment=True, filename=path.name)
