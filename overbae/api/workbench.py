from django.http import FileResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from overbae.api.scoping import project_ids_for
from overbae.models import (
    APIToken,
    DatasetPipeline,
    DatasetPipelineBinding,
    DatasetPipelinePackage,
    Project,
)
from overbae.services.datasets import pipeline_bindings, pipeline_packages, workbench
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.mcp.auth import ip_allowed


class PipelineSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    pipeline_id = serializers.UUIDField()
    revision = serializers.IntegerField()
    parent = serializers.UUIDField(allow_null=True)
    derived_from = serializers.UUIDField(allow_null=True)
    package = serializers.UUIDField(allow_null=True)
    runtime = serializers.CharField()
    executable = serializers.BooleanField()
    name = serializers.CharField()
    fingerprint = serializers.CharField()
    steps = serializers.ListField(child=serializers.DictField())
    flow = serializers.DictField()
    created_at = serializers.DateTimeField()


class PipelineRunSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    pipeline = serializers.UUIDField(allow_null=True)
    pipeline_name = serializers.CharField(allow_null=True)
    revision = serializers.IntegerField(allow_null=True)
    source_dataset = serializers.UUIDField()
    source_cell = serializers.UUIDField()
    source_fingerprint = serializers.CharField()
    artifact_cell = serializers.UUIDField(allow_null=True)
    artifact_fingerprint = serializers.CharField(allow_blank=True)
    provenance = serializers.CharField(allow_blank=True)
    execution = serializers.CharField()
    mode = serializers.CharField()
    parameters = serializers.DictField()
    binding = serializers.UUIDField(allow_null=True)
    operation = serializers.DictField(allow_null=True)
    runner = serializers.DictField(allow_null=True)
    output_cell = serializers.UUIDField(allow_null=True)
    state = serializers.CharField()
    error = serializers.CharField(allow_blank=True)
    result = serializers.DictField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)
    poll_after_seconds = serializers.IntegerField(allow_null=True)
    queue_seconds = serializers.FloatField(allow_null=True)


class WorkbenchQuerySerializer(serializers.Serializer):
    pipeline_offset = serializers.IntegerField(min_value=0, default=0)
    run_offset = serializers.IntegerField(min_value=0, default=0)
    binding_offset = serializers.IntegerField(min_value=0, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=20)


class WorkbenchPageSerializer(serializers.Serializer):
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    total = serializers.IntegerField()
    has_more = serializers.BooleanField()
    next_cursor = serializers.CharField(allow_null=True)


class WorkbenchSerializer(serializers.Serializer):
    dataset = serializers.UUIDField(allow_null=True)
    preparation = serializers.DictField(allow_null=True)
    current_pipeline = serializers.UUIDField(allow_null=True)
    pipelines = PipelineSerializer(many=True)
    runs = PipelineRunSerializer(many=True)
    pipeline_page = WorkbenchPageSerializer()
    run_page = WorkbenchPageSerializer()
    bindings = serializers.ListField(child=serializers.DictField())
    binding_page = WorkbenchPageSerializer()
    runner = serializers.DictField()
    authoring = serializers.DictField()
    semantic_quality = serializers.CharField()


class CancelWorkSerializer(serializers.Serializer):
    cancelled = serializers.IntegerField()
    detail = serializers.CharField()


class SavePipelineSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    request_key = serializers.CharField(max_length=128)
    package = serializers.UUIDField(
        help_text="Retained Python package UUID from overmind dataset pipeline-upload.",
        error_messages={
            "required": "Upload a staged Python package with overmind dataset pipeline-upload; supply its package UUID. See overmind://dataset-upload."
        },
    )
    pipeline = serializers.UUIDField(required=False)
    expected_revision = serializers.IntegerField(min_value=1, required=False)
    derived_from = serializers.UUIDField(required=False)

    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields)
        if unknown:
            raise ValidationError(
                dict.fromkeys(
                    sorted(unknown),
                    "Unsupported field. Transformation steps belong in the retained Python package manifest; supply package.",
                )
            )
        return super().to_internal_value(data)


class RunPipelineSerializer(serializers.Serializer):
    pipeline = serializers.UUIDField()
    source_cell = serializers.UUIDField()
    source_fingerprint = serializers.RegexField(r"^[0-9a-f]{64}$")
    request_key = serializers.CharField(max_length=128)
    mode = serializers.ChoiceField(choices=["publish", "preview"], default="publish")
    preview_rows = serializers.IntegerField(default=100, min_value=1, max_value=1000)
    parameters = serializers.DictField(required=False)


