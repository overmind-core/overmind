"""Trajectories live inline as ``JSONField`` because no object storage is
configured; the normalizer truncates oversized blobs.
"""

from __future__ import annotations

import contextlib
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce


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


class EvaluatorQuerySet(models.QuerySet):
    def _latest_version_ids(self, *key_fields):
        """ids of the highest-``version`` row per ``key_fields`` group."""
        latest_id_by_key: dict[tuple, object] = {}
        for row in self.order_by(*key_fields, "-version").values("id", *key_fields):
            key = tuple(row[field] for field in key_fields)
            latest_id_by_key.setdefault(key, row["id"])
        return list(latest_id_by_key.values())

    def library_for_project(self, project_id):
        """Selectable library evaluators for a dataset's project: project-scoped
        rows plus the global managed templates.

        Capability-scoped rows are bespoke to their capability (including legacy
        eval-matrix sentinels, see ``eval_set.NON_GRADING_NAMES``) and must
        never be pickable. Version history collapses to the latest row per
        ``(project, name)``. Generator-authored rows belong to the live bespoke
        generation and would otherwise surface here as a stale, unscored tail,
        because the cached semantic ranking predates them.
        """
        from overbae.services.eval.specs import (
            AUTHORED_GENERATORS,  # noqa: PLC0415 — avoid import cycle at module load
        )

        # Coalesce to "" keeps the exclude null-safe: rows without a provenance
        # key hold SQL NULL, and ``NULL NOT IN (...)`` is NULL, so a bare
        # ``__in`` exclude would drop them as well.
        generator = Coalesce(
            KeyTextTransform("generator", KeyTextTransform("provenance", "config")),
            Value(""),
            output_field=models.TextField(),
        )
        candidates = (
            self.filter(
                models.Q(project_id=project_id) | models.Q(is_managed=True, project__isnull=True),
                is_archived=False,
                capability__isnull=True,
            )
            .annotate(_provenance_generator=generator)
            .exclude(_provenance_generator__in=tuple(AUTHORED_GENERATORS))
        )
        latest_ids = candidates._latest_version_ids("project_id", "name")
        return self.filter(id__in=latest_ids).order_by("project_id", "name", "-version")

    def visible_catalog(self):
        """The full user-facing evaluator catalog for the evals page: both
        generic (null ``capability``) and bespoke graders, unlike
        :meth:`library_for_project`. Only the internal config/weight sentinels
        are excluded, and version history collapses to the latest live row per
        ``(project, capability, name)``. Call on an already project-scoped queryset.
        """
        from overbae.services.eval.eval_set import (  # noqa: PLC0415 — avoid import cycle
            NON_GRADING_NAMES,
            NON_GRADING_ROLES,
        )

        capability_spec_role = Coalesce(
            KeyTextTransform("capability_spec_role", "config"),
            Value(""),
            output_field=models.TextField(),
        )
        candidates = (
            self.filter(is_archived=False)
            .exclude(name__in=tuple(NON_GRADING_NAMES))
            .annotate(_capability_spec_role=capability_spec_role)
            .exclude(_capability_spec_role__in=tuple(NON_GRADING_ROLES))
        )
        latest_ids = candidates._latest_version_ids("project_id", "capability_id", "name")
        return (
            self.filter(id__in=latest_ids)
            .select_related("capability")
            .order_by("project_id", "capability_id", "name", "-version")
        )


