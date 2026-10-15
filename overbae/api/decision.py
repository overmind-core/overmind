"""Decision-specific identity and catalog over shared training jobs."""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.scoping import project_ids_for
from overbae.api.serializers import FinetuningJobListSerializer, FinetuningJobSerializer
from overbae.core.errors import InputValidationError
from overbae.modal.decision_catalog import options as decision_model_options
from overbae.models import Dataset, DecisionJob, Project
from overbae.services import decision_jobs
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.plan_limits import require_plan_quota
from overbae.services.training_cancellation import cancel as cancel_training


class DecisionJobCreateSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    model = serializers.CharField(max_length=255)
    dataset = serializers.UUIDField()
    version = serializers.CharField(max_length=80, required=False, allow_blank=True)
    validation_dataset = serializers.UUIDField(required=False, allow_null=True)
    validation_version = serializers.CharField(max_length=80, required=False, allow_blank=True)
    validation_enabled = serializers.BooleanField(default=True)
    validation_split_ratio = serializers.FloatField(default=0.2, min_value=0.05, max_value=0.5)
    split_method = serializers.ChoiceField(choices=["random", "ordered"], default="random")
    eval_dataset = serializers.UUIDField(required=False, allow_null=True)
    eval_version = serializers.CharField(max_length=80, required=False, allow_blank=True)
    hyperparameters = serializers.JSONField(required=False)
    name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    use_case = serializers.CharField(max_length=10_000, required=False, allow_blank=True)
    accepted_findings = serializers.ListField(
        child=serializers.CharField(max_length=2_000), max_length=100, required=False
    )
    request_key = serializers.CharField(max_length=128)


class DecisionJobSerializer(serializers.ModelSerializer):
    model = serializers.CharField(source="model_id", read_only=True)
    status = serializers.CharField(read_only=True)
    training_run = FinetuningJobSerializer(source="finetuning_job", read_only=True)

    class Meta:
        model = DecisionJob
        fields = ["id", "model", "status", "training_run", "created_at"]
        read_only_fields = fields


class DecisionJobListSerializer(DecisionJobSerializer):
    training_run = FinetuningJobListSerializer(source="finetuning_job", read_only=True)


class DecisionTrainingModelOptionSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    family = serializers.ChoiceField(choices=["clef", "d3", "d1"])
    training_available = serializers.BooleanField()
    unavailable_reason = serializers.CharField(allow_null=True)
    max_context_length = serializers.IntegerField()
    limits = serializers.JSONField()
    decision_index = serializers.FloatField(allow_null=True)


class DecisionModelListView(APIView):
    @extend_schema(
        tags=["decision-models"], responses=DecisionTrainingModelOptionSerializer(many=True)
    )
    def get(self, request):
        return Response(decision_model_options())


class DecisionJobViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = DecisionJobSerializer

    def get_serializer_class(self):
        return DecisionJobListSerializer if self.action == "list" else DecisionJobSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DecisionJob.objects.none()
        return (
            DecisionJob.objects.filter(
                finetuning_job__project_id__in=project_ids_for(self.request.user, self.request.auth)
            )
            .select_related(
                "finetuning_job",
                "finetuning_job__cell__dataset",
                "finetuning_job__dataset",
                "finetuning_job__deployed_model",
                "finetuning_job__decision_job",
                "finetuning_job__native_evaluation",
            )
            .order_by("-created_at", "-id")
        )

    @extend_schema(request=DecisionJobCreateSerializer, responses={202: DecisionJobSerializer})
    def create(self, request, *args, **kwargs):
        body = DecisionJobCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        project = get_object_or_404(
            Project.objects.filter(id__in=project_ids_for(request.user, request.auth)),
            pk=data["project"],
        )
        datasets = {}
        for key in ("dataset", "validation_dataset", "eval_dataset"):
            if data.get(key) is not None:
                datasets[key] = get_object_or_404(Dataset, pk=data[key], project=project)

        def admit_new_training():
            from overbae.api.credit_gate import require_credits

            require_credits(request.user)
            require_plan_quota(request.user, "training_jobs")

        try:
            job = decision_jobs.create(
                project=project,
                user=request.user,
                model_id=data["model"],
                dataset=datasets["dataset"],
                validation_dataset=datasets.get("validation_dataset"),
                eval_dataset=datasets.get("eval_dataset"),
                admit_new_training=admit_new_training,
                **{
                    key: value
                    for key, value in data.items()
                    if key
                    in {
                        "version",
                        "validation_version",
                        "validation_enabled",
                        "validation_split_ratio",
                        "split_method",
                        "eval_version",
                        "hyperparameters",
                        "name",
                        "use_case",
                        "accepted_findings",
                        "request_key",
                    }
                },
            )
        except (InputValidationError, DatasetError) as exc:
            raise ValidationError(str(exc)) from exc
        return Response(DecisionJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(request=None, responses={200: DecisionJobSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, *args, **kwargs):
        job = self.get_object()
        cancel_training(job.finetuning_job)
        job.finetuning_job.refresh_from_db()
        return Response(DecisionJobSerializer(job).data)
