import uuid

from django.conf import settings
from django.db import models


class WorkshopWorkspace(models.Model):
    project = models.OneToOneField("overbae.Project", on_delete=models.CASCADE, primary_key=True)
    sandbox_id = models.CharField(max_length=128, unique=True, null=True)
    provisioning = models.JSONField(default=dict)
    enabled = models.BooleanField(default=True)
    task_owner = models.UUIDField(null=True)
    generation = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)


class WorkshopTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey("overbae.Dataset", on_delete=models.RESTRICT)
    project = models.ForeignKey("overbae.Project", on_delete=models.RESTRICT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT)
    request_key = models.CharField(max_length=128)
    objective = models.TextField()
    context_sources = models.JSONField(default=list)
    fingerprint = models.CharField(max_length=64)
    state = models.CharField(max_length=24, default="queued", db_index=True)
    desired_state = models.CharField(max_length=24, default="running")
    preparation = models.ForeignKey(
        "overbae.DatasetPreparation", null=True, on_delete=models.RESTRICT
    )
    max_steps = models.PositiveIntegerField(default=0)
    used_tokens = models.PositiveIntegerField(default=0)
    step = models.PositiveIntegerField(default=0)
    result = models.JSONField(default=dict)
    retention = models.JSONField(default=dict)
    dispatched_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "request_key"], name="unique_workshop_task_request"
            )
        ]


class WorkshopAction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(WorkshopTask, on_delete=models.CASCADE, related_name="actions")
    sequence = models.PositiveIntegerField()
    name = models.CharField(max_length=128)
    arguments = models.JSONField(default=dict)
    fingerprint = models.CharField(max_length=64)
    state = models.CharField(max_length=24, default="intent")
    receipt = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["task", "sequence"], name="unique_workshop_action_sequence"
            )
        ]


class WorkshopChatEvent(models.Model):
    task = models.ForeignKey(WorkshopTask, on_delete=models.CASCADE, related_name="chat_events")
    key = models.CharField(max_length=180)
    message = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["task", "key"], name="unique_workshop_chat_event")
        ]