class ImportVersionSerializer(serializers.Serializer):
    source_cell = serializers.UUIDField()
    source_fingerprint = serializers.RegexField(r"^[0-9a-f]{64}$")
    request_key = serializers.CharField(max_length=128)
    imported_rows = serializers.ListField(
        child=serializers.DictField(), min_length=1, max_length=2000, required=False
    )
    artifact_cell = serializers.UUIDField(required=False)
    artifact_fingerprint = serializers.RegexField(r"^[0-9a-f]{64}$", required=False)
    name = serializers.CharField(max_length=255)
    provenance = serializers.CharField(max_length=8000)


class WorkbenchActions:
    @extend_schema(request=None, responses={202: CancelWorkSerializer})
    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, id=None):
        count = workbench.cancel(self.get_object())
        return Response(
            CancelWorkSerializer(
                {
                    "cancelled": count,
                    "detail": "Cancellation requested. Inspect dataset state and run receipts.",
                }
            ).data,
            status=202,
        )

    @extend_schema(parameters=[WorkbenchQuerySerializer], responses=WorkbenchSerializer)
    @action(detail=True, methods=["get"], url_path="workbench")
    def workbench(self, request, id=None):
        query = WorkbenchQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(
            WorkbenchSerializer(workbench.describe(self.get_object(), **query.validated_data)).data
        )

    @extend_schema(request=SavePipelineSerializer, responses={201: PipelineSerializer})
    @action(detail=True, methods=["post"], url_path="pipelines")
    def save_pipeline(self, request, id=None):
        body = SavePipelineSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            pipeline = workbench.save_pipeline(
                self.get_object().project,
                request.user,
                dataset=self.get_object(),
                **body.validated_data,
            )
        except DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc
        return Response(PipelineSerializer(workbench.pipeline_record(pipeline)).data, status=201)

    @extend_schema(request=RunPipelineSerializer, responses={202: PipelineRunSerializer})
    @action(detail=True, methods=["post"], url_path="pipeline-runs")
    def run_pipeline(self, request, id=None):
        return self._submit_work(request, RunPipelineSerializer)

    @extend_schema(request=ImportVersionSerializer, responses={202: PipelineRunSerializer})
    @action(detail=True, methods=["post"], url_path="versions/import")
    def import_version(self, request, id=None):
        return self._submit_work(request, ImportVersionSerializer)

    def _submit_work(self, request, serializer):
        body = serializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            run = workbench.submit(self.get_object(), request.user, **body.validated_data)
        except DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc
        return Response(PipelineRunSerializer(workbench.run_record(run)).data, status=202)


class ProjectPipelineViewSet(viewsets.ViewSet):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if isinstance(request.auth, APIToken):
            needed = "read" if request.method in {"GET", "HEAD", "OPTIONS"} else "write"
            if needed not in request.auth.scope.get("permission", []) or not ip_allowed(
                request.auth, request.META.get("REMOTE_ADDR")
            ):
                raise PermissionDenied("This connection does not permit this operation.")
        if getattr(request.user, "is_guest", False) and request.method not in {
            "GET",
            "HEAD",
            "OPTIONS",
        }:
            raise PermissionDenied("Guest access is read-only.")

    def project(self, identity):
        return get_object_or_404(
            Project,
            pk=identity,
            is_active=True,
            memberships__user=self.request.user,
            pk__in=project_ids_for(self.request.user, self.request.auth),
        )

    def resolve(self, model, identity):
        return get_object_or_404(
            model,
            pk=identity,
            project__is_active=True,
            project__memberships__user=self.request.user,
            project_id__in=project_ids_for(self.request.user, self.request.auth),
        )


class PackageInputSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    file = serializers.FileField()


class PackageFileSerializer(serializers.Serializer):
    path = serializers.CharField()
    sha256 = serializers.CharField()
    bytes = serializers.IntegerField()


class PackageSourceQuerySerializer(serializers.Serializer):
    file = serializers.CharField(max_length=255)
    offset = serializers.IntegerField(min_value=0, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=16000, default=8000)


class PackageSourceSerializer(serializers.Serializer):
    path = serializers.CharField()
    sha256 = serializers.CharField()
    content = serializers.CharField(allow_blank=True)
    offset = serializers.IntegerField()
    total = serializers.IntegerField()
    next_offset = serializers.IntegerField(allow_null=True)


class PackageSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    project = serializers.UUIDField()
    sha256 = serializers.CharField()
    size = serializers.IntegerField()
    manifest = serializers.DictField()
    inventory = PackageFileSerializer(many=True)
    created_at = serializers.DateTimeField()


