import uuid

from django.conf import settings
from django.db import models


class TrainingPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    request_key = models.CharField(max_length=128)
    request_fingerprint = models.CharField(max_length=64)
    supersedes = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True)
    source_cell = models.ForeignKey("overbae.Cell", on_delete=models.PROTECT)
    prepared_cell = models.ForeignKey(
        "overbae.Cell",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="training_plans",
    )
    partition = models.ForeignKey(
        "overbae.DataPartitionPlan", on_delete=models.PROTECT, null=True, blank=True
    )
    specification = models.JSONField(default=dict)
    bindings = models.JSONField(default=dict)
    design_identity = models.CharField(max_length=64)
    state = models.CharField(max_length=24)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "request_key"], name="unique_training_plan_request"
            )
        ]
