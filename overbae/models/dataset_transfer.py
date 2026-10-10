import uuid

from django.db import models


class DatasetTransfer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE)
    user = models.ForeignKey("overbae.User", on_delete=models.SET_NULL, null=True)
    request_key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    specification = models.JSONField(default=dict)
    upload_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    received = models.PositiveBigIntegerField(default=0)
    state = models.CharField(max_length=20, default="uploading")
    result = models.JSONField(default=dict)
    task = models.JSONField(default=dict)
    dispatched_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "request_key"], name="dataset_transfer_key_unique"
            )
        ]
