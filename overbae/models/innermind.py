"""Innermind tables: the eval matrix, its sets, runs over dataset cells, append-only
score history and the signals a project subscribes to.

A span keeps its latest result inline in ``Span.eval_attributes``; ``EvalScore``
keeps every result ever written for a span, a trajectory or a dataset row.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class ModelRef(models.Model):
    """Fine-tunes and OpenAI-compatible endpoints the catalog in
    :mod:`overbae.core.llms` does not carry."""

    class Provider(models.TextChoices):
        OPENAI = "openai"
        ANTHROPIC = "anthropic"
        GEMINI = "gemini"
        TOGETHER = "together"
        CUSTOM = "custom"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="model_refs"
    )

    label = models.CharField(max_length=255)
    provider = models.CharField(max_length=20, choices=Provider.choices)
    # Provider-native id, e.g. "gpt-5-mini" or "meta-llama/Llama-3-8b-chat-hf".
    model_id = models.CharField(max_length=512)
    # Empty => the provider default.
    base_url = models.CharField(max_length=1024, blank=True, default="")
    # Env var name holding the API key, e.g. "TOGETHER_API_KEY" — secrets are
    # never stored in the DB.
    api_key_ref = models.CharField(max_length=128, blank=True, default="")
    # Extra completion kwargs forwarded to LiteLLM (temperature, max_tokens…).
    params = models.JSONField(default=dict, blank=True)

    finetuning_job = models.ForeignKey(
        "overbae.FinetuningJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="model_refs",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.label} ({self.provider}/{self.model_id})"


class EvalMatrix(models.Model):
    """One metric scored on every matching span or dataset row."""

    class Type(models.TextChoices):
        DETERMINISTIC = "deterministic"
        LLM = "llm"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="eval_matrix"
    )
    # Null rows apply to every capability in the project.
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.CASCADE,
        related_name="eval_matrix",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=16, choices=Type.choices)
    # deterministic: a ``def evaluate(span)`` script; llm: one sentence the output must meet.
    source = models.TextField()
    # Blank matches every span; otherwise a ``Span.SpanType`` value or span name.
    span_type = models.CharField(max_length=40, blank=True, default="")
    span_name = models.CharField(max_length=255, blank=True, default="")
    enabled = models.BooleanField(default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "eval_matrix"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "capability", "name"], name="uniq_eval_matrix_capability_name"
            ),
            models.UniqueConstraint(
                fields=["project", "name"],
                condition=models.Q(capability__isnull=True),
                name="uniq_eval_matrix_project_name",
            ),
        ]
        indexes = [models.Index(fields=["project", "enabled"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.type})"

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "source": self.source,
            "span_type": self.span_type,
            "span_name": self.span_name,
        }

    @classmethod
    def from_snapshot(cls, data: dict) -> EvalMatrix:
        """An unsaved row: a run grades with the matrix frozen at launch."""
        return cls(
            name=data["name"],
            type=data["type"],
            source=data["source"],
            span_type=data.get("span_type", ""),
            span_name=data.get("span_name", ""),
        )


class EvalSet(models.Model):
    """``Capability.active_eval_set`` drives the optimizer, backtest and training defaults."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="eval_sets"
    )
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.CASCADE,
        related_name="eval_sets",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    rows = models.ManyToManyField(EvalMatrix, related_name="eval_sets", blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("capability", "name")]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"],
                condition=models.Q(capability__isnull=True),
                name="uniq_project_eval_set_name_unbound",
            )
        ]
        indexes = [models.Index(fields=["capability", "-created_at"])]

    def __str__(self) -> str:
        return f"EvalSet {self.name} ({self.capability_id})"

    def matrix_snapshot(self) -> list[dict]:
        return [row.snapshot() for row in self.rows.filter(enabled=True).order_by("name")]


class EvalRun(models.Model):
    """One dataset cell scored for one target. Baseline and candidates are sibling
    runs sharing ``group`` and paired row by row on ``row_index``."""

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"

    class Mode(models.TextChoices):
        # The row's own recorded output.
        RECORDED = "recorded"
        # A model generates each row's output.
        GENERATE = "generate"
        # The caller posts each row's output before scoring.
        PROVIDED = "provided"

    TERMINAL = (Status.COMPLETED, Status.FAILED, Status.CANCELLED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="eval_runs"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    judge_model = models.CharField(max_length=255, blank=True, default="")

    dataset = models.ForeignKey(
        "overbae.Dataset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eval_runs",
    )
    # PROTECT keeps the frame the rows were read from.
    cell = models.ForeignKey(
        "overbae.Cell",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="eval_runs",
    )
    # 0 => every row.
    max_items = models.PositiveIntegerField(default=0)
    sampling = models.FloatField(default=1.0)
    eval_set = models.ForeignKey(
        EvalSet,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eval_runs",
    )
    # The set's rows frozen at launch; grading reads only this.
    matrix = models.JSONField(default=list, blank=True)

    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.RECORDED)
    model_ref = models.ForeignKey(
        ModelRef,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eval_runs",
    )
    # A catalog model name when there is no ModelRef.
    model_name = models.CharField(max_length=255, blank=True, default="")
    # system_prompt, generation_strategy, reasoning_effort, max_steps, label.
    params = models.JSONField(default=dict, blank=True)
    group = models.UUIDField(default=uuid.uuid4, db_index=True)
    is_baseline = models.BooleanField(default=False)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    error = models.TextField(blank=True, default="")
    summary = models.JSONField(default=dict, blank=True)

    # Generation admission: project fairness and the single scoring hand-off.
    last_admitted_at = models.DateTimeField(null=True, blank=True)
    scoring_task_id = models.CharField(max_length=255, blank=True, default="")
    scoring_started_at = models.DateTimeField(null=True, blank=True)

    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_eval_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "-created_at"]),
            models.Index(
                fields=["project", "id"],
                condition=models.Q(status="running"),
                name="eval_running_project",
            ),
        ]

    def __str__(self) -> str:
        return f"EvalRun {self.name} ({self.status})"

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL

    @property
    def label(self) -> str:
        return self.params.get("label") or self.resolved_model or self.get_mode_display()

    @property
    def resolved_model(self) -> str:
        if self.model_ref_id and self.model_ref:
            return self.model_ref.model_id
        return self.model_name


