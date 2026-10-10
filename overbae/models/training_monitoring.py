import uuid

from django.db import models


class TrainingValidationRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        "overbae.FinetuningJob", on_delete=models.CASCADE, related_name="validation_runs"
    )
    key = models.CharField(max_length=160)
    attempt = models.PositiveIntegerField()
    stream = models.CharField(max_length=32)
    step = models.PositiveIntegerField()
    state = models.CharField(max_length=24)
    policy_fingerprint = models.CharField(max_length=64)
    sample_fingerprint = models.CharField(max_length=64)
    receipt_fingerprint = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(null=True)
    observed_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    metrics = models.JSONField(default=dict)
    coverage = models.JSONField(default=dict)
    facts = models.JSONField(default=dict)
    failure = models.JSONField(default=dict)
    evidence = models.FileField(upload_to="training-monitoring/", blank=True)
    evidence_sha256 = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["job", "key"], name="training_check_key")]
        indexes = [models.Index(fields=["job", "step", "attempt"], name="training_check_step")]


class TrainingCheckpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        "overbae.FinetuningJob", on_delete=models.CASCADE, related_name="retained_checkpoints"
    )
    key = models.CharField(max_length=160)
    attempt = models.PositiveIntegerField()
    step = models.PositiveIntegerField()
    state = models.CharField(max_length=24)
    identity = models.CharField(max_length=64)
    manifest = models.JSONField(default=dict)
    verification = models.JSONField(default=dict)
    metrics = models.JSONField(default=dict)
    selected = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "overbae_trainingretainedcheckpoint"
        constraints = [
            models.UniqueConstraint(fields=["job", "key"], name="training_checkpoint_key")
        ]
