import uuid

from django.db import models


class OperationalRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE)
    kind = models.CharField(max_length=40)
    reference = models.CharField(max_length=100)
    attempt = models.CharField(max_length=100)
    snapshot = models.JSONField(default=dict)
    provider_state = models.JSONField(default=dict)
    collection = models.JSONField(default=dict)
    next_collection_at = models.DateTimeField(null=True, db_index=True)
    sequence = models.PositiveBigIntegerField(default=0)
    last_observed_at = models.DateTimeField(null=True)
    last_heartbeat_at = models.DateTimeField(null=True)
    last_progress_at = models.DateTimeField(null=True)
    source_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "kind", "reference", "attempt"], name="operation_attempt_unique"
            )
        ]
        indexes = [models.Index(fields=["project", "kind", "reference"])]


class OperationalEvent(models.Model):
    operation = models.ForeignKey(OperationalRun, on_delete=models.CASCADE, related_name="events")
    sequence = models.PositiveBigIntegerField()
    event = models.CharField(max_length=20)
    facts = models.JSONField(default=dict)
    source_at = models.DateTimeField()
    observed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["operation", "sequence"], name="operation_event_sequence_unique"
            )
        ]
        ordering = ["sequence"]


class InferenceRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE)
    deployment = models.ForeignKey("overbae.DeployedModel", on_delete=models.PROTECT)
    user = models.ForeignKey("overbae.User", on_delete=models.SET_NULL, null=True)
    request_key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    state = models.CharField(max_length=32, default="queued", db_index=True)
    remote_id = models.CharField(max_length=100, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    is_cold = models.BooleanField(default=False)
    claim = models.UUIDField(null=True)
    claim_until = models.DateTimeField(null=True)
    next_poll_at = models.DateTimeField(db_index=True)
    deadline = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "request_key"], name="inference_request_key_unique"
            )
        ]


class OverwatchFunnelDelivery(models.Model):
    """Idempotency ledger for customer activation notices posted to Slack."""

    class Step(models.TextChoices):
        SIGNED_UP = "signed_up", "Signed up"
        JOINED_PROJECT = "joined_project", "Joined a project"
        SENT_FIRST_SPAN = "sent_first_span", "Sent first trace"
        SYNCED_CAPABILITIES = "synced_capabilities", "Synced capabilities"
        RAN_EVALUATION = "ran_evaluation", "Ran evaluation"
        STARTED_TRAINING = "started_training", "Started training"
        DEPLOYED_MODEL = "deployed_model", "Deployed model"
        CALLED_ALIAS = "called_alias", "Called model alias"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "overbae.User",
        on_delete=models.CASCADE,
        related_name="overwatch_funnel_deliveries",
    )
    step = models.CharField(max_length=32, choices=Step.choices)
    source_at = models.DateTimeField(db_index=True)
    channel_id = models.CharField(max_length=32)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    delivered_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=160, blank=True, default="")
    posthog_attempts = models.PositiveSmallIntegerField(default=0)
    posthog_delivered_at = models.DateTimeField(null=True, blank=True)
    posthog_last_error = models.CharField(max_length=160, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "step"], name="overwatch_funnel_user_step_unique"
            )
        ]
        indexes = [
            models.Index(
                fields=["status", "source_at"], name="overbae_ove_status_3f53a2_idx"
            )
        ]
