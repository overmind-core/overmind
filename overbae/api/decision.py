from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.scoping import project_ids_for
from overbae.core.errors import InputValidationError
from overbae.modal.decision_catalog import models as decision_models
from overbae.models import DecisionJob, Project
from overbae.services.decision_jobs import DecisionUnavailableError, refresh, submit, systemone


class DecisionModelSerializer(serializers.Serializer):
    id = serializers.CharField()
    repo = serializers.CharField()
    family = serializers.CharField()
    params = serializers.IntegerField()
    hidden_size = serializers.IntegerField()
    architecture_context = serializers.IntegerField()
    max_context_length = serializers.IntegerField()


class DecisionJobCreateSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    model = serializers.CharField(max_length=255)
    records = serializers.ListField(child=serializers.DictField(), allow_empty=False)


class DecisionUsageSerializer(serializers.Serializer):
    input_tokens = serializers.IntegerField()
    output_tokens = serializers.IntegerField()


class DecisionJobSerializer(serializers.ModelSerializer):
    record_count = serializers.SerializerMethodField()

    class Meta:
        model = DecisionJob
        fields = [
            "id",
            "project",
            "model_id",
            "status",
            "record_count",
            "result",
            "error",
            "created_at",
        ]
        read_only_fields = fields

    def get_record_count(self, job) -> int:
        return len(job.records or [])


class DecisionSystemOneRequestSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    state = serializers.JSONField()
    questions = serializers.DictField()


class DecisionSystemOneResponseSerializer(serializers.Serializer):
    model = serializers.CharField()
    answers = serializers.DictField()
    usage = DecisionUsageSerializer()


class DecisionModelListView(APIView):
    @extend_schema(responses={200: DecisionModelSerializer(many=True)}, tags=["decision-models"])
    def get(self, request):
        rows = [DecisionModelSerializer(row).data for row in decision_models().values()]
        return Response(rows)


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
        return DecisionJob.objects.filter(
            project_id__in=project_ids_for(self.request.user, self.request.auth)
        )

    def retrieve(self, request, *args, **kwargs):
        job = refresh(self.get_object())
        return Response(DecisionJobSerializer(job).data)

    @extend_schema(request=DecisionJobCreateSerializer, responses={202: DecisionJobSerializer})
    def create(self, request, *args, **kwargs):
        body = DecisionJobCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        project = get_object_or_404(
            Project.objects.filter(id__in=project_ids_for(request.user, request.auth)),
            pk=data["project"],
        )
        try:
            job = submit(project, data["model"], data["records"])
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from exc
        return Response(DecisionJobSerializer(job).data, status=202)


class DecisionSystemOneView(APIView):
    @extend_schema(
        request=DecisionSystemOneRequestSerializer,
        responses={200: DecisionSystemOneResponseSerializer},
        tags=["decision-models"],
        summary="Score a published decision checkpoint",
        description=(
            "Runs the catalog model as published. No training job is required. "
            "Accepts choice, noul, and score questions."
        ),
    )
    def post(self, request, model_id):
        body = DecisionSystemOneRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        get_object_or_404(
            Project.objects.filter(id__in=project_ids_for(request.user, request.auth)),
            pk=data["project"],
        )
        try:
            result = systemone(model_id, data["state"], data["questions"])
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from exc
        except DecisionUnavailableError:
            return Response({"detail": "Decision inference is not available."}, status=503)
        return Response(DecisionSystemOneResponseSerializer(result).data)
