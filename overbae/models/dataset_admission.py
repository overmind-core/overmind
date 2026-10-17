import uuid

from django.db import models
from django.utils import timezone


class DatasetLandingWork(models.Model):
    class State(models.TextChoices):
        WAITING = "waiting"
        ADMITTED = "admitted"
        RUNNING = "running"
        FINISHED = "finished"
        FAILED = "failed"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("Project", on_delete=models.CASCADE, related_name="landing_work")
    dataset = models.ForeignKey("Dataset", on_delete=models.CASCADE, related_name="landing_work")
    inputs = models.JSONField(default=dict)
    state = models.CharField(max_length=16, choices=State.choices, default=State.WAITING)
    owner = models.UUIDField(null=True, blank=True)
    queued_at = models.DateTimeField(default=timezone.now)
    failure_code = models.CharField(max_length=64, blank=True, default="")
    admitted_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "queued_at"], name="landing_work_waiting"),
            models.Index(fields=["state", "lease_until"], name="landing_work_lease"),
            models.Index(fields=["state", "finished_at"], name="landing_work_finished"),
            models.Index(fields=["project", "state", "queued_at"], name="landing_work_project"),
            models.Index(fields=["project", "-admitted_at"], name="landing_work_fairness"),
        ]
