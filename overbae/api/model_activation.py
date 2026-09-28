from rest_framework import serializers

from overbae.models import ModelActivation


class ModelActivationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelActivation
        fields = ["id", "target", "stage", "failed_stage", "error", "started_at", "completed_at"]
        read_only_fields = fields