class Evaluator(models.Model):
    """Edits write a new ``version`` row so past runs stay reproducible against
    the exact rubric that produced them."""

    class Kind(models.TextChoices):
        LLM_JUDGE = "llm_judge"
        TRAJECTORY = "trajectory"
        DETERMINISTIC = "deterministic"
        STATISTICAL = "statistical"
        AGENTIC = "agentic"

    class Scope(models.TextChoices):
        FINAL_OUTPUT = "final_output"
        TURN = "turn"
        STEP = "step"
        TRAJECTORY = "trajectory"
        SAMPLE = "sample"
        DATASET = "dataset"

    class ScoreType(models.TextChoices):
        NUMERIC = "numeric"
        CATEGORICAL = "categorical"
        BOOLEAN = "boolean"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project",
        on_delete=models.CASCADE,
        related_name="evaluators",
        null=True,
        blank=True,
    )
    # Set only for rows belonging to one capability's eval matrix, never for
    # project-wide rows or global managed templates.
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.CASCADE,
        related_name="evaluators",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=255)
    # Human-readable label for UI display; ``name`` stays the machine identity.
    display_name = models.CharField(max_length=255, blank=True, default="")
    version = models.PositiveIntegerField(default=1)
    description = models.TextField(blank=True, default="")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.FINAL_OUTPUT)

    rubric_md = models.TextField(blank=True, default="")
    # Compiled atomic checklist: [{id, q, weight, gate}].
    checklist = models.JSONField(default=list, blank=True)
    # "" => resolve via TaskType.JUDGE_SCORING; otherwise a ModelRef id or a
    # catalog model name.
    judge_model = models.CharField(max_length=255, blank=True, default="")
    # Panel-of-judges: list of model identifiers.
    score_type = models.CharField(
        max_length=20, choices=ScoreType.choices, default=ScoreType.NUMERIC
    )
    score_min = models.FloatField(default=0.0)
    score_max = models.FloatField(default=1.0)
    # Discrete label set: [{"label": "correct", "value": 1.0}, ...].
    choices = models.JSONField(default=list, blank=True)
    # Continuous score >= threshold => passed.
    pass_threshold = models.FloatField(null=True, blank=True)

    # Drives the creation-time warning and the execution-time skip: a
    # ``harness_artifact`` evaluator on a generate variant is not-applicable.
    class EvidenceRequirement(models.TextChoices):
        MODEL_OUTPUT = "model_output"
        HARNESS_ARTIFACT = "harness_artifact"
        TRAJECTORY = "trajectory"
        REFERENCE = "reference"

    evidence_requirement = models.CharField(
        max_length=20,
        choices=EvidenceRequirement.choices,
        default=EvidenceRequirement.MODEL_OUTPUT,
        db_index=True,
    )

    # A ``model`` grader must not run on live trace scoring; a ``harness`` grader
    # cannot run in generate mode; ``any`` is surface-agnostic (services/eval/roles.py).
    class Surface(models.TextChoices):
        MODEL = "model"
        HARNESS = "harness"
        ANY = "any"

    surface = models.CharField(
        max_length=8,
        choices=Surface.choices,
        default=Surface.ANY,
        db_index=True,
    )

    # "generative" / "trace_scoring". Empty list means derive from scope via
    # ``overbae.services.eval.roles.roles_for_scope``, so no backfill is needed.
    applicable_roles = models.JSONField(default=list, blank=True)

    # Read through the ``spec`` property, which derives one for rows minted
    # before this column existed.
    spec_data = models.JSONField(default=dict, blank=True)

    requires_reference = models.BooleanField(default=False)
    # [{"var": "answer", "source": "output", "jsonpath": "$..content"}].
    variable_mapping = models.JSONField(default=list, blank=True)
    # Family-specific: trajectory match mode, tool-arg match mode, aggregation
    # policy, long_trace_strategy, deterministic check params…
    config = models.JSONField(default=dict, blank=True)

    # Platform-shipped template; ``project`` is null for global ones.
    is_managed = models.BooleanField(default=False, db_index=True)
    is_archived = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_evaluators",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # In-place grading refreshes bump this; the scoring sweep compares it
    # against the pass timestamp to decide a contract changed.
    updated_at = models.DateTimeField(auto_now=True)

    objects = EvaluatorQuerySet.as_manager()

    class Meta:
        ordering = ["name", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "capability", "name", "version"],
                name="uniq_evaluator_version",
            )
        ]
        indexes = [models.Index(fields=["project", "name", "-version"])]

    def __str__(self) -> str:
        return f"{self.name} v{self.version} ({self.kind})"

    def save(self, *args, **kwargs):
        # spec_data is the single spec representation; column-wise writers (the
        # generic API, direct ORM creation) get it derived here. Skipped on
        # partial updates so an untargeted derivation is never silently lost.
        if not self.spec_data and kwargs.get("update_fields") is None:
            from pydantic import ValidationError  # noqa: PLC0415 — cycle via specs

            from overbae.services.eval.specs import derive_spec_data  # noqa: PLC0415 — cycle

            # Columns that don't form a valid spec (unbound variables, no
            # rubric) save anyway; reading .spec on such a row raises, as the
            # derive-at-read fallback always did.
            with contextlib.suppress(ValidationError):
                self.spec_data = derive_spec_data(self)
        super().save(*args, **kwargs)

    def normalize_score(self, score: float) -> float:
        denom = self.score_max - self.score_min
        if denom == 0:
            return 0.0
        scaled = (score - self.score_min) / denom * 100
        return max(0.0, min(100.0, scaled))

    def requires_checklist(self) -> bool:
        """A generative run scores an LLM judge from its checklist verdicts, so
        an empty checklist leaves nothing to grade. Exempt: trace scoring asks
        the model for a score directly, sentinel rows are never executed, and a
        proportional-mode judge enumerates its own claims instead of a checklist."""
        from overbae.services.eval import roles
        from overbae.services.eval.eval_set import NON_GRADING_NAMES, NON_GRADING_ROLES
        from overbae.services.eval.evaluators import gen_judge

        if self.kind not in (self.Kind.LLM_JUDGE, self.Kind.AGENTIC):
            return False
        role = (self.config or {}).get("capability_spec_role")
        if role in NON_GRADING_ROLES or self.name in NON_GRADING_NAMES:
            return False
        if gen_judge.is_proportional(self):
            return False
        return roles.GENERATIVE in roles.roles_for_evaluator(self)

    def unbound_checklist_variables(self) -> list[str]:
        """Checklist item text referencing ``{{var}}`` placeholders nothing binds.

        The prompt renders each question verbatim and lists the bound variables
        under it, so an unbound reference asks the judge about evidence it was
        never given — which it will answer anyway, from nothing.
        """
        from overbae.services.eval.evaluators.base import default_variable_mapping
        from overbae.services.eval.rubric_compiler import referenced_variables

        referenced: set[str] = set()
        for item in self.checklist or []:
            if isinstance(item, dict):
                referenced.update(referenced_variables(str(item.get("q") or "")))
        if not referenced:
            return []
        mapping = self.variable_mapping or default_variable_mapping()
        bound = {str(m.get("var")) for m in mapping if isinstance(m, dict) and m.get("var")}
        return sorted(referenced - bound)

    @property
    def spec(self):
        """The validated ``EvaluatorSpec`` for this row, from ``spec_data``."""
        from overbae.services.eval.specs import spec_for_evaluator  # noqa: PLC0415 — cycle

        cached = getattr(self, "_spec_cache", None)
        if cached is None:
            cached = spec_for_evaluator(self)
            self._spec_cache = cached
        return cached


