from rest_framework import serializers

from overbae.api.eval_serializers import require_membership, require_same_project
from overbae.innermind.signals import signal_factory
from overbae.models import SignalEvent, SignalSubscription


class SignalCatalogEntrySerializer(serializers.Serializer):
    signal_type = serializers.CharField()
    description = serializers.CharField()
    params = serializers.DictField()
    default_enabled = serializers.BooleanField()


class SignalSubscriptionSerializer(serializers.ModelSerializer):
    signal_type = serializers.ChoiceField(choices=sorted(signal_factory.signals))
    description = serializers.SerializerMethodField()

    class Meta:
        model = SignalSubscription
        fields = [
            "id",
            "project",
            "capability",
            "signal_type",
            "description",
            "enabled",
            "config",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "description", "created_at", "updated_at"]

    def validate_project(self, value):
        return require_membership(self, value)

    def get_description(self, obj) -> str:
        return signal_factory.signals[obj.signal_type].description

    def validate(self, attrs):
        instance = self.instance
        project = attrs.get("project") or instance.project
        if instance is not None:
            for field in ("project", "capability", "signal_type"):
                if field in attrs and attrs[field] != getattr(instance, field):
                    raise serializers.ValidationError({field: "Create a new subscription instead."})
        capability = attrs.get("capability", instance.capability if instance else None)
        require_same_project(project, capability=capability)
        signal_type = attrs.get("signal_type") or instance.signal_type
        try:
            signal_factory.validate_config(signal_type, attrs.get("config", {}))
        except ValueError as exc:
            raise serializers.ValidationError({"config": str(exc)}) from exc
        if (
            instance is None
            and SignalSubscription.objects.filter(
                project=project, capability=capability, signal_type=signal_type
            ).exists()
        ):
            raise serializers.ValidationError({"signal_type": "Already subscribed."})
        return attrs


class SignalEventSerializer(serializers.ModelSerializer):
    capability_name = serializers.CharField(source="capability.name", read_only=True, default="")

    class Meta:
        model = SignalEvent
        fields = [
            "id",
            "project",
            "capability",
            "capability_name",
            "subscription",
            "signal_type",
            "conversation",
            "trace_id",
            "message",
            "details",
            "occurred_at",
            "seen_at",
        ]
        read_only_fields = fields


class SignalEventSeenSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    ids = serializers.ListField(child=serializers.UUIDField(), required=False, max_length=1000)


class SignalEventSeenResponseSerializer(serializers.Serializer):
    updated = serializers.IntegerField()
