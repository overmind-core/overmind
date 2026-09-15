from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class BillingService(models.TextChoices):
    FREE_CREDITS = "free-credits", "Free credits"
    STRIPE_TOPUP = "stripe-topup", "Stripe top-up"
    FINETUNING_JOB = "finetuning-job", "Finetuning job"
    INFERENCE = "inference", "Inference"
    INFERENCE_FT_MODEL = "inference-ft-model", "Fine-tuned inference"
    DATA_WORKSHOP = "data-workshop", "Data workshop"
    CURSOR_AGENT = "cursor-agent", "Cursor agent"


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


class SubscriptionStatus(models.TextChoices):
    INCOMPLETE = "incomplete", "Incomplete"
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past due"
    CANCELED = "canceled", "Canceled"
    UNPAID = "unpaid", "Unpaid"


class Subscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    stripe_price_id = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=32,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.INCOMPLETE,
    )
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    last_stripe_invoice_id = models.CharField(max_length=255, blank=True, default="")
    cancel_at_period_end = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def started_at(self):
        return self.start_date

    def __str__(self) -> str:
        return f"Subscription({self.user_id}, {self.status})"
