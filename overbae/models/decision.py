import uuid

from django.conf import settings
from django.db import models


class DecisionJob(models.Model):
    """Trains a decision model on a frozen dataset version, then scores an optional
    evaluation version with the base and the trained model. Transitions use
    ``.filter(pk=..., status=<expected>).update(...)``."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        EVALUATING = "evaluating"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        CANCELLED = "cancelled"

    ACTIVE = (Status.QUEUED, Status.RUNNING, Status.EVALUATING)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project",
        on_delete=models.CASCADE,
        related_name="decision_jobs",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    model_id = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    cell = models.ForeignKey("overbae.Cell", on_delete=models.PROTECT, related_name="+")
    eval_cell = models.ForeignKey(
        "overbae.Cell",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    remote_id = models.CharField(max_length=255, blank=True, default="")
    result = models.JSONField(default=dict, blank=True)
    evaluation = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    cost_usd = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status"],
                condition=models.Q(status__in=["queued", "running", "evaluating"]),
                name="decision_job_active",
            )
        ]

    def __str__(self) -> str:
        return f"{self.model_id} {self.status}"

    @property
    def evaluation_status(self) -> str | None:
        """complete, partial or failed once evaluated; none without an eval set."""
        if self.eval_cell_id is None:
            return "none"
        evaluation = self.evaluation or {}
        if not evaluation:
            return None
        if evaluation.get("status"):
            return evaluation["status"]
        # Evaluations stored before the status field: an error or an empty side failed.
        sides = [evaluation.get("base") or {}, evaluation.get("trained") or {}]
        if evaluation.get("error") or any(not side.get("n") for side in sides):
            return "failed"
        return "partial" if any(side.get("unanswered") for side in sides) else "complete"
