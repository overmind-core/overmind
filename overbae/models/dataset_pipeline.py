import uuid

from django.conf import settings
from django.db import models


class DatasetPipeline(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="pipelines"
    )
    family = models.UUIDField(default=uuid.uuid4, db_index=True)
    revision = models.PositiveIntegerField(default=1)
    parent = models.ForeignKey(
        "self", null=True, on_delete=models.RESTRICT, related_name="revisions"
    )
    derived_from = models.ForeignKey(
        "self", null=True, on_delete=models.RESTRICT, related_name="derivatives"
    )
    package = models.ForeignKey(
        "overbae.DatasetPipelinePackage", null=True, on_delete=models.RESTRICT
    )
    name = models.CharField(max_length=255)
    request_key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    steps = models.JSONField(default=list)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "request_key"], name="unique_project_pipeline_request"
            ),
            models.UniqueConstraint(
                fields=["project", "family", "revision"], name="unique_pipeline_revision"
            ),
        ]


class DatasetPipelinePackage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    bundle = models.FileField(upload_to="pipeline-packages/%Y/%m/")
    manifest = models.JSONField()
    inventory = models.JSONField(default=list)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "sha256"], name="unique_pipeline_package")
        ]


class DatasetPipelineRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        "overbae.Dataset", on_delete=models.CASCADE, related_name="pipeline_runs"
    )
    pipeline = models.ForeignKey(DatasetPipeline, null=True, on_delete=models.RESTRICT)
    source = models.ForeignKey(
        "overbae.Cell", on_delete=models.RESTRICT, related_name="pipeline_inputs"
    )
    source_fingerprint = models.CharField(max_length=64)
    artifact = models.ForeignKey(
        "overbae.Cell", null=True, on_delete=models.RESTRICT, related_name="pipeline_artifacts"
    )
    artifact_fingerprint = models.CharField(max_length=64, blank=True, default="")
    output = models.ForeignKey(
        "overbae.Cell", null=True, on_delete=models.RESTRICT, related_name="pipeline_outputs"
    )
    request_key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    specification = models.JSONField(default=dict)
    mode = models.CharField(max_length=16, default="publish")
    attempt = models.UUIDField(default=uuid.uuid4)
    container_id = models.CharField(max_length=128, blank=True, default="")
    binding = models.ForeignKey(
        "overbae.DatasetPipelineBinding", null=True, on_delete=models.RESTRICT, related_name="runs"
    )
    state = models.CharField(max_length=16, default="queued", db_index=True)
    error = models.TextField(blank=True, default="")
    result = models.JSONField(default=dict)
    lease_until = models.DateTimeField(null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "request_key"], name="unique_pipeline_run_request"
            )
        ]


class DatasetPipelineBinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE)
    dataset = models.ForeignKey(
        "overbae.Dataset", on_delete=models.CASCADE, related_name="pipeline_bindings"
    )
    pipeline = models.ForeignKey(DatasetPipeline, on_delete=models.RESTRICT)
    source_dataset = models.ForeignKey(
        "overbae.Dataset", null=True, on_delete=models.RESTRICT, related_name="downstream_bindings"
    )
    trace_source = models.JSONField(default=dict)
    parameters = models.JSONField(default=dict)
    trigger = models.CharField(max_length=16, default="manual")
    interval_seconds = models.PositiveIntegerField(default=60)
    max_runs = models.PositiveIntegerField(default=1000)
    max_source_rows = models.PositiveIntegerField(default=1000000)
    runs_started = models.PositiveIntegerField(default=0)
    request_key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    version = models.PositiveIntegerField(default=1)
    enabled = models.BooleanField(default=False)
    checkpoint = models.CharField(max_length=64, blank=True, default="")
    next_check_at = models.DateTimeField(null=True)
    last_checked_at = models.DateTimeField(null=True)
    error = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "request_key"], name="unique_pipeline_binding_request"
            )
        ]


class DatasetPipelineRunner(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    images = models.JSONField(default=list)
    heartbeat_at = models.DateTimeField()
    status = models.CharField(max_length=32)
