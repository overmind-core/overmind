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
from overbae.models import DecisionPerformanceRun, NativeEvaluationPlan
from overbae.services import decision_performance
from overbae.tasks.decision_performance import measure

logger = logging.getLogger(__name__)


class PerformanceWorkloadSerializer(serializers.Serializer):
    amortization_decisions = serializers.IntegerField(
        min_value=1, max_value=1000000000000, default=1000000
    )
    sample_size = serializers.IntegerField(min_value=1, max_value=64, default=32)
    repetitions = serializers.IntegerField(min_value=1, max_value=10, default=3)
    concurrency = serializers.IntegerField(min_value=1, max_value=16, default=1)
    seed = serializers.IntegerField(min_value=0, max_value=4294967295, default=73491)
    questions_per_request = serializers.IntegerField(min_value=1, max_value=16, default=1)


class PerformanceRequestSerializer(serializers.Serializer):
    evaluation = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    request_key = serializers.CharField(max_length=128)
    workload = PerformanceWorkloadSerializer()


class DecisionPerformanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = DecisionPerformanceRun
        fields = [
            "id",
            "project",
            "evaluation",
            "name",
            "request_key",
            "workload",
            "state",
            "results",
            "error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DecisionPerformanceViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = DecisionPerformanceSerializer
    filterset_fields = ["project", "evaluation", "state"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DecisionPerformanceRun.objects.none()
        return DecisionPerformanceRun.objects.filter(
            project_id__in=project_ids_for(self.request.user, self.request.auth)
        ).order_by("-created_at")

    @extend_schema(
        request=PerformanceRequestSerializer, responses={202: DecisionPerformanceSerializer}
    )
    def create(self, request):
        body = PerformanceRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = dict(body.validated_data)
        evaluation = get_object_or_404(
            NativeEvaluationPlan.objects.select_related("final_cell__dataset"),
            pk=values.pop("evaluation"),
            project_id__in=project_ids_for(request.user, request.auth),
        )
        require_credits(request.user)
        try:
            run = decision_performance.create(evaluation, **values)
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        except ValueError:
            logger.exception("Workflow validation failed")
            raise ValidationError(
                "The workflow configuration could not be validated; inspect the selected inputs and recipe"
            ) from None
        if run.state == "queued":
            measure.delay(str(run.pk))
        return Response(DecisionPerformanceSerializer(run).data, status=202)

    @extend_schema(request=None, responses={202: DecisionPerformanceSerializer})
    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        run = self.get_object()
        if run.state != "failed":
            raise ValidationError("Only stopped measurements can be resumed")
        DecisionPerformanceRun.objects.filter(pk=run.pk, state="failed").update(
            state="queued", error=""
        )
        measure.delay(str(run.pk))
        run.refresh_from_db()
        return Response(DecisionPerformanceSerializer(run).data, status=202)
