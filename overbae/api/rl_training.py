from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from overbae.api.credit_gate import require_credits
from overbae.api.scoping import project_ids_for
from overbae.core.errors import InputValidationError
from overbae.models import Capability, Dataset, Project, RLRun
from overbae.services import rl_training
from overbae.services.datasets import rows as dataset_rows
from overbae.services.datasets import store, versions
from overbae.services.datasets.lifecycle import DatasetError


class RLRunRequestSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    dataset = serializers.UUIDField()
    cell = serializers.CharField(required=False, allow_blank=True, default="", max_length=64)
    capability = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)
    base_model = serializers.CharField(max_length=255)
    prompt_column = serializers.CharField(max_length=255)
    tools_column = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=255
    )
    system_prompt = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=20000
    )
    rewards = serializers.ListField(child=serializers.JSONField(), min_length=1, max_length=6)
    hyperparameters = serializers.JSONField(required=False, default=dict)
    request_key = serializers.CharField(max_length=128)


class RLRunSerializer(serializers.ModelSerializer):
    dataset_name = serializers.SerializerMethodField()
    holdout_rows = serializers.SerializerMethodField()
    steps_completed = serializers.SerializerMethodField()
    delta = serializers.SerializerMethodField()
    verdict = serializers.SerializerMethodField()

    def get_dataset_name(self, obj) -> str:
        return obj.dataset.name

    def get_holdout_rows(self, obj) -> int:
        return len(obj.holdout_indices)

    def get_steps_completed(self, obj) -> int:
        return obj.steps.exclude(metrics={}).count()

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_delta(self, obj):
        return rl_training.delta(obj)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_verdict(self, obj):
        return rl_training.verdict(obj)

    class Meta:
        model = RLRun
        fields = [
            "id",
            "project",
            "name",
            "status",
            "error",
            "base_model",
            "dataset",
            "dataset_name",
            "cell",
            "capability",
            "prompt_column",
            "tools_column",
            "system_prompt",
            "rewards",
            "hyperparameters",
            "train_rows",
            "holdout_rows",
            "gpu",
            "gpu_seconds",
            "baseline",
            "final",
            "delta",
            "verdict",
            "steps_completed",
            "adapter_path",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class RLRunDetailSerializer(RLRunSerializer):
    steps = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_steps(self, obj):
        return [
            {"step": s.step, "metrics": s.metrics, "batch": s.batch, "group": s.group}
            for s in obj.steps.all()
        ]

    class Meta(RLRunSerializer.Meta):
        fields = [*RLRunSerializer.Meta.fields, "steps", "samples"]
        read_only_fields = fields


class RLCatalogSerializer(serializers.Serializer):
    models = serializers.JSONField()
    checks = serializers.JSONField()
    hyperparameters = serializers.JSONField()


class RLColumnsSerializer(serializers.Serializer):
    dataset = serializers.UUIDField()
    cell = serializers.JSONField()
    columns = serializers.ListField(child=serializers.CharField())
    sample = serializers.JSONField()


class RLRunViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    filterset_fields = ["project", "status"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        return RLRunDetailSerializer if self.action == "retrieve" else RLRunSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RLRun.objects.none()
        return (
            RLRun.objects.filter(
                project_id__in=project_ids_for(self.request.user, self.request.auth)
            )
            .select_related("dataset", "cell", "capability")
            .order_by("-created_at")
        )

    @extend_schema(request=RLRunRequestSerializer, responses={201: RLRunSerializer})
    def create(self, request):
        require_credits(request.user)
        body = RLRunRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = dict(body.validated_data)
        project = get_object_or_404(
            Project, pk=values.pop("project"), pk__in=project_ids_for(request.user, request.auth)
        )
        dataset = get_object_or_404(Dataset, pk=values.pop("dataset"), project=project)
        capability_id = values.pop("capability", None)
        capability = (
            get_object_or_404(Capability, pk=capability_id, project=project)
            if capability_id
            else None
        )
        try:
            run = rl_training.start(
                project,
                user=request.user,
                dataset=dataset,
                capability=capability,
                cell_reference=values.pop("cell"),
                **values,
            )
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        return Response(RLRunSerializer(run).data, status=201)

    @extend_schema(request=None, responses=RLRunSerializer)
    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        return Response(RLRunSerializer(rl_training.cancel(self.get_object())).data)

    @extend_schema(responses=RLCatalogSerializer)
    @action(detail=False, methods=["get"])
    def catalog(self, request):
        return Response(
            {
                "models": list(rl_training.MODELS),
                "checks": rl_training.reward_catalog(),
                "hyperparameters": {
                    key: {"default": default, "min": low, "max": high}
                    for key, (default, _kind, low, high) in rl_training.HYPERPARAMETERS.items()
                },
            }
        )

    @extend_schema(
        parameters=[OpenApiParameter("dataset", OpenApiTypes.UUID, required=True)],
        responses=RLColumnsSerializer,
    )
    @action(detail=False, methods=["get"])
    def columns(self, request):
        dataset = get_object_or_404(
            Dataset,
            pk=request.query_params.get("dataset"),
            project_id__in=project_ids_for(request.user, request.auth),
        )
        try:
            cell = versions.resolve_cell(dataset, "", ran_only=True)
            first = store.read_rows(dataset_rows.frame_path(cell), [0]) if cell.rows else []
        except (DatasetError, dataset_rows.RowStoreError) as exc:
            raise ValidationError(str(exc)) from None
        return Response(
            {
                "dataset": str(dataset.pk),
                "cell": {
                    "id": str(cell.pk),
                    "version": dataset.versions().get(cell.id, ""),
                    "rows": cell.rows,
                },
                "columns": rl_training.columns_of(cell),
                "sample": rl_training.json_safe(first[0]) if first else {},
            }
        )
