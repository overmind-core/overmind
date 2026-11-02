import uuid

from django.conf import settings
from django.db import models


class RLRun(models.Model):
    """One single-turn GRPO run on Modal, rewarded by deterministic check scripts.

    The run pins a dataset version, a base model, the rendered reward checks and the
    held-out row indices before dispatch, so its baseline and final scores compare the
    same prompts under the same checks."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        STARTING = "starting"
        BASELINE = "baseline"
        TRAINING = "training"
        FINAL = "final"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        CANCELLED = "cancelled"

    TERMINAL = frozenset({Status.SUCCEEDED, Status.FAILED, Status.CANCELLED})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE, related_name="rl_runs")
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rl_runs",
    )
    dataset = models.ForeignKey("overbae.Dataset", on_delete=models.PROTECT, related_name="rl_runs")
    cell = models.ForeignKey("overbae.Cell", on_delete=models.PROTECT, related_name="rl_runs")
    name = models.CharField(max_length=255)
    base_model = models.CharField(max_length=255)
    prompt_column = models.CharField(max_length=255)
    tools_column = models.CharField(max_length=255, blank=True, default="")
    system_prompt = models.TextField(blank=True, default="")
    # [{key, check, params, bind, attributes, expected, weight}]
    rewards = models.JSONField(default=list)
    hyperparameters = models.JSONField(default=dict)
    holdout_indices = models.JSONField(default=list)
    train_rows = models.PositiveIntegerField(default=0)
    request_key = models.CharField(max_length=128)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    error = models.TextField(blank=True, default="")
    modal_call_id = models.CharField(max_length=128, blank=True, default="")
    gpu = models.CharField(max_length=32, blank=True, default="")
    # Wall time of the GPU container as the worker measured it; null until reported.
    gpu_seconds = models.FloatField(null=True, blank=True)
    # {pass_rate, mean_reward, count, per_check: {key: {mean, scored, abstained, errors}}}
    baseline = models.JSONField(default=dict, blank=True)
    final = models.JSONField(default=dict, blank=True)
    # [{index, prompt, baseline: {output, reward, passed, checks}, final: {...}}]
    samples = models.JSONField(default=list, blank=True)
    adapter_path = models.CharField(max_length=512, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "request_key"], name="uniq_rl_run_request_key"
            ),
        ]

    def __str__(self):
        return f"RL {self.name} ({self.status})"

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL


class RLStep(models.Model):
    """One optimizer step: the trainer's logged metrics, the batch's check outcomes and
    the first prompt's group of sampled attempts."""

    run = models.ForeignKey(RLRun, on_delete=models.CASCADE, related_name="steps")
    step = models.PositiveIntegerField()
    metrics = models.JSONField(default=dict, blank=True)
    batch = models.JSONField(default=dict, blank=True)
    group = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["step"]
        constraints = [
            models.UniqueConstraint(fields=["run", "step"], name="uniq_rl_step"),
        ]
