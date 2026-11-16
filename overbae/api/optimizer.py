from __future__ import annotations

import logging

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from overbae.api.scoping import project_ids_for
from overbae.models import (
    Cell,
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
)
from overbae.services.datasets import use
from overbae.services.optimizer_create import create_optimizer_experiment

logger = logging.getLogger(__name__)


class OptimizerCommandSerializer(serializers.ModelSerializer):
    class Meta:
        model = OptimizerCommand
        fields = [
            "id",
            "experiment",
            "candidate",
            "iteration",
            "datapoint_index",
            "command",
            "code_path",
            "output",
            "result",
            "score",
            "trace_type",
            "original_trace_id",
            "status",
            "timeout",
            "error",
            "created_at",
        ]
        read_only_fields = fields


class OptimizerCandidateSerializer(serializers.ModelSerializer):
    model_name = serializers.SerializerMethodField()

    def get_model_name(self, obj) -> str:
        return obj.target_model or obj.experiment._incumbent_model_name()

    class Meta:
        model = OptimizerCandidate
        fields = [
            "id",
            "experiment",
            "iteration",
            "candidate_index",
            "target_model",
            "model_name",
            "is_baseline",
            "score",
            "scores",
            "status",
            "code_path",
            "eval_run",
            "created_at",
        ]
        read_only_fields = fields


class OptimizerIterationSerializer(serializers.ModelSerializer):
    # Nested so the ``iterations`` action returns the whole tree in one call.
    candidates = OptimizerCandidateSerializer(many=True, read_only=True)

    class Meta:
        model = OptimizerIteration
        fields = [
            "id",
            "experiment",
            "order",
            "name",
            "status",
            "scores",
            "patch",
            "eval_run",
            "candidates",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "experiment",
            "order",
            "name",
            "status",
            "scores",
            "patch",
            "eval_run",
            "created_at",
        ]


