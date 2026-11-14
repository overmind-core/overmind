import uuid

from django.conf import settings
from django.db import models


def default_toolsets():
    return ["data"]


class AssistantThread(models.Model):
    """One conversation with the platform assistant. The event log is the whole record: no
    process holds state between steps."""

    class State(models.TextChoices):
        # At rest: only a queued message starts it.
        IDLE = "idle"
        # A step is due and no worker holds it.
        READY = "ready"
        RUNNING = "running"
        # Holds for the jobs in ``waiting_on``.
        WAITING = "waiting"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE)
    # The record the conversation was opened on. Not a foreign key: any platform record.
    subject_kind = models.CharField(max_length=32, blank=True, default="")
    subject_id = models.UUIDField(null=True)
    state = models.CharField(max_length=16, choices=State, default=State.IDLE, db_index=True)
    # Counts claims. A step writes only while its claim is the newest, so a step that
    # outlived its lease cannot write under the step that replaced it.
    claim = models.PositiveIntegerField(default=0)
    stop_requested = models.BooleanField(default=False)
    # The tool call the thread waits for: {"call", "name", "user", "started", "approval",
    # "jobs": [{"kind", "id"}]}.
    waiting_on = models.JSONField(null=True)
    # Names of the tool groups the conversation opened, from ``assistant.tools.TOOLSETS``.
    toolsets = models.JSONField(default=default_toolsets)
    session = models.CharField(max_length=255, blank=True, default="")
    # Why the kernel the model last used is gone. Its next cell is told before anything runs.
    kernel_lost = models.CharField(max_length=255, blank=True, default="")
    # Cell ids whose frames the session holds, staged again when a session is replaced.
    staged = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subject_kind", "subject_id"],
                condition=models.Q(subject_id__isnull=False),
                name="assistant_thread_subject_unique",
            )
        ]


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
    payload = models.JSONField()
    # What a tool result shows the user. Apart from the payload: the loop never reads it,
    # and it can hold thousands of rows.
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
    """A message the user sent. It stays editable in the queue until the loop delivers it."""

    class State(models.TextChoices):
        QUEUED = "queued"
        DELIVERED = "delivered"
        REMOVED = "removed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        AssistantThread, on_delete=models.CASCADE, related_name="inputs", db_index=False
    )
    # Credits are per user: the sender pays for the work the message starts, and tools act
    # with the sender's project access.
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    request_key = models.CharField(max_length=128)
    text = models.TextField()
    # Dataset versions the message refers to, as cell ids.
    cells = models.JSONField(default=list)
    # The answer to a Question or Choices: {"call", "id", "values"}, where ``call`` is the
    # tool call that showed it.
    interaction = models.JSONField(null=True)
    state = models.CharField(max_length=16, choices=State, default=State.QUEUED)
    # Delivered at the next step boundary instead of after the current run ends; it also
    # ends a wait for jobs.
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
        indexes = [
            # The reconciler asks for threads with a queued message every few seconds.
            models.Index(
                fields=["thread"],
                condition=models.Q(state="queued"),
                name="assistant_input_queued",
            )
        ]
        ordering = ["position"]


class AssistantHandoff(models.Model):
    """Work the assistant gives to the customer's coding agent: it needs their repository or
    machine. The agent reads it over MCP and reports the result; the thread waits for it."""

    class State(models.TextChoices):
        OPEN = "open"
        DONE = "done"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(AssistantThread, on_delete=models.CASCADE, db_index=False)
    # The tool call that made it.
    call = models.CharField(max_length=32)
    instructions = models.TextField()
    state = models.CharField(max_length=16, choices=State, default=State.OPEN, db_index=True)
    result = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["thread", "call"], name="assistant_handoff_call_unique")
        ]
        ordering = ["created_at"]
