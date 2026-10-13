from rest_framework import serializers

from overbae.api.eval_serializers import EvalRunProgressSerializer, EvalRunSerializer
from overbae.models import EvalRun


class EvaluationActivityQuerySerializer(serializers.Serializer):
    project = serializers.UUIDField()
    dataset = serializers.UUIDField(required=False)
    capability = serializers.UUIDField(required=False)
    search = serializers.CharField(required=False)
    status = serializers.ChoiceField(
        choices=[
            "pending",
            "running",
            "completed",
            "failed",
            "cancelled",
            "draft",
            "preparing",
            "prepared",
            "queued",
            "waiting_for_checkpoint",
            "paused",
            "submission_unknown",
        ],
        required=False,
    )
    ordering = serializers.ChoiceField(
        choices=[
            direction + field
            for field in (
                "name",
                "status",
                "created_at",
                "capability_name",
                "dataset_name",
                "variant_count",
                "evaluator_count",
            )
            for direction in ("", "-")
        ],
        default="-created_at",
    )


class EvaluationActivitySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=["generative", "decision"])
    name = serializers.CharField()
    status = serializers.CharField()
    dataset = serializers.UUIDField(allow_null=True)
    dataset_name = serializers.CharField()
    capability_id = serializers.UUIDField(allow_null=True)
    capability_name = serializers.CharField()
    variant_count = serializers.IntegerField()
    evaluator_count = serializers.IntegerField(allow_null=True)
    progress = EvalRunProgressSerializer(allow_null=True)
    data_source = serializers.CharField()
    created_at = serializers.DateTimeField()

    def to_representation(self, instance):
        if isinstance(instance, EvalRun):
            data = dict(EvalRunSerializer(instance, context=self.context).data)
            data.update(
                kind="generative",
                variant_count=1,
                evaluator_count=len(instance.matrix),
                data_source="dataset",
            )
        else:
            dataset = instance.final_cell.dataset
            data = {
                "id": instance.id,
                "kind": "decision",
                "name": instance.name,
                "status": instance.state,
                "dataset": dataset.id,
                "dataset_name": dataset.name,
                "capability_id": dataset.capability_id,
                "capability_name": dataset.capability.name if dataset.capability_id else "",
                "variant_count": len(instance.config.get("participants", []))
                or (2 if instance.job_id else 0),
                "evaluator_count": None,
                "progress": None,
                "data_source": "dataset",
                "created_at": instance.created_at,
            }
        return super().to_representation(data)


class EvaluationActivityDatasetSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
