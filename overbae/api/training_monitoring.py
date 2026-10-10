from rest_framework import serializers


class TrainingMonitoringQuerySerializer(serializers.Serializer):
    offset = serializers.IntegerField(default=0, min_value=0)
    limit = serializers.IntegerField(default=25, min_value=1, max_value=100)
    check = serializers.UUIDField(required=False)
    probe = serializers.ChoiceField(
        choices=["development", "training_reference", "generation"], required=False
    )


class TrainingCheckSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    key = serializers.CharField()
    state = serializers.CharField()
    step = serializers.IntegerField()
    attempt = serializers.IntegerField()
    stream = serializers.CharField()
    policy_fingerprint = serializers.CharField()
    sample_fingerprint = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    observed_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    metrics = serializers.JSONField()
    coverage = serializers.JSONField()
    facts = serializers.JSONField()
    error = serializers.JSONField()
    evidence_available = serializers.BooleanField()
    evidence_sha256 = serializers.CharField()


class TrainingCheckpointSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    key = serializers.CharField()
    attempt = serializers.IntegerField()
    step = serializers.IntegerField()
    state = serializers.CharField()
    identity = serializers.CharField()
    manifest = serializers.JSONField()
    verification = serializers.JSONField()
    metrics = serializers.JSONField()
    selected = serializers.BooleanField()


class TrainingMonitoringSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    policy = serializers.JSONField(allow_null=True)
    status = serializers.CharField()
    current = serializers.JSONField()
    probes = serializers.JSONField()
    count = serializers.IntegerField()
    next_offset = serializers.IntegerField(allow_null=True)
    checks = TrainingCheckSerializer(many=True)
    checkpoints = TrainingCheckpointSerializer(many=True)
    limitations = serializers.ListField(child=serializers.CharField())


class TrainingEvidenceSerializer(serializers.Serializer):
    items = serializers.ListField(child=serializers.JSONField())
    count = serializers.IntegerField()
    next_offset = serializers.IntegerField(allow_null=True)
    available = serializers.BooleanField()
    sha256 = serializers.CharField(required=False)
    metadata = serializers.JSONField(required=False)
