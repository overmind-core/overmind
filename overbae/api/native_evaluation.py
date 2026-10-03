from rest_framework import serializers

from overbae.models import NativeEvaluationPlan


class NativeEvaluationRequestSerializer(serializers.Serializer):
    calibration_cell = serializers.UUIDField()
    final_cell = serializers.UUIDField()


class NativeEvaluationSerializer(serializers.ModelSerializer):
    class Meta:
        model = NativeEvaluationPlan
        fields = [
            "id",
            "job",
            "state",
            "config",
            "calls",
            "calibration",
            "results",
            "error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
