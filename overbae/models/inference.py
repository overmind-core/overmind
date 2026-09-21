from __future__ import annotations

import uuid

from django.db import models

from .finetuning import FinetuningJob
from .iam import Project


class DeployedModel(models.Model):
    """Lifecycle QUEUED → DEPLOYING → WARMING → READY: DEPLOYING prepares the
    checkpoint on Modal; WARMING boots it once to prove it serves; READY means a
    request will be answered. One GPU container pool per model.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        QUANTIZING = "quantizing", "Quantizing"  # legacy — kept for old records
        DEPLOYING = "deploying", "Deploying"
        WARMING = "warming", "Warming"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        DELETING = "deleting", "Deleting"
        DELETED = "deleted", "Deleted"

    class Quantization(models.TextChoices):
        FP8 = "fp8", "FP8"
        BF16 = "bf16", "BF16"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    finetuning_job = models.OneToOneField(
        FinetuningJob,
        on_delete=models.CASCADE,
        related_name="deployed_model",
        null=True,
        blank=True,
        db_index=True,
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="deployed_models",
        db_index=True,
    )
    # Slug used in vLLM --served-model-name, e.g. "ft-llama31-abc12345"
    model_id = models.CharField(max_length=200, unique=True, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    quantization = models.CharField(
        max_length=10,
        choices=Quantization.choices,
        default=Quantization.FP8,
        blank=True,
    )
    # HF id, e.g. "meta-llama/Llama-3.2-1B-Instruct"
    base_model_id = models.CharField(
        max_length=200,
        blank=True,
        help_text="Base model ID used for the fine-tuning job.",
    )
    # "L4" for compact/small models, "L40S" for mid/large.
    gpu_type = models.CharField(
        max_length=20,
        blank=True,
        help_text="Modal GPU type: 'L4' or 'L40S'.",
    )
    # True when this deployment is an adapter served on a shared base rather than its own
    # merged checkpoint. Then weights_path is the shared base and adapter_path is this model.
    is_lora = models.BooleanField(default=False)
    lora_rank = models.PositiveIntegerField(
        default=0,
        help_text="LoRA rank; 0 for full models.",
    )
    weights_path = models.CharField(
        max_length=500,
        blank=True,
        help_text="Path inside the Modal overmind-weights Volume.",
    )
    adapter_path = models.CharField(
        max_length=500,
        blank=True,
        help_text=(
            "LoRA adapter path inside the overmind-weights Volume, served on the shared base "
            "at weights_path. Blank for merged and full checkpoints."
        ),
    )
    checkpoint_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Content hash of the quantized weights directory.",
    )
    max_model_len = models.PositiveIntegerField(
        default=8192,
        help_text="Maximum context length passed to vLLM --max-model-len.",
    )
    num_parameters = models.BigIntegerField(default=0)
    sla_tier = models.CharField(
        max_length=20,
        default="standard",
        help_text="'hot' keeps a warm container alive; 'standard' scales to zero.",
    )
    # Set when the model reaches READY.
    inference_url = models.CharField(
        max_length=500,
        blank=True,
        help_text="Modal web endpoint URL for the model's vLLM container pool.",
    )
    error_message = models.TextField(blank=True)
    deployment_stage = models.CharField(max_length=32, blank=True)
    deployment_call_id = models.CharField(max_length=100, blank=True)
    deployment_generation = models.UUIDField(default=uuid.uuid4)
    deployment_attempts = models.PositiveIntegerField(default=0)
    deployment_deadline = models.DateTimeField(null=True, blank=True)
    deployment_next_poll_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deployment_claim = models.UUIDField(null=True, blank=True)
    deployment_claim_until = models.DateTimeField(null=True, blank=True)
    # Persisted before submission: a lost acknowledgement must never launch a second call.
    deployment_dispatching = models.BooleanField(default=False)
    deployment_cancel_pending = models.BooleanField(default=False)
    deployment_notify = models.BooleanField(default=False)
    deployment_waiters = models.ManyToManyField(
        FinetuningJob, related_name="evaluation_deployments", blank=True
    )
    # A redeploy reuses the row, so created_at does not describe the current attempt.
    status_changed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # Stamped by the gateway when a request hits a cold model, and read within a
    # short window to show "Warming up". Never cleared: recent-traffic liveness
    # supersedes it once the boot finishes.
    warming_started_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    deployed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.model_id} [{self.status}]"

    @property
    def is_terminal(self) -> bool:
        return self.status in (self.Status.READY, self.Status.FAILED, self.Status.DELETED)


class InferenceCall(models.Model):
    """One row per served-model completion, written at the api/v1 gateway — the
    sole source for the inference page's totals and activity chart. Metrics come
    from vLLM's per-request ``metrics`` block (``--enable-per-request-metrics``)
    when present (see overbae.services.deployed_chat.record_inference_call).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deployed_model = models.ForeignKey(
        DeployedModel,
        on_delete=models.CASCADE,
        related_name="inference_calls",
        db_index=True,
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="inference_calls",
        db_index=True,
    )
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    # Crude GPU-time estimate; None when gpu_type has no configured rate.
    cost = models.FloatField(null=True, blank=True)
    # vLLM engine throughput; falls back to completion_tokens / wall-clock.
    tokens_per_second = models.FloatField(null=True, blank=True)
    # vLLM model time (TTFT + decode), excluding queue/network/cold-start; falls
    # back to gateway wall-clock when metrics are absent.
    latency_ms = models.FloatField(null=True, blank=True)
    # Set at the gateway from recent activity, not guessed from latency: the call
    # almost certainly paid a container cold start. Cold calls stay out of the
    # warm latency and throughput averages.
    is_cold = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["deployed_model", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"InferenceCall({self.deployed_model_id}, {self.created_at:%Y-%m-%d %H:%M})"
