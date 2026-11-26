import uuid

from django.conf import settings
from django.db import models


class Feedback(models.Model):
    """One report from a person in the Console or from their coding agent over MCP."""

    class Source(models.TextChoices):
        CONSOLE = "console", "Console"
        AGENT = "agent", "Coding agent"

    class Kind(models.TextChoices):
        FAILURE = "failure", "Something failed"
        MISMATCH = "mismatch", "Behaviour differs from its description"
        CONFUSING = "confusing", "Unclear guidance"
        MISSING = "missing", "Missing capability"
        STUCK = "stuck", "Slow or stuck work"
        DEAD_END = "dead_end", "No progress after repeated attempts"
        IDEA = "idea", "Idea"
        PRAISE = "praise", "Worked well"
        QUESTION = "question", "Question"

    class Area(models.TextChoices):
        SETUP = "setup", "Setup and connection"
        OBSERVABILITY = "observability", "Observability"
        DATASETS = "datasets", "Datasets"
        EVALUATION = "evaluation", "Evaluation"
        TRAINING = "training", "Training"
        SERVING = "serving", "Serving"
        BILLING = "billing", "Billing"
        OTHER = "other", "Other"

    class Severity(models.TextChoices):
        BLOCKED = "blocked", "Blocked"
        DEGRADED = "degraded", "Degraded"
        MINOR = "minor", "Minor"

    class Outcome(models.TextChoices):
        ABANDONED = "abandoned", "Abandoned"
        WORKED_AROUND = "worked_around", "Worked around"
        RESOLVED = "resolved", "Resolved after retries"
        HANDED_TO_USER = "handed_to_user", "Handed to the user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.CONSOLE)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedback_submissions",
    )
    project = models.ForeignKey(
        "overbae.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedback",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    area = models.CharField(max_length=16, choices=Area.choices, default=Area.OTHER)
    severity = models.CharField(max_length=16, choices=Severity.choices, blank=True, default="")
    outcome = models.CharField(max_length=16, choices=Outcome.choices, blank=True, default="")
    title = models.CharField(max_length=160)
    message = models.TextField()
    # Agent reports: intent, expected, observed, suggestion, trail, resources, tool_calls.
    detail = models.JSONField(default=dict, blank=True)
    # {"reported": what the agent says about itself, "observed": what the server saw}.
    environment = models.JSONField(default=dict, blank=True)
    # Groups reports of one problem across users and sessions.
    fingerprint = models.CharField(max_length=64, blank=True, default="", db_index=True)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Feedback"
        verbose_name_plural = "Feedback"

    def __str__(self):
        return f"[{self.get_source_display()} · {self.get_kind_display()}] {self.title}"
