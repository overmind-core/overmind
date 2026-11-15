import uuid

from django.conf import settings
from django.db import models


class AssistantThread(models.Model):
    """The conversation about one dataset, shared by the project's members. The event log
    is the record: no process holds state between turns."""

    class State(models.TextChoices):
        IDLE = "idle"
        RUNNING = "running"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE)
    dataset = models.OneToOneField(
        "overbae.Dataset", on_delete=models.CASCADE, related_name="assistant_thread"
    )
    state = models.CharField(max_length=16, choices=State, default=State.IDLE)
    # Counts turns. A turn writes only while it is the newest, so a turn that was taken
    # for dead cannot write under the one that followed it.
    turn = models.PositiveIntegerField(default=0)
    stop_requested = models.BooleanField(default=False)
    # A running turn moves this at every step: a turn that stopped moving was interrupted.
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AssistantEvent(models.Model):
    class Kind(models.TextChoices):
        USER = "user"
        ASSISTANT = "assistant"
        TOOL_RESULT = "tool_result"
        STATUS = "status"
        # Replaces everything up to ``through`` in what the model reads; the log keeps it all.
        SUMMARY = "summary"

    # The unique constraint is the index on ``thread``.
    thread = models.ForeignKey(
        AssistantThread, on_delete=models.CASCADE, related_name="events", db_index=False
    )
    sequence = models.PositiveIntegerField()
    kind = models.CharField(max_length=16, choices=Kind)
    # What the model reads.
    payload = models.JSONField()
    # The result document a ``present`` call shows. The model never reads it, and it can
    # hold thousands of rows.
    presentation = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["thread", "sequence"], name="assistant_event_sequence_unique"
            )
        ]
        ordering = ["sequence"]


class AssistantInput(models.Model):
    """A message the user sent. It stays editable in the queue until a turn reads it."""

    class State(models.TextChoices):
        QUEUED = "queued"
        DELIVERED = "delivered"
        REMOVED = "removed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        AssistantThread, on_delete=models.CASCADE, related_name="inputs", db_index=False
    )
    # The sender pays for the model calls the message starts, and tools act with the
    # sender's project access.
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    request_key = models.CharField(max_length=128)
    text = models.TextField()
    # Dataset versions the message refers to, as cell ids.
    cells = models.JSONField(default=list)
    # The answer to a Question or Choices: {"call", "id", "values"}, where ``call`` is the
    # tool call that showed it.
    interaction = models.JSONField(null=True)
    state = models.CharField(max_length=16, choices=State, default=State.QUEUED)
    # Read at the running turn's next model call instead of after the turn.
    urgent = models.BooleanField(default=False)
    position = models.PositiveIntegerField()
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["thread", "request_key"], name="assistant_input_request_unique"
            )
        ]
        ordering = ["position"]
