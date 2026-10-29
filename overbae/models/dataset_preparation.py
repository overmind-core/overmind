import uuid

from django.conf import settings
from django.db import models


class DatasetPreparation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE)
    dataset = models.ForeignKey(
        "overbae.Dataset", on_delete=models.CASCADE, related_name="preparations"
    )
    request_key = models.CharField(max_length=128)
    state = models.CharField(max_length=16, default="preparing")
    actor_kind = models.CharField(max_length=32)
    actor_name = models.CharField(max_length=100)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    outputs = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "request_key"], name="preparation_request_unique"
            ),
            models.UniqueConstraint(
                fields=["dataset"],
                condition=models.Q(state__in=["preparing", "paused"]),
                name="dataset_open_preparation_unique",
            ),
        ]