class EvalSet(models.Model):
    """``Capability.active_eval_set`` drives the optimizer, backtest and default
    wizard selection. No approval step: runnable as soon as it has members."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="eval_sets"
    )
    capability = models.ForeignKey(
        "overbae.Capability", on_delete=models.CASCADE, related_name="eval_sets"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    # Empty => the capability's current prompt. Otherwise each selected prompt scopes
    # the generative members at run-expansion time: one RunEvaluator per member
    # per prompt (see ``eval_set.expand_to_run_evaluators``).
    prompts = models.ManyToManyField(
        "overbae.Prompt",
        related_name="+",
        blank=True,
    )

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
        indexes = [models.Index(fields=["capability", "-created_at"])]

    def __str__(self) -> str:
        return f"EvalSet {self.name} ({self.capability_id})"


class EvalSetMember(models.Model):
    """``sampling_rate`` and ``prompt`` are stored for live trace scoring and not read yet."""

    class Role(models.TextChoices):
        GENERATIVE = "generative"
        TRACE_SCORING = "trace_scoring"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eval_set = models.ForeignKey(EvalSet, on_delete=models.CASCADE, related_name="members")
    evaluator = models.ForeignKey(
        Evaluator, on_delete=models.CASCADE, related_name="set_memberships"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.GENERATIVE)
    enabled = models.BooleanField(default=True)
    sampling_rate = models.FloatField(default=1.0)
    # Mirrors ``RunEvaluator.prompt``; null => global.
    prompt = models.ForeignKey(
        "overbae.Prompt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["eval_set", "evaluator", "role"],
                name="uniq_eval_set_member_role",
            )
        ]
        indexes = [models.Index(fields=["eval_set", "role", "order"])]

    def __str__(self) -> str:
        return f"{self.role} member {self.evaluator_id} @ {self.eval_set_id}"


class EvalRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"

    class DataSource(models.TextChoices):
        DATASET = "dataset"
        TRACE_FILTER = "trace_filter"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="eval_runs"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    data_source = models.CharField(
        max_length=20, choices=DataSource.choices, default=DataSource.DATASET
    )
    dataset = models.ForeignKey(
        "overbae.Dataset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eval_runs",
    )
    # When data_source == trace_filter: a SpanFilter-compatible query.
    trace_filter = models.JSONField(default=dict, blank=True)
    # The cell the run read its rows from; PROTECT keeps that frame forever.
    cell = models.ForeignKey(
        "overbae.Cell",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="eval_runs",
    )
    # Spans/datapoints to draw from the source; 0 => no cap.
    max_items = models.PositiveIntegerField(default=100)

    # Provenance only, and null when an advanced run attached its evaluators
    # directly instead of expanding a set.
    eval_set = models.ForeignKey(
        "overbae.EvalSet",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eval_runs",
    )

    evaluators = models.ManyToManyField(
        Evaluator, through="RunEvaluator", related_name="runs", blank=True
    )
    sampling = models.FloatField(default=1.0)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    error = models.TextField(blank=True, default="")
    # Per-variant rollups, deltas, ranking.
    summary = models.JSONField(default=dict, blank=True)

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
        indexes = [models.Index(fields=["project", "-created_at"])]

    def __str__(self) -> str:
        return f"EvalRun {self.name} ({self.status})"

    @property
    def is_terminal(self) -> bool:
        return self.status in {self.Status.COMPLETED, self.Status.FAILED, self.Status.CANCELLED}


class EvalVariant(models.Model):
    """One model "column" in a run. ``existing`` evaluates already-ingested
    traces as-is; ``generate`` replays the agent loop on each datapoint to
    produce fresh traces.
    """

    class Mode(models.TextChoices):
        GENERATE = "generate"
        EXISTING = "existing"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(EvalRun, on_delete=models.CASCADE, related_name="variants")
    label = models.CharField(max_length=255)
    model_ref = models.ForeignKey(
        ModelRef,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="variants",
    )
    # Fallback when no ModelRef: a catalog model name (e.g. "gpt-5-mini").
    model_name = models.CharField(max_length=255, blank=True, default="")
    prompt = models.ForeignKey(
        "overbae.Prompt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eval_variants",
    )
    params = models.JSONField(default=dict, blank=True)
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.EXISTING)
    is_baseline = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "created_at"]
        indexes = [models.Index(fields=["run", "order"])]

    def __str__(self) -> str:
        return f"{self.label} ({self.mode})"

    @property
    def resolved_model(self) -> str:
        if self.model_ref_id and self.model_ref:
            return self.model_ref.model_id
        return self.model_name


class RunEvaluator(models.Model):
    """Grading reads ``snapshot``, frozen at attach time, so a run stays
    reproducible when the library evaluator is later edited; ``evaluator`` is
    provenance only."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(EvalRun, on_delete=models.CASCADE, related_name="run_evaluators")
    evaluator = models.ForeignKey(
        Evaluator,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="run_links",
    )
    # Null => grades every variant. When set, the binding grades only samples
    # whose ``variant.prompt_id`` matches, so one evaluator bound to two prompts
    # is two rows, each with its own snapshot.
    prompt = models.ForeignKey(
        "overbae.Prompt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="run_evaluators",
    )
    # name, kind, scope, rubric, checklist, score schema, variable_mapping, …
    snapshot = models.JSONField(default=dict)
    scope_override = models.CharField(max_length=20, blank=True, default="")
    sampling = models.FloatField(default=1.0)
    enabled = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "created_at"]
        indexes = [models.Index(fields=["run", "order"])]

    def __str__(self) -> str:
        return f"{self.snapshot.get('name', 'evaluator')} @ {self.run_id}"

    @property
    def name(self) -> str:
        return self.snapshot.get("name", "")

    @property
    def kind(self) -> str:
        return self.snapshot.get("kind", "")