class OptimizerExperimentSerializer(serializers.ModelSerializer):
    # Denormalised labels so the run page needs no per-entity fetch.
    capability_name = serializers.SerializerMethodField()
    dataset_name = serializers.CharField(source="dataset.name", read_only=True, default="")
    eval_set_name = serializers.CharField(source="eval_set.name", read_only=True, default="")
    cell_info = serializers.SerializerMethodField()

    def get_capability_name(self, obj) -> str:
        return obj.capability.name if obj.capability_id else ""

    def get_cell_info(self, obj) -> dict | None:
        return use.describe(obj.cell if obj.cell_id else None)

    class Meta:
        model = OptimizerExperiment
        fields = [
            "id",
            "project",
            "capability",
            "capability_name",
            "dataset_name",
            "eval_set_name",
            "dataset",
            "cell",
            "cell_info",
            "eval_set",
            "triggered_by",
            "entrypoint",
            "code_trigger",
            "mode",
            "model_ids",
            "openrouter_key_source",
            "status",
            "current_iteration",
            "num_iterations",
            "num_candidates_per_iteration",
            "max_iterations_without_improvement",
            "stalled_iterations",
            "command_template",
            "scores",
            "state",
            "failure_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SetCommandTemplateSerializer(serializers.Serializer):
    template = serializers.CharField()


class CandidateInputSerializer(serializers.Serializer):
    candidate_index = serializers.IntegerField(min_value=0)
    code_path = serializers.CharField(required=False, default="", allow_blank=True)
    patch = serializers.CharField(required=False, default="", allow_blank=True)
    target_model = serializers.CharField(required=False, default="", allow_blank=True)
    is_baseline = serializers.BooleanField(required=False, default=False)


class CreateIterationSerializer(serializers.Serializer):
    order = serializers.IntegerField(min_value=0)
    name = serializers.CharField(required=False, default="", allow_blank=True)
    candidates = CandidateInputSerializer(many=True)


class CommandResultInputSerializer(serializers.Serializer):
    candidate_id = serializers.UUIDField()
    datapoint_index = serializers.IntegerField(min_value=0)
    success = serializers.BooleanField(default=True)
    output = serializers.CharField(required=False, default="", allow_blank=True)
    error = serializers.CharField(required=False, default="", allow_blank=True)
    trace_id = serializers.CharField(required=False, default="", allow_blank=True)
    input = serializers.JSONField(required=False, default=None, allow_null=True)


class PostResultsSerializer(serializers.Serializer):
    results = CommandResultInputSerializer(many=True)


class EvaluateIterationSerializer(serializers.Serializer):
    order = serializers.IntegerField(min_value=0)


class OptimizerExperimentCreateSerializer(serializers.ModelSerializer):
    """``perform_create`` fills ``eval_set``/``entrypoint`` from the capability;
    ``dataset`` must be supplied.
    """

    cell = serializers.PrimaryKeyRelatedField(
        queryset=Cell.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = OptimizerExperiment
        fields = [
            "capability",
            "dataset",
            "cell",
            "eval_set",
            "entrypoint",
            "code_trigger",
            "mode",
            "model_ids",
            "openrouter_key_source",
            "num_iterations",
            "num_candidates_per_iteration",
            "max_iterations_without_improvement",
        ]


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="capability",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Only return experiments for this capability.",
            )
        ]
    )
)
class OptimizerExperimentViewSet(viewsets.ModelViewSet):
    serializer_class = OptimizerExperimentSerializer
    lookup_field = "id"
    lookup_value_regex = "[0-9a-f-]{36}"
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return OptimizerExperiment.objects.none()
        qs = OptimizerExperiment.objects.filter(
            project_id__in=project_ids_for(self.request.user, self.request.auth)
        ).select_related("capability", "dataset", "cell", "eval_set")
        capability_id = self.request.query_params.get("capability")
        if capability_id:
            qs = qs.filter(capability_id=capability_id)
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return OptimizerExperimentCreateSerializer
        return OptimizerExperimentSerializer

    def perform_create(self, serializer):
        capability = serializer.validated_data["capability"]
        allowed_projects = project_ids_for(self.request.user, self.request.auth)
        if str(capability.project_id) not in [str(pid) for pid in allowed_projects]:
            raise PermissionDenied("You do not belong to this capability's project.")

        validated = serializer.validated_data
        serializer.instance = create_optimizer_experiment(
            user=self.request.user,
            capability=capability,
            dataset=validated.get("dataset"),
            cell=validated.get("cell"),
            eval_set=validated.get("eval_set"),
            entrypoint=validated.get("entrypoint", ""),
            code_trigger=validated.get("code_trigger", ""),
            mode=validated.get("mode", OptimizerExperiment.Mode.OPTIMIZE),
            model_ids=validated.get("model_ids", []),
            openrouter_key_source=validated.get(
                "openrouter_key_source", OptimizerExperiment.OpenRouterKeySource.PLATFORM
            ),
            num_iterations=validated.get("num_iterations", 5),
            num_candidates_per_iteration=validated.get("num_candidates_per_iteration", 3),
            max_iterations_without_improvement=validated.get(
                "max_iterations_without_improvement", 3
            ),
        )

    @extend_schema(responses=OptimizerExperimentSerializer)
    def create(self, request, *args, **kwargs):
        create_serializer = self.get_serializer(data=request.data)
        create_serializer.is_valid(raise_exception=True)
        self.perform_create(create_serializer)
        output = OptimizerExperimentSerializer(
            create_serializer.instance, context=self.get_serializer_context()
        )
        return Response(output.data, status=status.HTTP_201_CREATED)

    def _paginated(self, qs, serializer_cls):
        """The generated client expects a ``Paginated*List`` envelope from every
        list-shaped action, so custom actions must paginate too."""
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(serializer_cls(page, many=True).data)
        return Response(serializer_cls(qs, many=True).data)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="status",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter commands by status (e.g. 'failed').",
            )
        ],
        responses=OptimizerCommandSerializer(many=True),
    )
    @action(detail=True, methods=["get"])
    def commands(self, request, id=None):
        experiment = self.get_object()
        qs = experiment.commands.all().order_by("created_at")
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return self._paginated(qs, OptimizerCommandSerializer)

    @extend_schema(responses=OptimizerIterationSerializer(many=True))
    @action(detail=True, methods=["get"])
    def iterations(self, request, id=None):
        experiment = self.get_object()
        qs = experiment.iterations.prefetch_related("candidates").order_by("order")
        return self._paginated(qs, OptimizerIterationSerializer)

    @extend_schema(responses=OptimizerExperimentSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, id=None):
        experiment = self.get_object()
        experiment.cancel()
        return Response(OptimizerExperimentSerializer(experiment).data)

    @extend_schema(request=SetCommandTemplateSerializer, responses=OptimizerExperimentSerializer)
    @action(detail=True, methods=["post"])
    def template(self, request, id=None):
        from overbae.services.optimizer_ledger import set_command_template

        experiment = self.get_object()
        ser = SetCommandTemplateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        experiment = set_command_template(experiment, ser.validated_data["template"])
        return Response(OptimizerExperimentSerializer(experiment).data)

    @extend_schema(request=CreateIterationSerializer, responses=OptimizerIterationSerializer)
    @action(detail=True, methods=["post"], url_path="add-iteration")
    def add_iteration(self, request, id=None):
        from overbae.services.optimizer_ledger import create_iteration

        experiment = self.get_object()
        ser = CreateIterationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v = ser.validated_data
        iteration = create_iteration(
            experiment,
            order=v["order"],
            name=v.get("name", ""),
            candidates=v["candidates"],
        )
        return Response(
            OptimizerIterationSerializer(iteration).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=PostResultsSerializer,
        responses={"200": {"type": "object", "properties": {"upserted": {"type": "integer"}}}},
    )
    @action(detail=True, methods=["post"])
    def results(self, request, id=None):
        from overbae.services.optimizer_ledger import post_results

        experiment = self.get_object()
        ser = PostResultsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        summary = post_results(experiment, [dict(r) for r in ser.validated_data["results"]])
        return Response(summary)

    @extend_schema(request=EvaluateIterationSerializer, responses=OptimizerExperimentSerializer)
    @action(detail=True, methods=["post"])
    def evaluate(self, request, id=None):
        from overbae.services.optimizer_ledger import evaluate_iteration

        experiment = self.get_object()
        ser = EvaluateIterationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        experiment = evaluate_iteration(experiment, ser.validated_data["order"])
        return Response(OptimizerExperimentSerializer(experiment).data)

    @extend_schema(responses=OptimizerExperimentSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request, id=None):
        from overbae.services.optimizer_ledger import complete_experiment

        experiment = self.get_object()
        experiment = complete_experiment(experiment)
        return Response(OptimizerExperimentSerializer(experiment).data)


class OptimizerCandidateViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OptimizerCandidateSerializer
    lookup_field = "id"
    lookup_value_regex = "[0-9a-f-]{36}"
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return OptimizerCandidate.objects.none()
        return OptimizerCandidate.objects.filter(
            experiment__project_id__in=project_ids_for(self.request.user, self.request.auth)
        ).select_related("iteration", "eval_run", "experiment")
