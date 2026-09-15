"""Minted from the codebase scan and versioned ONLY on real code change —
never re-derived from instrumented code.
"""

from __future__ import annotations

import uuid

from django.db import models


class Behaviour(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        RETIRED = "retired"

    class Grain(models.TextChoices):
        RUN = "run"
        TURN = "turn"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="behaviours"
    )
    capability = models.ForeignKey(
        "overbae.Capability", on_delete=models.CASCADE, related_name="behaviours"
    )
    # Stable across rescans; re-anchoring carries it forward through renames.
    key = models.SlugField(max_length=255)
    display_name = models.CharField(max_length=512, blank=True, default="")
    # module.qualname of the entry code symbol; the binder's primary join key.
    entry_anchor = models.CharField(max_length=512, blank=True, default="", db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    # Written at scan time (anchoring.refresh_grains), never inferred at bind time.
    grain = models.CharField(max_length=8, choices=Grain.choices, default=Grain.TURN)
    first_seen_sha = models.CharField(max_length=64, blank=True, default="")
    last_seen_sha = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        unique_together = [("capability", "key")]
        indexes = [models.Index(fields=["project", "status"])]

    def __str__(self) -> str:
        return f"{self.display_name or self.key} ({self.status})"


class BehaviourVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    behaviour = models.ForeignKey(Behaviour, on_delete=models.CASCADE, related_name="versions")
    analyzed_sha = models.CharField(max_length=64, db_index=True)
    # {entry_anchor, anchors: [{qualname, kind, file}], anchor_sequence, sequence, tools,
    #  tool_set, routing, terminal: {kind, description}, divergences, provenance, indistinguishable_with}
    contract = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("behaviour", "analyzed_sha")]

    def __str__(self) -> str:
        return f"{self.behaviour_id} @ {self.analyzed_sha[:8]}"


class TaskExecution(models.Model):
    class BindingSource(models.TextChoices):
        ANCHOR_JOIN = "anchor_join"
        DECLARED = "declared"
        UNBOUND = "unbound"

    class Status(models.TextChoices):
        COMPLETED = "completed"
        ERROR = "error"
        # Rootless trace: the run was killed or is in flight past the settle window.
        INTERRUPTED = "interrupted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="task_executions"
    )
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_executions",
    )
    # Null = unbound execution (still materialized, feeds the deviation surface).
    behaviour = models.ForeignKey(
        Behaviour,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="executions",
    )
    behaviour_version = models.ForeignKey(
        BehaviourVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="executions",
    )
    trace_id = models.CharField(max_length=32, db_index=True)
    unit_span_id = models.CharField(max_length=16)
    conversation_id = models.CharField(max_length=512, blank=True, default="")
    binding_source = models.CharField(
        max_length=20, choices=BindingSource.choices, default=BindingSource.UNBOUND
    )
    # {sha, entry_qualname, anchors (observed order), matched_anchors (interior
    #  anchors that drove the binding), unknown_anchors, terminal,
    #  cluster_occupancy, alignment: {completion, order_ok, terminal_match}} —
    #  descriptive metadata, NEVER an input to success_score.
    observed_route = models.JSONField(default=dict, blank=True)
    # {text, source: "declared" | "first_message", running?, conversation_id?} —
    # `text` is this turn's ask; `running` is conversation-scoped when it differs.
    user_intent = models.JSONField(default=dict, blank=True)
    # [{evaluator, role: step|outcome, segment, score, passed, outcome, rationale}]
    step_results = models.JSONField(default=list, blank=True)
    success_score = models.FloatField(null=True, blank=True)
    # Conversation-level completion, not a turn eval. Same value on every
    # turn sharing conversation_id; refreshed when any turn is scored.
    session_score = models.FloatField(null=True, blank=True)
    session_rationale = models.TextField(blank=True, default="")
    # e.g. ["unanalyzed_sha", "ambiguous_anchor_overlap", "off_contract_route",
    #       "unusual_route_good_outcome", "usual_route_bad_outcome"]
    route_flags = models.JSONField(default=list, blank=True)
    terminal_kind = models.CharField(max_length=32, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED)
    started_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        unique_together = [("project", "unit_span_id")]
        indexes = [
            models.Index(fields=["project", "-started_at"]),
            models.Index(fields=["behaviour", "-started_at"]),
            models.Index(fields=["project", "binding_source"]),
        ]

    def __str__(self) -> str:
        return f"TaskExecution {self.unit_span_id} ({self.binding_source})"


class ConversationEvent(models.Model):
    """Append-only; the LLM classifier is the only writer and recorded
    transitions are never re-run, so rescores are deterministic."""

    class EventType(models.TextChoices):
        ASK_OPENED = "ask_opened"
        ASK_SUPERSEDED = "ask_superseded"
        ASK_REPROMPTED = "ask_reprompted"
        DELIVERED = "delivered"
        DELIVERED_WRONG = "delivered_wrong"
        REFUSED = "refused"
        PARKED = "parked"

    class Source(models.TextChoices):
        CLASSIFIER = "classifier"
        BACKFILL = "backfill"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="conversation_events"
    )
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversation_events",
    )
    conversation_id = models.CharField(max_length=512, db_index=True)
    # The turn that produced this transition. SET_NULL so a deleted execution
    # never silently rewrites recorded conversation history.
    execution = models.ForeignKey(
        TaskExecution,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_events",
    )
    trace_id = models.CharField(max_length=32, blank=True, default="")
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    # Stable per ask ("a1", "a2", …) so transitions chain across turns.
    ask_id = models.CharField(max_length=64, blank=True, default="")
    ask_text = models.TextField(blank=True, default="")
    ask_kind = models.CharField(max_length=16, blank=True, default="")  # produce|inspect|instruct
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.CLASSIFIER)
    rationale = models.TextField(blank=True, default="")
    # Monotonic per conversation; within-turn transitions stay in emit order.
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "created_at"]
        indexes = [models.Index(fields=["project", "conversation_id", "order"])]

    def __str__(self) -> str:
        return f"{self.event_type} {self.ask_id} ({self.conversation_id[:8]})"
