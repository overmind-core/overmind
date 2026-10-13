from __future__ import annotations

from django.db.models import Case, CharField, Exists, OuterRef, Q, Subquery, Value, When
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.config import EvalPagination
from overbae.api.credit_gate import require_credits
from overbae.api.eval_serializers import (
    EvalMatrixSerializer,
    EvalRunComparisonSerializer,
    EvalRunCreateSerializer,
    EvalRunDetailSerializer,
    EvalRunGroupSerializer,
    EvalRunRowDetailSerializer,
    EvalRunRowSerializer,
    EvalRunSerializer,
    EvalScoreSerializer,
    EvalSetSerializer,
    EvaluationContextReportSerializer,
    EvaluationContextRequestSerializer,
)
from overbae.api.evaluation_activity import (
    EvaluationActivityDatasetSerializer,
    EvaluationActivityQuerySerializer,
    EvaluationActivitySerializer,
)
from overbae.api.filters import (
    EvalMatrixFilter,
    EvalRunFilter,
    EvalRunRowFilter,
    EvalScoreFilter,
)
from overbae.api.scoping import project_ids_for
from overbae.api.serializers import ModelCatalogResponseSerializer
from overbae.api.views import _user_project_ids
from overbae.core.model_registry import model_defaults
from overbae.innermind import comparison, runs
from overbae.models import (
    EvalMatrix,
    EvalRun,
    EvalRunRow,
    EvalScore,
    EvalSet,
    FinetuningJobEval,
    OptimizerCandidate,
)
from overbae.services.evaluation_activity import (
    activity_datasets,
    activity_records,
    evaluation_activity,
)
from overbae.services.model_catalog import fetch_model_catalog


class ModelCatalogView(APIView):
    @extend_schema(
        summary="Browse the OpenRouter model catalog",
        description=(
            "Trimmed OpenRouter model list for the evaluation model picker. "
            "Each `id` is the exact `model_name` a run target should use. "
            "On upstream failure `upstream_available` is false and `models` is empty."
        ),
        responses=ModelCatalogResponseSerializer,
    )
    def get(self, request):
        models, upstream_available = fetch_model_catalog()
        serializer = ModelCatalogResponseSerializer(
            {
                "models": models,
                "upstream_available": upstream_available,
                "defaults": model_defaults(),
            }
        )
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(summary="List eval matrix rows"),
    retrieve=extend_schema(summary="Get an eval matrix row"),
    create=extend_schema(summary="Add an eval matrix row"),
    destroy=extend_schema(summary="Delete an eval matrix row"),
)
class EvalMatrixViewSet(viewsets.ModelViewSet):
    """Deterministic checks and llm expectations; live span scoring reads the enabled rows."""

    serializer_class = EvalMatrixSerializer
    filterset_class = EvalMatrixFilter
    search_fields = ["name", "source"]
    ordering_fields = ["name", "created_at"]
    lookup_field = "id"
    http_method_names = ["get", "post", "delete"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return EvalMatrix.objects.none()
        return EvalMatrix.objects.filter(
            project_id__in=_user_project_ids(self.request.user, self.request.auth)
        ).select_related("capability")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


@extend_schema_view(
    list=extend_schema(summary="List eval sets"),
    retrieve=extend_schema(summary="Get an eval set"),
    create=extend_schema(summary="Create an eval set"),
    partial_update=extend_schema(summary="Update an eval set's name or rows"),
    destroy=extend_schema(summary="Delete an eval set"),
)
class EvalSetViewSet(viewsets.ModelViewSet):
    """A named selection of matrix rows. The capability's active set drives the
    optimizer, backtests and training evaluations."""

    serializer_class = EvalSetSerializer
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "name"]
    lookup_field = "id"
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return EvalSet.objects.none()
        qs = (
            EvalSet.objects.filter(
                Q(capability__status="current") | Q(capability__isnull=True),
                project_id__in=_user_project_ids(self.request.user, self.request.auth),
            )
            .select_related("capability")
            .prefetch_related("rows")
        )
        capability_id = self.request.query_params.get("capability")
        if capability_id:
            qs = qs.filter(Q(capability_id=capability_id) | Q(capability__isnull=True))
        project_id = self.request.query_params.get("project")
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_destroy(self, instance):
        capability = instance.capability
        if capability is not None and capability.active_eval_set_id == instance.id:
            capability.active_eval_set = (
                EvalSet.objects.filter(capability=capability)
                .exclude(id=instance.id)
                .order_by("-created_at")
                .first()
            )
            capability.save(update_fields=["active_eval_set"])
        instance.delete()

    @extend_schema(
        summary="Activate this set on its capability",
        request=None,
        responses={200: EvalSetSerializer},
    )
    @action(detail=True, methods=["post"])
    def activate(self, request, id=None):
        eval_set = self.get_object()
        if eval_set.capability_id is None:
            raise ValidationError({"capability": "Assign a capability before activating this set."})
        eval_set.capability.active_eval_set = eval_set
        eval_set.capability.save(update_fields=["active_eval_set"])
        return Response(EvalSetSerializer(eval_set, context=self.get_serializer_context()).data)