class EvalSample(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(EvalRun, on_delete=models.CASCADE, related_name="samples")
    variant = models.ForeignKey(EvalVariant, on_delete=models.CASCADE, related_name="samples")
    # The row this sample grades: ``row_index`` into the run's pinned
    # ``checkpoint`` frame. Null for trace-filter runs.
    row_index = models.PositiveIntegerField(null=True, blank=True)
    # Source Span tree, when evaluating existing traces or once a generate run
    # has written its spans.
    source_trace_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    # This sample's ``prepare_sample`` task, recorded at dispatch so a run cancel
    # can revoke it instead of letting a hung generation hold a worker thread.
    celery_task_id = models.CharField(max_length=255, blank=True, default="")

    # ChatML messages + tool_definitions + final_output + metadata, size-guarded
    # by the normalizer.
    trajectory = models.JSONField(default=dict, blank=True)
    # Tool-call graph, turns, salient steps.
    structured = models.JSONField(default=dict, blank=True)
    expected = models.JSONField(null=True, blank=True)

    # Fraction of the trace the judges saw; < 1.0 means the long-trace cascade
    # truncated or summarised it.
    context_coverage = models.FloatField(default=1.0)
    error = models.TextField(blank=True, default="")

    # Degraded samples stay out of run aggregates so a pipeline-caused 0 is
    # never read as a model failure.
    degraded = models.BooleanField(default=False)
    degraded_reason = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["run", "variant"]),
            models.Index(fields=["run", "row_index"]),
        ]

    def __str__(self) -> str:
        return f"Sample {self.id} ({self.variant_id})"