class EvalRunRow(models.Model):
    """One dataset row of a run: its admission state and the output that gets scored."""

    class State(models.TextChoices):
        WAITING = "waiting"
        QUEUED = "queued"
        RUNNING = "running"
        DONE = "done"
        UNKNOWN = "unknown"
        CANCELLED = "cancelled"

    run = models.ForeignKey(EvalRun, on_delete=models.CASCADE, related_name="rows")
    row_index = models.PositiveIntegerField()
    source_trace_id = models.CharField(max_length=64, blank=True, default="")
    # A string or an assistant message; null until generated.
    output = models.JSONField(null=True, blank=True)
    # model, cost, tokens, finish_reasons, truncated, generation_strategy.
    metadata = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    # Degraded rows stay out of aggregates: a pipeline-caused 0 is not a model failure.
    degraded = models.BooleanField(default=False)
    degraded_reason = models.CharField(max_length=255, blank=True, default="")

    state = models.CharField(max_length=16, choices=State.choices, default=State.WAITING)
    task_id = models.CharField(max_length=255, blank=True, default="")
    queued_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["row_index"]
        constraints = [
            models.UniqueConstraint(fields=["run", "row_index"], name="uniq_eval_run_row")
        ]
        indexes = [models.Index(fields=["state", "expires_at"], name="eval_row_state_expiry")]

    def __str__(self) -> str:
        return f"Row {self.row_index} of {self.run_id} ({self.state})"


class EvalGenerationScheduler(models.Model):
    """The single row the generation dispatcher locks; its tick proves the beat is alive."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_tick_at = models.DateTimeField(null=True)


class EvalScore(models.Model):
    """Append-only: every scoring pass adds a row, so history is never rewritten."""

    class SourceType(models.TextChoices):
        SPAN = "span"
        TRAJECTORY = "trajectory"
        DATASET_ROW = "dataset_row"

    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="eval_scores"
    )
    source_type = models.CharField(max_length=16, choices=SourceType.choices)
    # span_id, trace_id, or "<cell_id>:<row_index>" for a dataset row.
    source_id = models.CharField(max_length=255)
    run = models.ForeignKey(
        EvalRun, on_delete=models.CASCADE, null=True, blank=True, related_name="scores"
    )
    # Mean of the scored metrics; null when every metric abstained or errored.
    score = models.FloatField(null=True, blank=True)
    # {name: {type, expectation, score, verdict, reason, error}}
    metrics = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "eval_score"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_type", "source_id", "-created_at"]),
            models.Index(fields=["run", "source_id"]),
            models.Index(fields=["project", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.source_type}:{self.source_id} = {self.score}"


class SignalSubscription(models.Model):
    """Which signals a project watches; a null capability watches every capability."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="signal_subscriptions"
    )
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.CASCADE,
        related_name="signal_subscriptions",
        null=True,
        blank=True,
    )
    signal_type = models.CharField(max_length=64)
    enabled = models.BooleanField(default=True)
    # Overrides of the signal's class attributes, e.g. {"times": 5}.
    config = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["signal_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "capability", "signal_type"],
                name="uniq_signal_subscription",
            ),
            models.UniqueConstraint(
                fields=["project", "signal_type"],
                condition=models.Q(capability__isnull=True),
                name="uniq_signal_subscription_project",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.signal_type} @ {self.project_id}"


class SignalEvent(models.Model):
    """One signal occurrence. ``dedupe_key`` collapses repeats within a day."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="signal_events"
    )
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="signal_events",
    )
    subscription = models.ForeignKey(
        SignalSubscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    signal_type = models.CharField(max_length=64)
    conversation = models.ForeignKey(
        "overbae.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="signal_events",
    )
    trace_id = models.CharField(max_length=32, blank=True, default="")
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    dedupe_key = models.CharField(max_length=64)
    occurred_at = models.DateTimeField(auto_now_add=True)
    seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "dedupe_key"], name="uniq_signal_event")
        ]
        indexes = [
            models.Index(fields=["project", "-occurred_at"]),
            models.Index(
                fields=["project", "-occurred_at"],
                condition=models.Q(seen_at__isnull=True),
                name="signal_event_unseen",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.signal_type}: {self.message[:60]}"


class LiveScoringScheduler(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_tick_at = models.DateTimeField(null=True)


class LiveScoringProject(models.Model):
    last_kind = models.CharField(max_length=16, blank=True, default="")
    project = models.OneToOneField("overbae.Project", primary_key=True, on_delete=models.CASCADE)
    last_admitted_at = models.DateTimeField(null=True)


class LiveScoringWork(models.Model):
    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE)
    kind = models.CharField(max_length=16)
    key = models.CharField(max_length=64)
    state = models.CharField(max_length=16, default="waiting")
    task_id = models.CharField(max_length=36, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    queued_at = models.DateTimeField(null=True)
    started_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "kind", "key"], name="live_scoring_identity")
        ]
        indexes = [
            models.Index(fields=["state", "expires_at"], name="live_scoring_expiry"),
            models.Index(fields=["finished_at"], name="live_scoring_finished"),
            models.Index(fields=["project", "state", "kind", "id"], name="live_scoring_kind_wait"),
        ]