@extend_schema_view(
    list=extend_schema(summary="List evaluation runs"),
    retrieve=extend_schema(summary="Get an evaluation run"),
)
class EvalRunViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    filterset_class = EvalRunFilter
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "completed_at", "name", "status"]
    lookup_field = "id"

    def get_serializer_class(self):
        if self.action == "retrieve":
            return EvalRunDetailSerializer
        return EvalRunSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return EvalRun.objects.none()
        candidate = OptimizerCandidate.objects.filter(eval_run=OuterRef("pk"))
        origin = Case(
            When(Exists(candidate), then=Value("optimizer")),
            When(
                Exists(FinetuningJobEval.objects.filter(eval_run=OuterRef("pk"))),
                then=Value("finetuning"),
            ),
            default=Value("manual"),
            output_field=CharField(),
        )
        return (
            EvalRun.objects.filter(
                project_id__in=_user_project_ids(self.request.user, self.request.auth)
            )
            .select_related("dataset__capability", "cell", "model_ref")
            .annotate(
                origin=origin,
                optimizer_experiment=Subquery(candidate.values("experiment_id")[:1]),
            )
        )

    @extend_schema(
        summary="Create and launch evaluation runs",
        description=(
            "One run per target, all sharing a `group`. The baseline target is compared "
            "with every other target once both complete."
        ),
        request=EvalRunCreateSerializer,
        responses={201: EvalRunGroupSerializer},
    )
    def create(self, request):
        serializer = EvalRunCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        require_credits(request.user)
        try:
            created = serializer.save(triggered_by=request.user)
        except runs.RunError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            EvalRunGroupSerializer(created, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="List generated-response and probability evaluations",
        parameters=[EvaluationActivityQuerySerializer],
        responses=EvaluationActivitySerializer(many=True),
    )
    @action(detail=False, methods=["get"], filter_backends=[])
    def activity(self, request):
        query = EvaluationActivityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        rows = evaluation_activity(
            project_ids_for(request.user, request.auth), query.validated_data
        )
        page = self.paginate_queryset(rows)
        serializer = EvaluationActivitySerializer(activity_records(page), many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(
        summary="List datasets used by either evaluation format",
        parameters=[EvaluationActivityQuerySerializer],
        responses=EvaluationActivityDatasetSerializer(many=True),
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="activity-datasets",
        filter_backends=[],
        pagination_class=None,
    )
    def activity_datasets(self, request):
        query = EvaluationActivityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        rows = activity_datasets(project_ids_for(request.user, request.auth), query.validated_data)
        return Response(EvaluationActivityDatasetSerializer(rows, many=True).data)

    @extend_schema(
        summary="Estimate evaluation context requirements without launching",
        request=EvaluationContextRequestSerializer,
        responses=EvaluationContextReportSerializer,
    )
    @action(detail=False, methods=["post"], url_path="context-check")
    def context_check(self, request):
        serializer = EvaluationContextRequestSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        return Response(EvaluationContextReportSerializer(serializer.report()).data)

    @extend_schema(
        summary="List the datasets evaluation runs used",
        parameters=[OpenApiParameter(name="project", required=False, type=str)],
        responses={
            200: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "name": {"type": "string"},
                    },
                    "required": ["id", "name"],
                },
            }
        },
    )
    @action(detail=False, methods=["get"])
    def datasets(self, request):
        # Read off the runs so the options and the ``?dataset=`` filter always agree.
        pairs = (
            self.filter_queryset(
                EvalRun.objects.filter(project_id__in=_user_project_ids(request.user, request.auth))
            )
            .exclude(dataset=None)
            .values_list("dataset_id", "dataset__name")
            .distinct()
            .order_by("dataset__name")
        )
        return Response([{"id": str(dataset_id), "name": name} for dataset_id, name in pairs])

    @extend_schema(
        summary="Re-launch a finished run", request=None, responses={200: EvalRunSerializer}
    )
    @action(detail=True, methods=["post"])
    def run(self, request, id=None):
        run = self.get_object()
        require_credits(request.user)
        try:
            launched = runs.relaunch(run)
        except runs.RunError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        if not launched:
            raise ValidationError({"status": "Only a finished run can be re-launched."})
        return Response(EvalRunSerializer(self.get_queryset().get(pk=run.pk)).data)

    @extend_schema(summary="Cancel a run", request=None, responses={200: EvalRunSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, id=None):
        run = self.get_object()
        runs.cancel(run)
        return Response(EvalRunSerializer(self.get_queryset().get(pk=run.pk)).data)

    @extend_schema(
        summary="Compare this run with its group's baseline",
        description="404 until a completed baseline exists in the run's group.",
        responses={200: EvalRunComparisonSerializer},
    )
    @action(detail=True, methods=["get"])
    def comparison(self, request, id=None):
        run = self.get_object()
        baseline = comparison.baseline_of(run.group)
        if baseline is None or baseline.pk == run.pk:
            raise NotFound("No completed baseline to compare with.")
        return Response(EvalRunComparisonSerializer(comparison.compare(baseline, run)).data)


@extend_schema_view(
    list=extend_schema(summary="List a run's rows with their newest scores"),
    retrieve=extend_schema(summary="Get one run row with its input and reference"),
)
class EvalRunRowViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    filterset_class = EvalRunRowFilter
    ordering_fields = ["row_index"]
    ordering = ["row_index"]
    pagination_class = EvalPagination

    def get_serializer_class(self):
        if self.action == "retrieve":
            return EvalRunRowDetailSerializer
        return EvalRunRowSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return EvalRunRow.objects.none()
        return EvalRunRow.objects.filter(
            run__project_id__in=_user_project_ids(self.request.user, self.request.auth)
        ).select_related("run__cell")

    def list(self, request, *args, **kwargs):
        if not request.query_params.get("run"):
            raise ValidationError({"run": "Required."})
        return super().list(request, *args, **kwargs)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if getattr(self, "swagger_fake_view", False):
            return ctx
        run_id = self.request.query_params.get("run")
        if self.action == "retrieve":
            run_id = get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"]).run_id
        if run_id:
            run = get_object_or_404(
                EvalRun.objects.filter(
                    project_id__in=_user_project_ids(self.request.user, self.request.auth)
                ),
                pk=run_id,
            )
            ctx["scores"] = comparison.latest_scores(run)
        return ctx


@extend_schema_view(
    list=extend_schema(summary="List scores for spans, trajectories or run rows"),
    retrieve=extend_schema(summary="Get one score"),
)
class EvalScoreViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = EvalScoreSerializer
    filterset_class = EvalScoreFilter
    ordering_fields = ["created_at", "score"]
    pagination_class = EvalPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return EvalScore.objects.none()
        return EvalScore.objects.filter(
            project_id__in=_user_project_ids(self.request.user, self.request.auth)
        )