class DatasetPipelinePackageViewSet(ProjectPipelineViewSet):
    parser_classes = [MultiPartParser]

    @extend_schema(request=PackageInputSerializer, responses={201: PackageSerializer})
    def create(self, request):
        body = PackageInputSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            package = pipeline_packages.save(
                self.project(body.validated_data["project"]),
                request.user,
                body.validated_data["file"],
            )
        except DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc
        return Response(PackageSerializer(pipeline_packages.record(package)).data, status=201)

    @extend_schema(responses=PackageSerializer)
    def retrieve(self, request, pk=None):
        return Response(
            PackageSerializer(
                pipeline_packages.record(self.resolve(DatasetPipelinePackage, pk))
            ).data
        )

    @extend_schema(parameters=[PackageSourceQuerySerializer], responses=PackageSourceSerializer)
    @action(detail=True, methods=["get"])
    def source(self, request, pk=None):
        package = self.resolve(DatasetPipelinePackage, pk)
        query = PackageSourceQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        arguments = dict(query.validated_data)
        try:
            result = pipeline_packages.source_file(package, arguments.pop("file"), **arguments)
        except DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc
        return Response(PackageSourceSerializer(result).data)

    @extend_schema(responses={(200, "application/zip"): bytes})
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        package = self.resolve(DatasetPipelinePackage, pk)
        return FileResponse(
            package.bundle.open("rb"),
            as_attachment=True,
            filename=f"{package.pk}.zip",
            content_type="application/zip",
        )


class ProjectSavePipelineSerializer(SavePipelineSerializer):
    project = serializers.UUIDField()


class DatasetPipelineViewSet(ProjectPipelineViewSet):
    @extend_schema(request=ProjectSavePipelineSerializer, responses={201: PipelineSerializer})
    def create(self, request):
        body = ProjectSavePipelineSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = dict(body.validated_data)
        try:
            recipe = workbench.save_pipeline(
                self.project(data.pop("project")), request.user, **data
            )
        except DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc
        return Response(PipelineSerializer(workbench.pipeline_record(recipe)).data, status=201)

    @extend_schema(responses=PipelineSerializer)
    def retrieve(self, request, pk=None):
        return Response(
            PipelineSerializer(workbench.pipeline_record(self.resolve(DatasetPipeline, pk))).data
        )


class BindingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    dataset = serializers.UUIDField()
    pipeline = serializers.UUIDField()
    source_dataset = serializers.UUIDField(allow_null=True)
    trace_source = serializers.DictField()
    parameters = serializers.DictField()
    trigger = serializers.CharField()
    interval_seconds = serializers.IntegerField()
    enabled = serializers.BooleanField()
    version = serializers.IntegerField()
    checkpoint = serializers.CharField(allow_blank=True)
    max_runs = serializers.IntegerField()
    runs_started = serializers.IntegerField()
    max_source_rows = serializers.IntegerField()
    error = serializers.CharField(allow_blank=True)
    last_checked_at = serializers.DateTimeField(allow_null=True)
    next_check_at = serializers.DateTimeField(allow_null=True)
    execution_semantics = serializers.CharField()
    revision_adoption = serializers.CharField()


class SaveBindingSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    dataset = serializers.UUIDField()
    pipeline = serializers.UUIDField()
    request_key = serializers.CharField(max_length=128)
    source_dataset = serializers.UUIDField(required=False)
    trace_source = serializers.DictField(required=False)
    parameters = serializers.DictField(required=False)
    trigger = serializers.ChoiceField(
        choices=["manual", "scheduled", "ingestion"], default="manual"
    )
    interval_seconds = serializers.IntegerField(default=60, min_value=10, max_value=86400)
    max_runs = serializers.IntegerField(default=1000, min_value=1, max_value=100000)
    max_source_rows = serializers.IntegerField(default=1000000, min_value=1, max_value=10000000)
    binding = serializers.UUIDField(required=False)
    expected_version = serializers.IntegerField(required=False, min_value=1)


class BindingStateSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    enabled = serializers.BooleanField()


class DatasetPipelineBindingViewSet(ProjectPipelineViewSet):
    @extend_schema(request=SaveBindingSerializer, responses={201: BindingSerializer})
    def create(self, request):
        body = SaveBindingSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = dict(body.validated_data)
        try:
            binding = pipeline_bindings.save(
                self.project(data.pop("project")), request.user, **data
            )
        except DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc
        return Response(
            BindingSerializer(pipeline_bindings.binding_record(binding)).data, status=201
        )

    @extend_schema(responses=BindingSerializer)
    def retrieve(self, request, pk=None):
        return Response(
            BindingSerializer(
                pipeline_bindings.binding_record(self.resolve(DatasetPipelineBinding, pk))
            ).data
        )

    @extend_schema(request=BindingStateSerializer, responses=BindingSerializer)
    @action(detail=True, methods=["post"])
    def state(self, request, pk=None):
        binding = self.resolve(DatasetPipelineBinding, pk)
        body = BindingStateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            updated = pipeline_bindings.set_state(
                binding.project, binding=pk, **body.validated_data
            )
        except DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc
        return Response(BindingSerializer(pipeline_bindings.binding_record(updated)).data)
