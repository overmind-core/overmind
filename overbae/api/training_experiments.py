import logging

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from overbae.api.credit_gate import require_credits
from overbae.api.scoping import project_ids_for
from overbae.core.errors import InputValidationError
from overbae.models import NativeEvaluationPlan, Project, TrainingExperiment
from overbae.services import training_experiments
from overbae.services.plan_limits import require_plan_quota

logger = logging.getLogger(__name__)


class ExperimentCandidateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    base_model = serializers.CharField(max_length=255)
    cell = serializers.UUIDField()
    development_cell = serializers.UUIDField(required=False, allow_null=True)
    hyperparameters = serializers.JSONField()


class ExperimentRequestSerializer(serializers.Serializer):
    reuse_existing_predictions = serializers.BooleanField(default=False)
    project = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    purpose = serializers.CharField(max_length=20000)
    request_key = serializers.CharField(max_length=128)
    variants = ExperimentCandidateSerializer(many=True, min_length=1, max_length=6)
    evaluation = serializers.UUIDField(required=False, allow_null=True)
    constraints = serializers.JSONField(required=False)


class ExperimentLaunchSerializer(serializers.Serializer):
    quote_id = serializers.CharField(required=False, max_length=64)


class TrainingProfileSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    request_key = serializers.CharField(max_length=128)
    variant = ExperimentCandidateSerializer()
    max_steps = serializers.IntegerField(min_value=1, max_value=128)
    max_seconds = serializers.IntegerField(min_value=60, max_value=3600)


class TrainingExperimentSerializer(serializers.ModelSerializer):
    record = serializers.SerializerMethodField()

    def get_record(self, obj) -> dict:
        return training_experiments.describe(obj)

    class Meta:
        model = TrainingExperiment
        fields = [
            "id",
            "project",
            "name",
            "purpose",
            "request_key",
            "variants",
            "protocol",
            "evaluation",
            "state",
            "record",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class TrainingExperimentViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = TrainingExperimentSerializer
    filterset_fields = ["project", "state"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TrainingExperiment.objects.none()
        return (
            TrainingExperiment.objects.filter(
                project_id__in=project_ids_for(self.request.user, self.request.auth)
            )
            .select_related("project")
            .order_by("-created_at")
        )

    @extend_schema(
        request=ExperimentRequestSerializer, responses={201: TrainingExperimentSerializer}
    )
    def create(self, request):
        body = ExperimentRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = dict(body.validated_data)
        project = get_object_or_404(
            Project, pk=values.pop("project"), pk__in=project_ids_for(request.user, request.auth)
        )
        reference = values.pop("evaluation", None)
        evaluation = (
            get_object_or_404(
                NativeEvaluationPlan.objects.select_related(
                    "final_cell__dataset", "calibration_cell__dataset"
                ),
                pk=reference,
                project=project,
            )
            if reference
            else None
        )
        variants = [
            {
                **v,
                "cell": str(v["cell"]),
                **(
                    {"development_cell": str(v["development_cell"])}
                    if v.get("development_cell")
                    else {}
                ),
            }
            for v in values.pop("variants")
        ]
        try:
            experiment = training_experiments.create(
                project, user=request.user, variants=variants, evaluation=evaluation, **values
            )
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        except ValueError:
            logger.exception("Workflow validation failed")
            raise ValidationError(
                "The workflow configuration could not be validated; inspect the selected inputs and recipe"
            ) from None
        return Response(TrainingExperimentSerializer(experiment).data, status=201)

    @extend_schema(
        request=ExperimentLaunchSerializer, responses={202: TrainingExperimentSerializer}
    )
    @action(detail=True, methods=["post"])
    def launch(self, request, pk=None):
        require_credits(request.user)
        require_plan_quota(request.user, "training_jobs")
        body = ExperimentLaunchSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            experiment = training_experiments.launch(
                self.get_object(), user=request.user, **body.validated_data
            )
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        except ValueError:
            logger.exception("Workflow validation failed")
            raise ValidationError(
                "The workflow configuration could not be validated; inspect the selected inputs and recipe"
            ) from None
        return Response(TrainingExperimentSerializer(experiment).data, status=202)

    @extend_schema(request=None, responses=TrainingExperimentSerializer)
    @action(detail=True, methods=["post"])
    def prepare(self, request, pk=None):
        try:
            experiment = training_experiments.request_preparation(self.get_object())
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        except ValueError:
            logger.exception("Workflow validation failed")
            raise ValidationError(
                "The workflow configuration could not be validated; inspect the selected inputs and recipe"
            ) from None
        return Response(TrainingExperimentSerializer(experiment).data)

    @extend_schema(request=TrainingProfileSerializer, responses={201: TrainingExperimentSerializer})
    @action(detail=False, methods=["post"])
    def profile(self, request):
        body = TrainingProfileSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = dict(body.validated_data)
        project = get_object_or_404(
            Project, pk=values.pop("project"), pk__in=project_ids_for(request.user, request.auth)
        )
        values["variant"] = {
            k: str(v) if k in {"cell", "development_cell"} and v else v
            for k, v in values["variant"].items()
        }
        try:
            experiment = training_experiments.create_profile(project, user=request.user, **values)
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        except ValueError:
            logger.exception("Workflow validation failed")
            raise ValidationError(
                "The workflow configuration could not be validated; inspect the selected inputs and recipe"
            ) from None
        return Response(TrainingExperimentSerializer(experiment).data, status=201)