class Score(models.Model):
    """Append-only; every evaluator family writes here."""

    class DataType(models.TextChoices):
        NUMERIC = "numeric"
        CATEGORICAL = "categorical"
        BOOLEAN = "boolean"

    class FailureRole(models.TextChoices):
        NONE = "none"
        ROOT_CAUSE = "root_cause"
        PROPAGATED = "propagated"

    class Source(models.TextChoices):
        EVAL = "eval"
        HUMAN = "human_annotation"
        API = "api"

    class Outcome(models.TextChoices):
        # A null ``value`` alone is ambiguous (abstain vs not-applicable vs error
        # vs skip), so intent is typed here instead of inferred.
        # Invariant: ``value is None`` ⇔ ``outcome != SCORED``.
        SCORED = "scored"
        ABSTAINED = "abstained"
        NOT_APPLICABLE = "not_applicable"
        SKIPPED = "skipped"
        ERROR = "error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="eval_scores"
    )
    run = models.ForeignKey(
        EvalRun, on_delete=models.CASCADE, null=True, blank=True, related_name="scores"
    )
    variant = models.ForeignKey(
        EvalVariant, on_delete=models.CASCADE, null=True, blank=True, related_name="scores"
    )
    sample = models.ForeignKey(
        EvalSample, on_delete=models.CASCADE, null=True, blank=True, related_name="scores"
    )
    evaluator = models.ForeignKey(
        Evaluator, on_delete=models.SET_NULL, null=True, blank=True, related_name="scores"
    )
    # The snapshot that produced this score; also the idempotency handle.
    run_evaluator = models.ForeignKey(
        "overbae.RunEvaluator",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scores",
    )

    scope = models.CharField(max_length=20, default=Evaluator.Scope.SAMPLE)
    # span_id / turn index / "" for whole-sample.
    target_ref = models.CharField(max_length=64, blank=True, default="")

    name = models.CharField(max_length=128)
    data_type = models.CharField(max_length=20, choices=DataType.choices, default=DataType.NUMERIC)
    value = models.FloatField(null=True, blank=True)
    string_value = models.CharField(max_length=256, blank=True, default="")
    passed = models.BooleanField(null=True)
    # Only ``SCORED`` rows feed means and pass-rates; the rest surface separately.
    outcome = models.CharField(
        max_length=20, choices=Outcome.choices, default=Outcome.SCORED, db_index=True
    )

    reasoning = models.TextField(blank=True, default="")
    # Per-checklist-item / per-step breakdown.
    sub_scores = models.JSONField(default=list, blank=True)
    failure_role = models.CharField(
        max_length=20, choices=FailureRole.choices, default=FailureRole.NONE
    )
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.EVAL)
    # The judge call's own trace, for debugging and cost.
    judge_trace_id = models.CharField(max_length=64, blank=True, default="")
    cost = models.FloatField(default=0.0)
    latency_ms = models.FloatField(default=0.0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["run", "variant", "name"]),
            models.Index(fields=["sample", "name"]),
            models.Index(fields=["project", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name}={self.value if self.value is not None else self.string_value}"


class Annotation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="eval_annotations"
    )
    sample = models.ForeignKey(EvalSample, on_delete=models.CASCADE, related_name="annotations")
    evaluator = models.ForeignKey(
        Evaluator, on_delete=models.SET_NULL, null=True, blank=True, related_name="annotations"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eval_annotations",
    )
    value = models.FloatField(null=True, blank=True)
    label = models.CharField(max_length=128, blank=True, default="")
    note = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["sample", "-created_at"])]

    def __str__(self) -> str:
        return f"Annotation {self.id} ({self.value or self.label})"


