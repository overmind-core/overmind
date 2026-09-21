import uuid

from django.conf import settings
from django.db import models


class Dataset(models.Model):
    """A source and a linear chain of cells. Every cell is a version of the
    table; rows live only as Parquet under the media volume. The intent and
    the capability are proposed at landing and fixed by the first use."""

    class SourceKind(models.TextChoices):
        FILE = "file"
        TRACES = "traces"

    class Intent(models.TextChoices):
        TRAIN = "train"
        EVAL = "eval"
        PENDING = "pending"

    class State(models.TextChoices):
        LANDING = "landing"
        DIAGNOSING = "diagnosing"
        IDLE = "idle"
        RUNNING = "running"
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
    # The cell consumers read; null means the last cell that ran.
    active = models.ForeignKey(
        "overbae.Cell", on_delete=models.SET_NULL, related_name="+", null=True, blank=True
    )
    state = models.CharField(max_length=12, choices=State.choices, default=State.LANDING)
    error = models.TextField(blank=True, default="")
    # [{role: user|agent, text, cells: [cell ids], at}] — the one conversation.
    chat = models.JSONField(default=list, blank=True)
    agent_id = models.CharField(max_length=128, blank=True, default="")
    agent_messages = models.JSONField(default=list, blank=True)
    agent_turn_key = models.CharField(max_length=255, blank=True, default="")
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
    def chain(self) -> list["Cell"]:
        """Every cell in position order, proposals last."""
        return list(self.cells.order_by("position"))

    @property
    def source(self) -> "Cell | None":
        return self.cells.filter(position=0).first()

    @property
    def active_cell(self) -> "Cell | None":
        """The chosen cell when it still stands, else the last cell that ran."""
        if self.active_id is not None:
            cell = self.cells.filter(pk=self.active_id, state=Cell.State.OK).first()
            if cell is not None:
                return cell
        return self.cells.filter(state=Cell.State.OK).order_by("-position").first()

    @property
    def frozen_before(self) -> int:
        """Positions at or below this are frozen: a used cell and everything it reads."""
        used = self.cells.filter(used_at__isnull=False).order_by("-position").first()
        return used.position if used is not None else -1

    def versions(self, *, chain: list["Cell"] | None = None) -> dict[uuid.UUID, str]:
        """Cell id → ``major.minor``. The source is 1.0; a use starts a new major."""
        out: dict[uuid.UUID, str] = {}
        major, minor = 1, 0
        for cell in self.chain if chain is None else chain:
            if cell.state == Cell.State.PROPOSED:
                continue
            if cell.position == 0:
                out[cell.id] = "1.0"
                continue
            if cell.used_at is not None:
                major, minor = major + 1, 0
            else:
                minor += 1
            out[cell.id] = f"{major}.{minor}"
        return out


class Cell(models.Model):
    """One transformation and the frame it left. Position 0 is the source. A
    used cell is frozen; consumers PROTECT it."""

    class State(models.TextChoices):
        PROPOSED = "proposed"
        QUEUED = "queued"
        RUNNING = "running"
        OK = "ok"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="cells")
    position = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    # A Python body over ``df``; empty for the source.
    script = models.TextField(blank=True, default="")
    note = models.CharField(max_length=512, blank=True, default="")
    state = models.CharField(max_length=8, choices=State.choices, default=State.QUEUED)
    error = models.TextField(blank=True, default="")
    rows = models.PositiveIntegerField(default=0)
    # [{name, type, null_rate}] of the frame.
    columns = models.JSONField(default=list, blank=True)
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    # Fingerprint of the frame this cell read; a mismatch means it must run again.
    input_fingerprint = models.CharField(max_length=64, blank=True, default="")
    # {train: {ok, reason}, eval: {ok, reason}}
    intent_report = models.JSONField(default=dict, blank=True)
    # {ok, rows, rows_ok, reason} against the dataset's capability.
    capability_report = models.JSONField(default=dict, blank=True)
    # The six numbers training planners read; see contract.stats.
    stats = models.JSONField(default=dict, blank=True)
    review = models.JSONField(default=dict, blank=True)
    quality_report = models.JSONField(default=dict, blank=True)
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
        ]

    def __str__(self):
        return f"{self.dataset_id} #{self.position} {self.title}"

    @property
    def is_source(self) -> bool:
        return self.position == 0

    @property
    def frozen(self) -> bool:
        return self.used_at is not None or self.position <= self.dataset.frozen_before

    @property
    def ran(self) -> bool:
        return self.state == self.State.OK and bool(self.fingerprint)

    def fits(self, intent: str) -> tuple[bool, str]:
        """Whether a consumer can read this version; quality findings are advisory."""
        if intent not in (Dataset.Intent.TRAIN, Dataset.Intent.EVAL):
            return False, "The intent is still pending. Choose train or eval."
        if not self.ran:
            return False, "This cell has not run."
        shape = (self.intent_report or {}).get(intent) or {}
        if not shape.get("ok"):
            return False, str(shape.get("reason") or f"the table is not a {intent} table")
        return True, ""
