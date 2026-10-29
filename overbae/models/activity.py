import uuid

from django.conf import settings
from django.db import models


class Activity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("overbae.Project", null=True, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    preparation = models.ForeignKey(
        "overbae.DatasetPreparation", null=True, on_delete=models.SET_NULL
    )
    key = models.CharField(max_length=255, unique=True)
    kind = models.CharField(max_length=64)
    actor_kind = models.CharField(max_length=32, default="overmind")
    actor_name = models.CharField(max_length=100, default="Overmind")
    action = models.CharField(max_length=200)
    status = models.CharField(max_length=32, default="completed")
    assets = models.JSONField(default=list)
    task_kind = models.CharField(max_length=64, blank=True)
    task_id = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["project", "-updated_at"])]


class ActivityRead(models.Model):
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    seen_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["activity", "user"], name="activity_read_user_unique")
        ]


class ActivityClient(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    credential = models.CharField(max_length=100)
    actor_kind = models.CharField(max_length=32)
    actor_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "credential", "actor_kind"], name="activity_client_identity_unique"
            )
        ]
