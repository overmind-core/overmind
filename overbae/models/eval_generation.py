from django.db import models


class EvalGenerationScheduler(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_tick_at = models.DateTimeField(null=True)


class EvalGenerationRun(models.Model):
    run = models.OneToOneField("overbae.EvalRun", on_delete=models.CASCADE, primary_key=True)
    last_admitted_at = models.DateTimeField(null=True)
    scoring_task_id = models.CharField(max_length=255, blank=True)
    scoring_started_at = models.DateTimeField(null=True)


class EvalGenerationWork(models.Model):
    sample = models.OneToOneField("overbae.EvalSample", on_delete=models.CASCADE, primary_key=True)
    state = models.CharField(max_length=16, default="waiting", db_index=True)
    task_id = models.CharField(max_length=255, blank=True)
    queued_at = models.DateTimeField(null=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["state", "expires_at"], name="eval_gen_state_expiry")]
