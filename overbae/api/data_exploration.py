import logging

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from overbae.api.scoping import project_ids_for
from overbae.core.errors import InputValidationError
from overbae.models import Cell, DataExploration, Project
from overbae.services.datasets import exploration
from overbae.tasks.data_exploration import run

logger = logging.getLogger(__name__)


class ExplorationRequestSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    source_cell = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    request_key = serializers.CharField(max_length=128)
    kind = serializers.ChoiceField(choices=["profile", "derive"])
    sampling_request = serializers.JSONField(required=False)


class ExplorationSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataExploration
        fields = [
            "id",
            "project",
            "name",
            "request_key",
            "kind",
            "source_cell",
            "source_fingerprint",
            "config",
            "state",
            "report",
            "output_dataset",
            "error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class StrataQuerySerializer(serializers.Serializer):
    limit = serializers.IntegerField(default=100, min_value=1, max_value=100)
    offset = serializers.IntegerField(default=0, min_value=0)


class StratumSerializer(serializers.Serializer):
    values = serializers.ListField(child=serializers.JSONField())
    count = serializers.IntegerField()
    allocation = serializers.IntegerField(allow_null=True)


class StrataPageSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    offset = serializers.IntegerField()
    strata = StratumSerializer(many=True)


class DataExplorationViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = ExplorationSerializer
    filterset_fields = ["project", "source_cell", "state"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DataExploration.objects.none()
        return DataExploration.objects.filter(
            project_id__in=project_ids_for(self.request.user, self.request.auth)
        ).order_by("-created_at")

    @extend_schema(request=ExplorationRequestSerializer, responses={202: ExplorationSerializer})
    def create(self, request):
        body = ExplorationRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = dict(body.validated_data)
        project = get_object_or_404(
            Project, pk=values.pop("project"), pk__in=project_ids_for(request.user, request.auth)
        )
        source = get_object_or_404(
            Cell.objects.select_related("dataset"),
            pk=values.pop("source_cell"),
            dataset__project=project,
        )
        try:
            op = exploration.request(project, source_cell=source, **values)
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        except ValueError:
            logger.exception("Workflow validation failed")
            raise ValidationError(
                "The workflow configuration could not be validated; inspect the selected inputs and recipe"
            ) from None
        if op.state == "queued":
            run.delay(str(op.pk))
        return Response(ExplorationSerializer(op).data, status=202)

    @extend_schema(parameters=[StrataQuerySerializer], responses=StrataPageSerializer)
    @action(detail=True, methods=["get"], filter_backends=[], pagination_class=None)
    def strata(self, request, pk=None):
        query = StrataQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            result = exploration.strata(self.get_object(), **query.validated_data)
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        return Response(StrataPageSerializer(result).data)
