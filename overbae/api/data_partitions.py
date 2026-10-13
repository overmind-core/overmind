import logging

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from overbae.api.scoping import project_ids_for
from overbae.core.errors import InputValidationError
from overbae.models import Cell, DataPartitionMember, DataPartitionPlan, Project
from overbae.services.datasets.partition_plans import request_plan, retry
from overbae.tasks.data_partitions import build_plan

logger = logging.getLogger(__name__)


class PartitionRequestSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    request_key = serializers.CharField(max_length=128)
    source_cell = serializers.UUIDField()
    recipe = serializers.JSONField()


class PartitionMemberSerializer(serializers.ModelSerializer):
    dataset = serializers.UUIDField(source="cell.dataset_id", read_only=True)
    rows = serializers.IntegerField(source="cell.rows", read_only=True)

    class Meta:
        model = DataPartitionMember
        fields = ["role", "cell", "dataset", "rows"]
        read_only_fields = fields


class PartitionPlanSerializer(serializers.ModelSerializer):
    members = PartitionMemberSerializer(many=True, read_only=True)

    class Meta:
        model = DataPartitionPlan
        fields = [
            "id",
            "project",
            "name",
            "source_cell",
            "source_fingerprint",
            "request_key",
            "recipe",
            "report",
            "state",
            "error",
            "members",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DataPartitionViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = PartitionPlanSerializer
    filterset_fields = ["project", "source_cell"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DataPartitionPlan.objects.none()
        return (
            DataPartitionPlan.objects.filter(
                project_id__in=project_ids_for(self.request.user, self.request.auth)
            )
            .prefetch_related("members__cell")
            .order_by("-created_at")
        )

    @extend_schema(request=PartitionRequestSerializer, responses={202: PartitionPlanSerializer})
    def create(self, request):
        body = PartitionRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = dict(body.validated_data)
        project = get_object_or_404(
            Project, pk=data.pop("project"), pk__in=project_ids_for(request.user, request.auth)
        )
        cell = get_object_or_404(
            Cell.objects.select_related("dataset"),
            pk=data.pop("source_cell"),
            dataset__project=project,
        )
        try:
            plan = request_plan(project, source_cell=cell, **data)
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        except ValueError:
            logger.exception("Workflow validation failed")
            raise ValidationError(
                "The workflow configuration could not be validated; inspect the selected inputs and recipe"
            ) from None
        if plan.state == "queued":
            build_plan.delay(str(plan.pk))
        return Response(PartitionPlanSerializer(plan).data, status=202)

    @extend_schema(request=None, responses={202: PartitionPlanSerializer})
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        plan = self.get_object()
        try:
            retry(plan)
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        except ValueError:
            logger.exception("Workflow validation failed")
            raise ValidationError(
                "The workflow configuration could not be validated; inspect the selected inputs and recipe"
            ) from None
        build_plan.delay(str(plan.pk))
        plan.refresh_from_db()
        return Response(PartitionPlanSerializer(plan).data, status=202)
