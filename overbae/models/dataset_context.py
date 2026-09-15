from __future__ import annotations

import uuid
from datetime import UTC, datetime

from django.db import models


class DatasetContext(models.Model):
    """Cached profiling + rubric analysis. Callers may serve a stale context
    while they queue an async refresh.
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

    domain = models.CharField(max_length=128, blank=True, default="")
    task_description = models.TextField(blank=True, default="")
    # Values come from services.benchmarks.taxonomy.TaskType, validated at the
    # write site — the model layer must not import service code.
    task_type = models.CharField(max_length=32, blank=True, default="")
    # "semantic" (from the LLM analysis) or "heuristic" (from the profile).
    task_type_source = models.CharField(max_length=16, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    # [{name, rubric, reason}] — at most 2.
    suggested_rubrics = models.JSONField(default=list)
    # {evaluator_id: {score, reason}} from the semantic analysis LLM call.
    evaluator_scores = models.JSONField(default=dict)

    auto_rubric_md = models.TextField(blank=True, default="")
    # [{id, q, weight, gate}]
    auto_rubric_checklist = models.JSONField(default=list)

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
