import uuid

from django.conf import settings
from django.db import models


class FinetuningJob(models.Model):
    """API-side handle for one training run. A Celery worker dispatches the run
    to the configured provider and reconciles the remote state back onto this row.
    """

    class Status(models.TextChoices):
        QUEUED = "queued"
        PREPARING = "preparing"
        RUNNING = "running"
        DEPLOYING = "deploying"  # training done, waiting for Modal deployment
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        CANCELLED = "cancelled"

    class Tier(models.TextChoices):
        COMPACT = "compact"
        SMALL = "small"
        MID = "mid"
        LARGE = "large"

    class Provider(models.TextChoices):
        TOGETHER_AI = "together_ai"
        # Legacy providers — retired backends kept so existing job rows render;
        # new submissions only go to TOGETHER_AI, BASETEN, or MODAL.
        NEBIUS = "nebius"
        TINKER = "tinker"
        BASETEN = "baseten"
        MODAL = "modal"

    class SplitMethod(models.TextChoices):
        ORDERED = "ordered"  # last N% by row index
        RANDOM = "random"  # GroupShuffleSplit, trace-safe (default)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    project = models.ForeignKey(
        "overbae.Project",
        on_delete=models.CASCADE,
        related_name="finetuning_jobs",
    )
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finetuning_jobs",
    )
    dataset = models.ForeignKey(
        "overbae.Dataset",
        on_delete=models.PROTECT,
        related_name="finetuning_jobs",
    )
    # The cells the job trains and validates on; the dataset FKs only group
    # jobs per dataset.
    cell = models.ForeignKey(
        "overbae.Cell",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="finetuning_jobs",
    )
    validation_cell = models.ForeignKey(
        "overbae.Cell",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="validation_finetuning_jobs",
    )
    validation_enabled = models.BooleanField(default=True)
    validation_split_ratio = models.FloatField(default=0.2)
    validation_dataset = models.ForeignKey(
        "overbae.Dataset",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="validation_finetuning_jobs",
    )
    split_method = models.CharField(
        max_length=16,
        choices=SplitMethod.choices,
        default=SplitMethod.RANDOM,
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_finetuning_jobs",
    )

    eval_dataset = models.ForeignKey(
        "overbae.Dataset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eval_finetuning_jobs",
    )
    eval_cell = models.ForeignKey(
        "overbae.Cell",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="eval_finetuning_jobs",
    )
    eval_set = models.ForeignKey(
        "overbae.EvalSet",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finetuning_jobs",
    )

    name = models.CharField(max_length=255, blank=True, default="")
    use_case = models.TextField(blank=True, default="")

    # Shared by the experiments the wizard launches together; null for jobs from
    # the legacy single-job dialog.
    group_id = models.UUIDField(null=True, blank=True, db_index=True)
    model_tier = models.CharField(max_length=20, choices=Tier.choices, blank=True, default="")

    # Set once on submission; never changes.
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.BASETEN)

    base_model = models.CharField(max_length=255)
    # Forwarded verbatim to the provider (learning_rate, n_epochs, batch_size,
    # lora params, …).
    hyperparameters = models.JSONField(default=dict, blank=True)

    # The capability's production model at submit time, snapshotted so the delta
    # answers "did the fine-tune beat what we run today?" rather than comparing
    # against the base model of the family being trained. Empty when the job has
    # no capability or no resolvable model; the baseline then uses base_model.
    baseline_model = models.CharField(max_length=255, blank=True, default="")

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True
    )

    remote_job_id = models.CharField(max_length=255, blank=True, default="", db_index=True)

    # Set on success.
    model_weights_location = models.CharField(max_length=1024, blank=True, default="")
    output_model_name = models.CharField(max_length=255, blank=True, default="")

    # Rewritten each poll cycle by progress_from_snapshot: {trained_steps,
    # total_steps, percent, eta_seconds, elapsed_seconds, tokens_processed,
    # phase, provider_status, latest_train_loss, latest_eval_loss,
    # metrics: {loss, learning_rate, grad_norm}, checkpoints: [...]}
    progress = models.JSONField(default=dict, blank=True)
    # Written on terminal status; on success {"epoch_losses": [...], "model":
    # "...", "metrics": {...}, "checkpoints": [...]}.
    result = models.JSONField(default=dict, blank=True)

    error_message = models.TextField(blank=True, default="")
    retry_count = models.PositiveIntegerField(default=0)
    # 0 by default: a failure surfaces immediately for manual review instead of
    # re-running silently.
    max_retries = models.PositiveIntegerField(default=0)

    celery_task_id = models.CharField(max_length=255, blank=True, default="")

    # Per-job USD from Baseten's GET /v1/billing/usage_summary breakdown item
    # subtotal; credits there are account-level only.
    cost_usd = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    billed_minutes = models.PositiveIntegerField(null=True, blank=True)
    cost_synced_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["group_id", "-created_at"]),
        ]

    def __str__(self):
        return f"FinetuningJob {self.id} [{self.provider}] ({self.base_model} → {self.status})"

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            self.Status.SUCCEEDED,
            self.Status.FAILED,
            self.Status.CANCELLED,
        }


