from __future__ import annotations

import uuid
from datetime import UTC, datetime

from django.db import models


class DatasetContext(models.Model):
    """Cached dataset profile. Callers may serve a stale context while they
    queue an async refresh.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.OneToOneField(
        "overbae.Dataset",
        on_delete=models.CASCADE,
        related_name="context",
    )
    project = models.ForeignKey(
        "overbae.Project",
        on_delete=models.CASCADE,
        related_name="dataset_contexts",
        null=True,
        blank=True,
    )

    # Output of profiler.profile_dataset.
    profile = models.JSONField(default=dict)

    # Values come from services.benchmarks.taxonomy.TaskType, validated at the
    # write site — the model layer must not import service code.
    task_type = models.CharField(max_length=32, blank=True, default="")
    # "heuristic" (from the profile) or "declared" (from the capability).
    task_type_source = models.CharField(max_length=16, blank=True, default="")

    sample_count = models.PositiveIntegerField(default=0)
    extracted_at = models.DateTimeField()
    # Last background refresh failure; empty when healthy.
    refresh_error = models.TextField(blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["dataset"])]

    def __str__(self):
        return f"DatasetContext<{self.dataset_id}@{self.extracted_at.isoformat()}>"

    @property
    def age_days(self) -> int:
        return (datetime.now(UTC) - self.extracted_at).days
