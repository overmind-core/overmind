import logging

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from overbae.api.scoping import project_ids_for
from overbae.core.errors import InputValidationError
from overbae.models import Dataset, TrainingPreparation
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.training_preparation import request_preparation, retry_preparation
from overbae.tasks.training_preparation import inspect_preparation

logger = logging.getLogger(__name__)


class TrainingPreparationRequestSerializer(serializers.Serializer):
    dataset = serializers.UUIDField()
    cell = serializers.UUIDField(required=False)
    validation_dataset = serializers.UUIDField(required=False, allow_null=True)
    model = serializers.CharField(max_length=255)
    context_length = serializers.IntegerField(min_value=128, max_value=2_000_000)
    training_type = serializers.ChoiceField(choices=["lora", "full"], default="lora")


class TrainingPreparationSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrainingPreparation
        fields = [
            "id",
            "cell",
            "validation_cell",
            "state",
            "config",
            "report",
            "error",
            "created_at",
        ]
        read_only_fields = fields


class TrainingPreparationViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = TrainingPreparationSerializer

    def get_queryset(self):
        return TrainingPreparation.objects.filter(
            cell__dataset__project_id__in=project_ids_for(self.request.user, self.request.auth)
        )

    @extend_schema(request=None, responses={202: TrainingPreparationSerializer})
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        try:
            preparation = retry_preparation(self.get_object())
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from exc
        except (ValueError, RuntimeError, OSError) as exc:
            logger.exception("Could not retry training preparation %s", pk)
            raise ValidationError("Could not retry preprocessing. Try again.") from exc
        if preparation.state == "queued":
            inspect_preparation.delay(str(preparation.id))
        return Response(TrainingPreparationSerializer(preparation).data, status=202)

    @extend_schema(
        request=TrainingPreparationRequestSerializer, responses={202: TrainingPreparationSerializer}
    )
    def create(self, request):
        body = TrainingPreparationRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        datasets = Dataset.objects.filter(
            project_id__in=project_ids_for(request.user, request.auth)
        )
        dataset = get_object_or_404(datasets, pk=data["dataset"])
        cell = (
            get_object_or_404(dataset.cells, pk=data["cell"])
            if data.get("cell")
            else dataset.active_cell
        )
        validation_cell = None
        if data.get("validation_dataset"):
            validation = get_object_or_404(
                datasets, pk=data["validation_dataset"], project_id=dataset.project_id
            )
            validation_cell = validation.active_cell
            if validation_cell is None:
                raise ValidationError("Validation dataset has no completed version.")
        if cell is None:
            raise ValidationError("Training dataset has no completed version.")
        try:
            preparation = request_preparation(
                cell,
                data["model"],
                data["context_length"],
                validation_cell=validation_cell,
                training_type=data["training_type"],
            )
        except (InputValidationError, DatasetError) as exc:
            raise ValidationError(exc.detail) from exc
        except (ValueError, RuntimeError, OSError) as exc:
            logger.exception("Could not prepare training dataset %s", dataset.pk)
            raise ValidationError(
                "Could not prepare this dataset for training. Try again."
            ) from exc
        if preparation.state == "queued":
            inspect_preparation.delay(str(preparation.id))
        return Response(TrainingPreparationSerializer(preparation).data, status=202)