class Verdict(models.Model):
    """The uniqueness key is the idempotency checkpoint (same contract upserts),
    the disagreement mechanism (another judge coexists under a different
    ``identifier``) and the rescore-series boundary."""

    class TargetKind(models.TextChoices):
        SPAN = "span"
        TRACE = "trace"

    class Outcome(models.TextChoices):
        SCORED = "scored"
        ABSTAINED = "abstained"
        NOT_APPLICABLE = "not_applicable"
        ERROR = "error"

    class AnnotatorKind(models.TextChoices):
        LLM = "llm"
        CODE = "code"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="verdicts"
    )
    evaluator = models.ForeignKey(
        Evaluator, on_delete=models.SET_NULL, null=True, blank=True, related_name="verdicts"
    )
    # Survives evaluator deletion; part of the uniqueness key.
    evaluator_name = models.CharField(max_length=255)
    target_kind = models.CharField(max_length=16, choices=TargetKind.choices)
    target_id = models.CharField(max_length=64, db_index=True)

    label = models.CharField(max_length=64, blank=True, default="")
    # Invariant: score is None ⇔ outcome != SCORED; scored values are in [0, 1].
    score = models.FloatField(null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, default=Outcome.SCORED)
    explanation = models.TextField(blank=True, default="")
    # Warrant clauses unmet, on abstention — machine-readable, never prose-only.
    unmet = models.JSONField(default=list, blank=True)
    # Per-claim verdicts, panel members, sub-scores, short-circuit markers…
    metadata = models.JSONField(default=dict, blank=True)

    annotator_kind = models.CharField(
        max_length=8, choices=AnnotatorKind.choices, default=AnnotatorKind.LLM
    )
    # Judge contract "{provider}:{model}:{rubric_hash}" for LLM verdicts,
    # user id for human ones, "" for deterministic code.
    identifier = models.CharField(max_length=255, blank=True, default="")
    judge_trace_id = models.CharField(max_length=64, blank=True, default="")

    # None = unknown; a pass total with any unknown poisons to None.
    cost = models.FloatField(null=True, blank=True)
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["evaluator_name", "target_kind", "target_id", "identifier"],
                name="uniq_verdict_series",
            )
        ]
        indexes = [
            models.Index(fields=["project", "target_kind", "target_id"]),
            models.Index(fields=["evaluator_name", "outcome"]),
        ]

    def __str__(self) -> str:
        return f"{self.evaluator_name}@{self.target_kind}:{self.target_id} = {self.label or self.score}"


class ScoringPass(models.Model):
    """A finished pass is what the sweep checks instead of re-reading feedback blocks."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="scoring_passes"
    )
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scoring_passes",
    )
    trace_id = models.CharField(max_length=64, db_index=True)
    started = models.DateTimeField(auto_now_add=True)
    finished = models.DateTimeField(null=True, blank=True)
    # {"scored": n, "abstained": n, "not_applicable": n, "skipped": n,
    #  "error": n, "skipped_existing": n, "deferred": n}
    verdict_counts = models.JSONField(default=dict, blank=True)
    total_cost = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-started"]
        indexes = [models.Index(fields=["project", "trace_id", "-started"])]

    def __str__(self) -> str:
        return f"ScoringPass {self.trace_id} ({self.started:%Y-%m-%d %H:%M})"


class EvidenceProfile(models.Model):
    """Derived at ingest with hysteresis so path variance doesn't flap the grades;
    authoring refuses specs whose warrant the telemetry cannot satisfy."""

    capability = models.OneToOneField(
        "overbae.Capability",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="evidence_profile",
    )
    # {task: declared|inferred|absent, units: explicit|inferred,
    #  tool_ops: real|dispatcher|untraced, provenance: tagged|partial|untagged,
    #  observations: rich|sparse|none, delivery: declared|heuristic}
    grades = models.JSONField(default=dict, blank=True)
    # Hysteresis state: per-clause candidate grade + streak, plus trace counts.
    window = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"EvidenceProfile {self.capability_id}"


class JudgeCache(models.Model):
    """Keyed by hash of (model, prompt, output-schema): judge calls are treated
    as deterministic for a fixed triple. ``raw`` is re-parsed on read."""

    key = models.CharField(max_length=64, primary_key=True)  # sha256 hex
    raw = models.TextField()
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    hits = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["-last_used_at"])]

    def __str__(self) -> str:
        return f"JudgeCache {self.key[:12]}… (hits={self.hits})"
