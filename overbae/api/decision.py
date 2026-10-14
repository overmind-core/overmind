from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.credit_gate import require_credits
from overbae.api.scoping import project_ids_for
from overbae.core.errors import InputValidationError
from overbae.modal.decision_catalog import models as decision_models
from overbae.models import Dataset, DecisionJob, Project
from overbae.services import decision_jobs, decision_recommend
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.datasets.rows import RowStoreError


class DecisionWarming(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The decision model is starting. Retry the request."
    default_code = "warming"
    wait = decision_jobs.RETRY_AFTER_SECONDS


class DecisionUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Decision inference is not available."
    default_code = "unavailable"

    def __init__(self, reason: str):
        super().__init__(
            detail={"detail": self.default_detail, "code": self.default_code, "reason": reason}
        )


class DecisionLimitsSerializer(serializers.Serializer):
    options = serializers.IntegerField(help_text="Choice options per question.")
    score_levels = serializers.IntegerField(help_text="Levels per score question.")
    questions = serializers.IntegerField(help_text="Questions per request.")


class DecisionModelSerializer(serializers.Serializer):
    id = serializers.CharField()
    repo = serializers.CharField()
    family = serializers.CharField()
    params = serializers.IntegerField()
    hidden_size = serializers.IntegerField()
    architecture_context = serializers.IntegerField()
    max_context_length = serializers.IntegerField()
    limits = DecisionLimitsSerializer()


class DecisionRecommendSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    dataset = serializers.UUIDField()
    version = serializers.CharField(max_length=80, required=False, allow_blank=True)


class DecisionShapeSerializer(serializers.Serializer):
    options = serializers.IntegerField(help_text="Widest choice or noul option list.")
    score_levels = serializers.IntegerField(help_text="Most levels in one score question.")
    questions = serializers.IntegerField(help_text="Questions in the widest request.")


class DecisionDatasetSerializer(serializers.Serializer):
    rows = serializers.IntegerField()
    longest_input_tokens = serializers.IntegerField(
        help_text="Approximate tokens of the longest row's input (state and questions)."
    )
    shape = DecisionShapeSerializer()


class DecisionCandidateSerializer(serializers.Serializer):
    model = serializers.CharField()
    fits = serializers.BooleanField()
    why_not = serializers.CharField(allow_null=True)
    decision_index = serializers.FloatField(allow_null=True)
    decision_index_edition = serializers.CharField(allow_null=True)
    params = serializers.IntegerField()


class DecisionPickSerializer(serializers.Serializer):
    labels = serializers.ListField(
        child=serializers.ChoiceField(choices=["strongest", "smaller", "smallest"]),
        help_text="Trade-offs this model fills; the picks are unranked.",
    )
    model = serializers.CharField()
    reason = serializers.CharField()


class DecisionRecommendationSerializer(serializers.Serializer):
    picks = DecisionPickSerializer(many=True)
    dataset = DecisionDatasetSerializer()
    candidates = DecisionCandidateSerializer(many=True)


class DecisionJobCreateSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    model = serializers.CharField(max_length=255)
    dataset = serializers.UUIDField()
    version = serializers.CharField(max_length=80, required=False, allow_blank=True)
    eval_dataset = serializers.UUIDField(required=False, allow_null=True)
    eval_version = serializers.CharField(max_length=80, required=False, allow_blank=True)


class DecisionVersionSerializer(serializers.Serializer):
    dataset = serializers.UUIDField(source="dataset_id")
    dataset_name = serializers.CharField(source="dataset.name")
    version = serializers.CharField(source="label")
    rows = serializers.IntegerField()


class DecisionJobSerializer(serializers.ModelSerializer):
    training = DecisionVersionSerializer(source="cell")
    evaluation_data = DecisionVersionSerializer(source="eval_cell", allow_null=True)
    checkpoint_ready = serializers.SerializerMethodField()
    evaluation_status = serializers.SerializerMethodField()

    class Meta:
        model = DecisionJob
        fields = [
            "id",
            "project",
            "model_id",
            "status",
            "training",
            "evaluation_data",
            "result",
            "evaluation",
            "evaluation_status",
            "error",
            "cost_usd",
            "checkpoint_ready",
            "created_at",
            "started_at",
            "completed_at",
        ]
        read_only_fields = fields

    def get_checkpoint_ready(self, job) -> bool:
        return job.status == DecisionJob.Status.SUCCEEDED and bool((job.result or {}).get("saved"))

    @extend_schema_field(
        serializers.ChoiceField(choices=["none", "complete", "partial", "failed"], allow_null=True)
    )
    def get_evaluation_status(self, job) -> str | None:
        return job.evaluation_status


class DecisionRequestSerializer(serializers.Serializer):
    state = serializers.CharField(allow_blank=True, trim_whitespace=False)
    questions = serializers.DictField()


class DecisionModelRequestSerializer(DecisionRequestSerializer):
    project = serializers.UUIDField()


class DecisionUsageSerializer(serializers.Serializer):
    input_tokens = serializers.IntegerField()
    output_tokens = serializers.IntegerField()


class DecisionResponseSerializer(serializers.Serializer):
    model = serializers.CharField()
    answers = serializers.DictField()
    usage = DecisionUsageSerializer()


def _answer(project, request, data, model_id, job=None):
    require_credits(request.user)
    try:
        result = decision_jobs.answer(
            project, request.user, model_id, data["state"], data["questions"], job=job
        )
    except InputValidationError as exc:
        raise ValidationError({"detail": [exc.detail]}) from exc
    except decision_jobs.DecisionWarmingError as exc:
        raise DecisionWarming() from exc
    except decision_jobs.DecisionUnavailableError as exc:
        raise DecisionUnavailable(exc.reason) from exc
    return Response(DecisionResponseSerializer(result).data)


class DecisionModelListView(APIView):
    @extend_schema(responses={200: DecisionModelSerializer(many=True)}, tags=["decision-models"])
    def get(self, request):
        return Response(DecisionModelSerializer(list(decision_models().values()), many=True).data)


class DecisionSystemOneView(APIView):
    @extend_schema(
        request=DecisionModelRequestSerializer,
        responses={200: DecisionResponseSerializer},
        tags=["decision-models"],
        summary="Answer a System One request with a catalog decision model",
    )
    def post(self, request, model_id):
        body = DecisionModelRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        project = get_object_or_404(
            Project.objects.filter(id__in=project_ids_for(request.user, request.auth)),
            pk=body.validated_data["project"],
        )
        return _answer(project, request, body.validated_data, model_id)


class DecisionJobViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = DecisionJobSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DecisionJob.objects.none()
        queryset = DecisionJob.objects.filter(
            project_id__in=project_ids_for(self.request.user, self.request.auth)
        ).select_related("cell__dataset", "eval_cell__dataset")
        project = self.request.query_params.get("project")
        return queryset.filter(project_id=project) if project else queryset

    @extend_schema(
        parameters=[OpenApiParameter("project", OpenApiTypes.UUID, OpenApiParameter.QUERY)]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=DecisionRecommendSerializer, responses={200: DecisionRecommendationSerializer}
    )
    @action(detail=False, methods=["post"])
    def recommend(self, request):
        body = DecisionRecommendSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        project = get_object_or_404(
            Project.objects.filter(id__in=project_ids_for(request.user, request.auth)),
            pk=data["project"],
        )
        dataset = get_object_or_404(Dataset, pk=data["dataset"], project=project)
        try:
            result = decision_recommend.recommend(dataset, data.get("version"))
        except DatasetError as exc:
            raise ValidationError({exc.field or "dataset": [exc.detail]}) from exc
        except RowStoreError as exc:
            raise ValidationError({"dataset": ["The dataset version cannot be read."]}) from exc
        return Response(DecisionRecommendationSerializer(result).data)

    @extend_schema(request=DecisionJobCreateSerializer, responses={202: DecisionJobSerializer})
    def create(self, request, *args, **kwargs):
        body = DecisionJobCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        project = get_object_or_404(
            Project.objects.filter(id__in=project_ids_for(request.user, request.auth)),
            pk=data["project"],
        )
        require_credits(request.user)
        dataset = get_object_or_404(Dataset, pk=data["dataset"], project=project)
        eval_dataset = (
            get_object_or_404(Dataset, pk=data["eval_dataset"], project=project)
            if data.get("eval_dataset")
            else None
        )
        try:
            job = decision_jobs.create(
                project,
                request.user,
                data["model"],
                dataset,
                data.get("version"),
                eval_dataset,
                data.get("eval_version"),
            )
        except InputValidationError as exc:
            raise ValidationError({"detail": [exc.detail]}) from exc
        except DatasetError as exc:
            raise ValidationError({exc.field or "dataset": [exc.detail]}) from exc
        job = self.get_queryset().get(pk=job.pk)
        return Response(DecisionJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(request=None, responses={200: DecisionJobSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        try:
            job = decision_jobs.cancel(self.get_object())
        except InputValidationError as exc:
            raise ValidationError({"detail": [exc.detail]}) from exc
        return Response(DecisionJobSerializer(job).data)

    @extend_schema(
        request=DecisionRequestSerializer,
        responses={200: DecisionResponseSerializer},
        summary="Answer a System One request with the trained decision model",
    )
    @action(detail=True, methods=["post"], url_path="systemone")
    def systemone(self, request, pk=None):
        job = self.get_object()
        body = DecisionRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return _answer(job.project, request, body.validated_data, job.model_id, job)
