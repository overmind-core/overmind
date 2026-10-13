from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from pydantic import ValidationError as ContractError
from rest_framework import mixins, serializers, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from overbae.api.scoping import project_ids_for
from overbae.core.errors import InputValidationError
from overbae.models import Project, TrainingPlan
from overbae.services import training_plans
from overbae.services.training_plan_contract import TrainingPlanRequest


class TrainingPlanInputSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    request_key = serializers.CharField(max_length=128)
    source_cell = serializers.UUIDField()
    task = serializers.JSONField()
    supersedes = serializers.UUIDField(required=False, allow_null=True)
    prepared_cell = serializers.UUIDField(required=False, allow_null=True)
    partition = serializers.UUIDField(required=False, allow_null=True)
    evidence = serializers.JSONField(required=False)
    selection = serializers.JSONField(required=False, allow_null=True)
    unresolved = serializers.ListField(child=serializers.CharField(), required=False)

    def validate(self, attrs):
        if set(self.initial_data) - set(self.fields):
            raise ValidationError("Unknown training-plan fields")
        try:
            request = TrainingPlanRequest.model_validate(
                {k: v for k, v in attrs.items() if k != "project"}
            )
        except ContractError:
            raise ValidationError(
                "Supply a valid task, scoped evidence and, if provided, development-only pilot selection"
            ) from None
        return {"project": attrs["project"], **request.model_dump(mode="json")}


class TrainingPlanSerializer(serializers.ModelSerializer):
    record = serializers.SerializerMethodField()

    def get_record(self, obj) -> dict:
        return training_plans.describe(obj)

    class Meta:
        model = TrainingPlan
        fields = ["id", "project", "name", "state", "record", "created_at"]
        read_only_fields = fields


class TrainingPlanViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = TrainingPlanSerializer
    filterset_fields = ["project", "state"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TrainingPlan.objects.none()
        return (
            TrainingPlan.objects.filter(
                project_id__in=project_ids_for(self.request.user, self.request.auth)
            )
            .select_related("project")
            .order_by("-created_at")
        )

    @extend_schema(request=TrainingPlanInputSerializer, responses={201: TrainingPlanSerializer})
    def create(self, request):
        body = TrainingPlanInputSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = dict(body.validated_data)
        project = get_object_or_404(
            Project, pk=values.pop("project"), pk__in=project_ids_for(request.user, request.auth)
        )
        try:
            plan = training_plans.save(project, user=request.user, **values)
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        return Response(TrainingPlanSerializer(plan).data, status=201)
