import uuid

from django.db import models
from django.utils import timezone


class CapabilityQuerySet(models.QuerySet):
    def current(self):
        return self.filter(status=Capability.Status.CURRENT)


class Capability(models.Model):
    """One purpose inside the project's agent — a node of the product graph.

    The agent itself is the project; it has no row. Scans carry or reactivate
    these rows and never delete one; a hand delete only hides, so every uuid is
    stable for life."""

    class Status(models.TextChoices):
        # In the latest scan, or minted from runtime traffic and never retired.
        CURRENT = "current", "Current"
        # Not reproduced by the latest scan; reactivates in place when the code returns.
        LEFTOVER = "leftover", "Leftover"
        # Deleted by hand: invisible to lists, scans, and identity lookup. A later
        # scan that finds the same code mints a fresh row.
        DELETED = "deleted", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="capabilities"
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    description = models.TextField(blank=True, default="")
    source_path = models.CharField(max_length=512, blank=True, default="")
    entrypoint_fn = models.CharField(max_length=255, blank=True, default="")
    model = models.CharField(max_length=128, blank=True, default="")
    analyzer_model = models.CharField(max_length=128, blank=True, default="")
    cli_version = models.CharField(max_length=20, blank=True, default="unknown")

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.CURRENT, db_index=True
    )
    # Minted from runtime traffic or by hand, never by a scan — so a scan that
    # cannot see it must not retire it.
    observed = models.BooleanField(default=False)
    # The fingerprint the matcher last carried this row on; the next scan
    # scores candidates against it instead of re-deriving from the row.
    footprint = models.JSONField(default=dict, blank=True)

    # Eval spec flattened from the SDK's eval_spec.json; these columns feed the
    # optimizer and the eval_spec endpoint.
    input_schema = models.JSONField(default=dict, blank=True)
    output_fields = models.JSONField(default=dict, blank=True)
    output_schema = models.JSONField(default=dict, blank=True)
    structure_weight = models.FloatField(default=20.0)
    total_points = models.FloatField(default=100.0)
    tool_config = models.JSONField(default=dict, blank=True)
    tool_usage_weight = models.FloatField(default=10.0)
    consistency_rules = models.JSONField(default=list, blank=True)
    optimizable_elements = models.JSONField(default=list, blank=True)
    fixed_elements = models.JSONField(default=list, blank=True)
    policy_markdown = models.TextField(blank=True, default="")
    tools_summary = models.TextField(blank=True, default="")
    decision_logic = models.TextField(blank=True, default="")

    active_eval_set = models.ForeignKey(
        "overbae.EvalSet",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Deployment behind "overmind/<capability-uuid>". Null makes the alias 404 —
    # there is deliberately no fallback to the free-text ``model`` field, which
    # would bill a misconfiguration as frontier inference. SET_NULL because
    # DeployedModelViewSet hard-deletes and must not take the capability with it.
    active_model = models.ForeignKey(
        "overbae.DeployedModel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # Scan payload (capability card, modes, eval matrix, repo stamp) plus keys
    # other subsystems write; scans merge into it, never replace it.
    improvement_metadata = models.JSONField(default=dict, blank=True)

    # Rewritten by overbae.api.otlp.process_span on every ingest.
    usage_stats = models.JSONField(default=dict, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CapabilityQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("project", "slug")]

    def __str__(self):
        return f"{self.project.slug}/{self.name}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Every identity this row has had stays resolvable; renames add, never remove.
        fields = kwargs.get("update_fields")
        if fields is None or {"name", "slug"} & set(fields):
            self.sync_aliases()

    def sync_aliases(self) -> None:
        values = [(IdentityAlias.Kind.ID, str(self.id).lower())]
        if (self.name or "").strip():
            values.append((IdentityAlias.Kind.NAME, self.name.strip().lower()[:512]))
        if (self.slug or "").strip():
            values.append((IdentityAlias.Kind.SLUG, self.slug.strip().lower()[:512]))
        for kind, value in values:
            # The row that carries an identity NOW owns its alias: a rename must
            # bind its new name even when another row held it historically.
            IdentityAlias.objects.update_or_create(
                project_id=self.project_id, kind=kind, value=value, defaults={"capability": self}
            )

    @property
    def is_current(self) -> bool:
        return self.status == self.Status.CURRENT

    def set_status(self, status: str) -> None:
        """``.filter().update()`` like every other transition: no signals fire, so
        the caller projects the graph node itself."""
        Capability.objects.filter(pk=self.pk).update(status=status, updated_at=timezone.now())
        self.status = status


class IdentityAlias(models.Model):
    """Every id, name, and slug a capability has ever had, so telemetry and
    URLs that carry an old identity keep resolving after a rename."""

    class Kind(models.TextChoices):
        ID = "id"
        NAME = "name"
        SLUG = "slug"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="identity_aliases"
    )
    kind = models.CharField(max_length=8, choices=Kind.choices)
    # Stored lowercased; lookups lowercase the probe.
    value = models.CharField(max_length=512)
    capability = models.ForeignKey(
        "overbae.Capability", on_delete=models.CASCADE, related_name="aliases"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("project", "kind", "value")]

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}"


class Prompt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    capability = models.ForeignKey(
        "overbae.Capability", on_delete=models.CASCADE, related_name="prompts"
    )
    version = models.IntegerField(default=1)
    label = models.CharField(max_length=128, blank=True, default="")
    system_prompt = models.TextField(blank=True, default="")
    tools_json = models.JSONField(default=list, blank=True)
    model = models.CharField(max_length=128, blank=True, default="")
    full_capability_code = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]

    def __str__(self):
        return f"{self.capability.name} v{self.version}"
