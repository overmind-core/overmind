import uuid

from django.conf import settings
from django.db import models


class WorkshopRun(models.Model):
    class State(models.TextChoices):
        PLANNING = "planning"
        QUEUED = "queued"
        RUNNING = "running"
        PAUSED = "paused"
        PARTIAL = "partial"
        BLOCKED = "blocked"
        COMPLETE = "complete"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        "overbae.Dataset", on_delete=models.CASCADE, related_name="workshop_runs"
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    kind = models.CharField(max_length=24, default="preparation")
    state = models.CharField(max_length=16, choices=State.choices, default=State.PLANNING)
    request = models.TextField(blank=True, default="")
    source = models.ForeignKey(
        "overbae.Cell",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="workshop_inputs",
    )
    source_fingerprint = models.CharField(max_length=64, blank=True, default="")
    output = models.ForeignKey(
        "overbae.Cell",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="workshop_outputs",
    )
    specification = models.JSONField(default=dict, blank=True)
    plan = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    failure = models.JSONField(default=dict, blank=True)
    recovery = models.JSONField(default=dict, blank=True)
    revision = models.PositiveIntegerField(default=0)
    generated_rows = models.PositiveIntegerField(default=0)
    target_rows = models.PositiveIntegerField(default=0)
    owner = models.CharField(max_length=255, blank=True, default="")
    lease_until = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["dataset", "kind", "-created_at"], name="workshop_dataset_kind")
        ]


class WorkshopWorkItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(WorkshopRun, on_delete=models.CASCADE, related_name="items")
    key = models.CharField(max_length=128)
    kind = models.CharField(max_length=24, default="generation")
    state = models.CharField(max_length=24, default="pending")
    inputs = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    failure = models.JSONField(default=dict, blank=True)
    provider = models.JSONField(default=dict, blank=True)
    usage = models.JSONField(default=dict, blank=True)
    artifact = models.CharField(max_length=255, blank=True, default="")
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    request_fingerprint = models.CharField(max_length=64, blank=True, default="")
    rows = models.PositiveIntegerField(default=0)
    attempts = models.PositiveIntegerField(default=0)
    owner = models.CharField(max_length=255, blank=True, default="")
    lease_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "key"], name="unique_workshop_work_key")
        ]


class WorkshopRecord(models.Model):
    run = models.ForeignKey(WorkshopRun, on_delete=models.CASCADE, related_name="records")
    batch = models.ForeignKey(WorkshopWorkItem, on_delete=models.CASCADE, related_name="records")
    content_fingerprint = models.CharField(max_length=64)
    seed_row = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "content_fingerprint"], name="unique_workshop_generated_content"
            )
        ]
