import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models


class Dataset(models.Model):
    """A source and a tree of immutable versions (``Cell``); rows live only as
    Parquet under the media volume. The intent and the capability are proposed
    at landing and fixed by the first use."""

    class SourceKind(models.TextChoices):
        FILE = "file"
        TRACES = "traces"
        LLM_CALLS = "llm_calls"

    class Intent(models.TextChoices):
        TRAIN = "train"
        EVAL = "eval"
        PENDING = "pending"

    class State(models.TextChoices):
        LANDING = "landing"
        IDLE = "idle"
        ERROR = "error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="datasets"
    )
    name = models.CharField(max_length=255)
    source_kind = models.CharField(
        max_length=16, choices=SourceKind.choices, default=SourceKind.FILE
    )
    # {filename} for a file; {trace_ids} or a traces-list selection for traces.
    source_spec = models.JSONField(default=dict, blank=True)
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.SET_NULL,
        related_name="datasets",
        null=True,
        blank=True,
    )
    # [{capability_id, name, score, reason}] best first, computed at landing.
    capability_rank = models.JSONField(default=list, blank=True)
    intent = models.CharField(max_length=8, choices=Intent.choices, default=Intent.PENDING)
    # The version consumers read by default; set at landing and only by an explicit choice.
    active = models.ForeignKey(
        "overbae.Cell", on_delete=models.SET_NULL, related_name="+", null=True, blank=True
    )
    state = models.CharField(max_length=12, choices=State.choices, default=State.LANDING)
    error = models.TextField(blank=True, default="")
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
        indexes = [
            models.Index(fields=["project", "-created_at"]),
            models.Index(fields=["capability", "-created_at"]),
        ]

    def __str__(self):
        return self.name or str(self.id)[:8]

    @property
    def versions(self) -> list["Cell"]:
        return list(self.cells.order_by("position"))

    @property
    def source(self) -> "Cell | None":
        return self.cells.filter(parent__isnull=True).first()

    @property
    def active_cell(self) -> "Cell | None":
        return self.active


class DatasetImport(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        BLOCKED = "blocked"
        COMPLETE = "complete"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.OneToOneField(Dataset, on_delete=models.CASCADE, related_name="import_run")
    evaluation = models.ForeignKey(
        Dataset, null=True, blank=True, on_delete=models.SET_NULL, related_name="split_imports"
    )
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    inputs = models.JSONField(default=dict)
    source_manifest = models.JSONField(default=list)
    queued_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    next_publish_at = models.DateTimeField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    owner = models.UUIDField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    publish_attempts = models.PositiveIntegerField(default=0)
    publish_owner = models.UUIDField(null=True, blank=True)
    failure_code = models.CharField(max_length=64, blank=True, default="")
    error = models.TextField(blank=True, default="")
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "next_publish_at"], name="dataset_import_publish"),
            models.Index(fields=["state", "queued_at"], name="dataset_import_queue"),
            models.Index(fields=["state", "lease_until"], name="dataset_import_lease"),
            GinIndex(fields=["source_manifest"], name="dataset_import_sources"),
        ]


class Cell(models.Model):
    """One immutable version: the frame a script left from its parent's frame. The
    source has no parent. A used version is frozen; consumers PROTECT it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="cells")
    # The version the script read. RESTRICT: a dataset delete removes the whole tree,
    # a delete of one version with children is refused.
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.RESTRICT, related_name="children"
    )
    position = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    # A Python body over ``df``; empty for the source.
    script = models.TextField(blank=True, default="")
    script_sha256 = models.CharField(max_length=64, blank=True, default="")
    stdout = models.TextField(blank=True, default="")
    rows = models.PositiveIntegerField(default=0)
    # [{name, type, null_rate}] of the frame.
    columns = models.JSONField(default=list, blank=True)
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    input_fingerprint = models.CharField(max_length=64, blank=True, default="")
    # {train: {ok, reason}, eval: {ok, reason}}
    intent_report = models.JSONField(default=dict, blank=True)
    # {ok, rows, rows_ok, reason} against the dataset's capability.
    capability_report = models.JSONField(default=dict, blank=True)
    # The six numbers training planners read; see contract.stats.
    stats = models.JSONField(default=dict, blank=True)
    impact = models.JSONField(default=dict, blank=True)
    seconds = models.FloatField(default=0.0)
    used_at = models.DateTimeField(null=True, blank=True)
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
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "position"], name="uniq_dataset_cell_position"
            ),
            models.UniqueConstraint(
                fields=["parent", "script_sha256"],
                condition=models.Q(parent__isnull=False),
                name="uniq_cell_parent_script",
            ),
        ]

    def __str__(self):
        return f"{self.dataset_id} v{self.position} {self.title}"

    @property
    def label(self) -> str:
        return f"v{self.position}"

    @property
    def frozen(self) -> bool:
        return self.used_at is not None

    def fits(self, intent: str) -> tuple[bool, str]:
        if intent not in (Dataset.Intent.TRAIN, Dataset.Intent.EVAL):
            return False, "The intent is still pending. Choose train or eval."
        shape = (self.intent_report or {}).get(intent) or {}
        if not shape.get("ok"):
            return False, str(shape.get("reason") or f"the table is not a {intent} table")
        return True, ""


class Execution(models.Model):
    """One script run, successful or failed. Kept 30 days. Transitions use
    ``.filter(id=..., state=<expected>).update(...)``: a zero-row update means another
    worker owns the run."""

    class Mode(models.TextChoices):
        TRY = "try"
        PROBE = "probe"
        CELL = "cell"
        SYNTHETIC = "synthetic"

    class State(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        OK = "ok"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=64)
    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE, related_name="+")
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="executions")
    parent = models.ForeignKey(
        Cell, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    cell = models.ForeignKey(
        Cell, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    mode = models.CharField(max_length=10, choices=Mode.choices)
    title = models.CharField(max_length=120, blank=True, default="")
    # The script, or for synthetic runs the canonical JSON of rows and instruction.
    script = models.TextField(blank=True, default="")
    state = models.CharField(max_length=8, choices=State.choices, default=State.QUEUED)
    # The platform caused the failure; an identical call runs again at once.
    transient = models.BooleanField(default=False)
    error = models.TextField(blank=True, default="")
    stdout = models.TextField(blank=True, default="")
    # try: {rows, columns, head}; empty otherwise.
    result = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    queued_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    queue_seconds = models.FloatField(null=True, blank=True)
    transfer_seconds = models.FloatField(null=True, blank=True)
    run_seconds = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["dataset", "-queued_at"], name="execution_dataset"),
            models.Index(fields=["key", "-queued_at"], name="execution_key"),
            models.Index(
                fields=["project", "state"],
                name="execution_active",
                condition=models.Q(state__in=["queued", "running"]),
            ),
            # The interactive capacity metric counts recent transient failures every minute.
            models.Index(
                fields=["finished_at"],
                name="execution_transient_failed",
                condition=models.Q(state="failed", transient=True),
            ),
        ]