class FinetuningJobEvent(models.Model):
    """Append-only timeline entry. Epoch-level losses live in
    ``FinetuningJob.result``, not here.
    """

    class EventType(models.TextChoices):
        STATUS_CHANGE = "status_change"
        PROGRESS = "progress"
        CHECKPOINT = "checkpoint"
        LOG = "log"
        ERROR = "error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(FinetuningJob, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=20, choices=EventType.choices, default=EventType.LOG)
    message = models.TextField(blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["job", "-created_at"])]

    def __str__(self):
        return f"{self.job_id} [{self.event_type}] {self.message[:40]}"


class FinetuningJobEval(models.Model):
    """One judge-eval against a fine-tune job's model artifact. Baseline rows
    score the untouched ``base_model``; checkpoint and final rows score provider
    artifacts, and only when those are callable for inference. Results are
    denormalised here once the linked ``EvalRun`` completes.
    """

    class Kind(models.TextChoices):
        BASELINE = "baseline"
        CHECKPOINT = "checkpoint"
        FINAL = "final"

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"
        SKIPPED = "skipped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(FinetuningJob, on_delete=models.CASCADE, related_name="job_evals")
    eval_run = models.ForeignKey(
        "overbae.EvalRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finetuning_job_evals",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # Provider checkpoint id / step when kind=checkpoint; empty for baseline.
    checkpoint_id = models.CharField(max_length=255, blank=True, default="")
    checkpoint_step = models.IntegerField(null=True, blank=True)
    # Exact model id passed to inference (base model, ckpt path, or final output).
    model_id = models.CharField(max_length=512, blank=True, default="")
    # Mean judge score across metrics (0–1-ish); null until the EvalRun finishes.
    aggregate_score = models.FloatField(null=True, blank=True)
    # aggregate_score − baseline aggregate_score (null until both exist).
    baseline_delta = models.FloatField(null=True, blank=True)
    # Set only when the eval dataset's references are labels: {classes:
    # [{label, precision, recall, f1, support}], aggregates: {accuracy, n,
    # macro/micro/weighted}, confusion_matrix: {labels, matrix}}.
    class_metrics = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["checkpoint_step", "created_at"]
        indexes = [
            models.Index(fields=["job", "kind"]),
            models.Index(fields=["job", "checkpoint_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "kind"],
                condition=models.Q(kind="baseline"),
                name="uniq_ft_job_eval_baseline",
            ),
            models.UniqueConstraint(
                fields=["job", "kind"],
                condition=models.Q(kind="final"),
                name="uniq_ft_job_eval_final",
            ),
            models.UniqueConstraint(
                fields=["job", "checkpoint_id"],
                condition=models.Q(kind="checkpoint") & ~models.Q(checkpoint_id=""),
                name="uniq_ft_job_eval_checkpoint",
            ),
        ]

    def __str__(self):
        return f"FinetuningJobEval {self.kind} job={self.job_id} ({self.status})"
