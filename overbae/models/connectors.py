"""Connector credentials, versioned sync configs, and sync-run history.

api_key and api_secret are stored encrypted at rest using Fernet symmetric
encryption (see overbae/core/encryption.py).  The EncryptedField transparently
encrypts on write and decrypts on read so the rest of the codebase works with
plain strings.

Capability mapping lives on ConnectorCredential (mutable, retroactive) — relabeling
is a correction, not an ingestion filter. Source project + lookback live on
ConnectorSyncConfig (versioned).
"""

from __future__ import annotations

import uuid

from cryptography.fernet import InvalidToken
from django.db import models

from overbae.core.encryption import decrypt, encrypt


class EncryptedField(models.TextField):
    """TextField that transparently encrypts on save and decrypts on load."""

    def from_db_value(self, value, expression, connection):  # noqa: ARG002
        if not value:
            return ""
        try:
            return decrypt(value)
        except InvalidToken:
            if str(value).startswith("gAAAA"):  # a real Fernet envelope must decrypt
                raise
            return value  # Explicit compatibility for pre-encryption plaintext.

    def get_prep_value(self, value):
        if not value:
            return ""
        return encrypt(value)

    def to_python(self, value):
        return value or ""


class ConnectorCredential(models.Model):
    class ConnectorType(models.TextChoices):
        LANGFUSE = "langfuse", "LangFuse"
        LANGSMITH = "langsmith", "LangSmith"
        BRAINTRUST = "braintrust", "Braintrust"
        GALILEO = "galileo", "Galileo"
        OPIK = "opik", "Opik"
        HELICONE = "helicone", "Helicone"
        ARIZE = "arize", "Arize Phoenix"

    class SyncStatus(models.TextChoices):
        IDLE = "idle", "Idle"  # newly connected — waiting for the first backfill
        BACKFILLING = "backfilling", "Backfilling"  # walking historical pages
        LIVE = "live", "Live"  # caught up; incremental polling
        ERROR = "error", "Error"  # last poll failed; retrying with backoff

    class ApiVersion(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        V1 = "v1", "v1 traces"
        V2 = "v2", "v2 observations"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project",
        on_delete=models.CASCADE,
        related_name="connector_credentials",
    )
    name = models.CharField(max_length=255)
    connector_type = models.CharField(max_length=40, choices=ConnectorType.choices)
    base_url = models.CharField(max_length=512, blank=True, default="")

    api_key = EncryptedField(blank=True, default="")
    api_secret = EncryptedField(blank=True, default="")

    is_active = models.BooleanField(default=True)
    # Opt-in gate for the background poller. Default OFF so merely having valid
    # creds never kicks off an unexpected full-history backfill — the user must
    # explicitly enable automatic polling. Manual "Sync now" ignores this flag.
    auto_sync_enabled = models.BooleanField(default=False)
    # How often the live poller re-checks this connector once caught up.
    # Clamped to [60, 86400] at the serializer; the outer beat tick (see
    # settings.CELERY_BEAT_SCHEDULE["poll-connectors"]) is the real floor.
    poll_interval_seconds = models.PositiveIntegerField(default=300)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    sync_status = models.CharField(
        max_length=20, choices=SyncStatus.choices, default=SyncStatus.IDLE
    )
    # Provider-agnostic checkpoint (opaque per-adapter JSON).
    sync_cursor = models.JSONField(default=dict, blank=True)
    backfill_imported = models.PositiveIntegerField(default=0)
    backfill_total = models.PositiveIntegerField(null=True, blank=True)
    sync_error = models.TextField(blank=True, default="")
    sync_retry_count = models.PositiveSmallIntegerField(default=0)
    next_poll_at = models.DateTimeField(null=True, blank=True)

    api_version = models.CharField(
        max_length=16, choices=ApiVersion.choices, default=ApiVersion.UNKNOWN
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    total_spans_imported = models.PositiveIntegerField(default=0)
    total_traces_imported = models.PositiveIntegerField(default=0)

    # Mutable. Shape:
    #   {source: "observation_name"|"metadata"|"tag"|"trace_name",
    #    key: str|null, names: [str], assignments: {<discovered>: <capability uuid>},
    #    auto_create: bool, fallback_capability_id: uuid|null}
    # Assignments are retroactive; changing source regroups traces and needs a re-import.
    capability_mapping = models.JSONField(default=dict, blank=True)
    # MCP stages a proposal here until confirm_mapping=true. Console mapping PUT applies live.
    pending_capability_mapping = models.JSONField(default=dict, blank=True)
    # MCP sync requires True. Console mapping writes set it; new CLI rows stay False.
    capability_mapping_confirmed = models.BooleanField(default=False)
    # source + names + key of the mapping last adopted or imported. Empty means
    # the next sync adopts the live mapping without wiping existing spans.
    imported_boundary_key = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("project", "connector_type", "name")]

    def __str__(self) -> str:
        return f"{self.get_connector_type_display()} — {self.name}"

    @property
    def api_key_hint(self) -> str:
        key = self.api_key
        if not key:
            return ""
        visible = key[:4] if len(key) >= 4 else key
        return f"{visible}{'*' * min(len(key) - 4, 20)}"

    def active_config(self) -> ConnectorSyncConfig | None:
        """Newest sync-config version, or None before first wizard save."""
        return self.configs.order_by("-version").first()


class ConnectorSyncConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    credential = models.ForeignKey(
        ConnectorCredential,
        on_delete=models.CASCADE,
        related_name="configs",
    )
    version = models.PositiveIntegerField()
    source_project_id = models.CharField(max_length=128, blank=True, default="")
    target_project = models.ForeignKey(
        "overbae.Project",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # How far back the initial backfill should look (days). None = unbounded.
    lookback_days = models.PositiveIntegerField(null=True, blank=True)
    backfill_from = models.DateTimeField(null=True, blank=True)
    backfill_to = models.DateTimeField(null=True, blank=True)
    effective_from = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        unique_together = [("credential", "version")]

    def __str__(self) -> str:
        return f"{self.credential_id} config v{self.version}"


class ConnectorSyncRun(models.Model):
    """One poll / backfill / import window — source of UI sync stats."""

    class Mode(models.TextChoices):
        BACKFILL = "backfill", "Backfill"
        LIVE = "live", "Live"
        MANUAL = "manual", "Manual"
        DELTA = "delta", "Delta"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    credential = models.ForeignKey(
        ConnectorCredential,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    config_version = models.PositiveIntegerField(null=True, blank=True)
    mode = models.CharField(max_length=20, choices=Mode.choices)
    window_from = models.DateTimeField(null=True, blank=True)
    window_to = models.DateTimeField(null=True, blank=True)
    traces_seen = models.PositiveIntegerField(default=0)
    spans_created = models.PositiveIntegerField(default=0)
    spans_skipped = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["credential", "-started_at"], name="conn_run_cred_started_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.credential_id} {self.mode} {self.status}"
