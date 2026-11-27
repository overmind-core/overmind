from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class BillingService(models.TextChoices):
    GRANT = "grant", "Grant"
    PURCHASE = "purchase", "Purchase"
    FINETUNING_JOB = "finetuning-job", "Finetuning job"
    INFERENCE = "inference", "Inference"
    INFERENCE_FT_MODEL = "inference-ft-model", "Fine-tuned inference"
    DEDICATED_INFERENCE = "dedicated-inference", "Dedicated inference"
    DATA_WORKSHOP = "data-workshop", "Data workshop"
    EVALUATION = "evaluation", "Evaluation"


class BillingTelemetry(models.Model):
    """Append-only credits ledger. Balance = SUM(amount) per user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billing_telemetry",
    )
    project = models.ForeignKey(
        "overbae.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billing_telemetry",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=7)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    service = models.CharField(max_length=32, choices=BillingService.choices)
    idempotency_key = models.CharField(max_length=255, null=True, blank=True, unique=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["user", "timestamp"], name="billing_tel_user_ts_idx"),
        ]

    def __str__(self) -> str:
        return f"BillingTelemetry({self.user_id}, {self.service}, {self.amount})"
