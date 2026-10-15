import uuid

from django.db import models
from django.utils import timezone


class DecisionJob(models.Model):
    """Decision-facing identity for a run owned by the shared training lifecycle."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    finetuning_job = models.OneToOneField(
        "overbae.FinetuningJob",
        on_delete=models.CASCADE,
        related_name="decision_job",
    )
    created_at = models.DateTimeField(default=timezone.now)

    @property
    def project_id(self):
        return self.finetuning_job.project_id

    @property
    def model_id(self):
        return self.finetuning_job.base_model

    @property
    def status(self):
        return self.finetuning_job.status
