"""No ``Trace`` table: a trace is the set of spans sharing a ``trace_id``, listed
by its head span (``Span.trace_heads``). ``service_name`` is denormalised
because nearly every list query filters on it.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q


class Conversation(models.Model):
    """UI/API session grouping for a traced conversation.
    ``capability`` is only the first seen, so capability filtering goes through spans."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="conversations"
    )
    # Wire value of the ``conversation.id`` span attribute.
    external_id = models.CharField(max_length=512, blank=True, default="")
    name = models.CharField(max_length=512, blank=True, default="")
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "external_id"],
                condition=~Q(external_id=""),
                name="conversation_proj_ext_uniq",
            ),
        ]

    def __str__(self):
        return self.name or self.external_id or str(self.id)


# Every attribute key the usage/cost/model rollups read. Ingest copies this
# slice into ``Span.usage`` so list queries never detoast ``attributes``
# (multi-MB snapshot blobs live there).
USAGE_ATTR_KEYS = (
    "genai.total_tokens",
    "genai.usage.total_tokens",
    "llm.usage.total_tokens",
    "gen_ai.usage.total_tokens",
    "genai.prompt_tokens",
    "genai.usage.prompt_tokens",
    "gen_ai.usage.input_tokens",
    "genai.completion_tokens",
    "genai.usage.completion_tokens",
    "gen_ai.usage.output_tokens",
    "genai.cache_read_tokens",
    "cost",
    "response_cost",
    "gen_ai.usage.cost",
    "genai.cost",
    "overmind.cost",
    "genai.model",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "genai.response.model",
    "llm.model",
    "model",
)


def usage_slice(attributes: dict | None) -> dict | None:
    """The compact usage/cost/model projection stored on ``Span.usage``."""
    if not attributes:
        return None
    out = {key: attributes[key] for key in USAGE_ATTR_KEYS if attributes.get(key) is not None}
    return out or None


# Retrievals ground a response in external state just like tool invocations.
TOOL_OPERATION_TYPES = frozenset({"tool_call", "tool", "retrieval"})


def is_tool_operation(span_type) -> bool:
    return str(span_type or "") in TOOL_OPERATION_TYPES


class Span(models.Model):
    """``span_id`` is the table-wide primary key: OTLP producers must emit span
    ids that are unique across every project.
    """

    class SpanType(models.TextChoices):
        LLM_CALL = "llm_call"
        TOOL_CALL = "tool_call"
        RETRIEVAL = "retrieval"
        WORKFLOW = "workflow"

    span_id = models.CharField(max_length=16, primary_key=True)
    trace_id = models.CharField(max_length=32, db_index=True)
    parent_span_id = models.CharField(max_length=16, null=True, blank=True, default=None)

    # Product tag, distinct from OTel ``kind``; set at ingest from
    # ``overclaw.span_type`` or heuristics on span name + attributes.
    span_type = models.CharField(
        max_length=40,
        default=SpanType.LLM_CALL,
        db_index=True,
    )
    operation = models.CharField(max_length=512, blank=True, default="")

    project = models.ForeignKey("overbae.Project", on_delete=models.CASCADE, related_name="spans")

    name = models.CharField(max_length=255, blank=True, default="")
    # OTel SpanKind: 0=UNSPECIFIED 1=INTERNAL 2=SERVER 3=CLIENT 4=PRODUCER 5=CONSUMER
    kind = models.SmallIntegerField(default=0)

    start_time_ns = models.BigIntegerField(default=0)
    end_time_ns = models.BigIntegerField(default=0)
    duration_ns = models.BigIntegerField(default=0)

    # OTel StatusCode: 0=UNSET 1=OK 2=ERROR
    status_code = models.SmallIntegerField(default=0)
    status_message = models.TextField(blank=True, default="")

    service_name = models.CharField(max_length=255, blank=True, default="")
    resource_attrs = models.JSONField(default=dict, blank=True)

    scope_name = models.CharField(max_length=255, blank=True, default="")
    scope_version = models.CharField(max_length=64, blank=True, default="")

    attributes = models.JSONField(default=dict, blank=True)
    # Derived projection of the usage/cost/model attribute keys; recomputed on
    # save, set explicitly on bulk ingest paths. Rollups read only this column.
    usage = models.JSONField(null=True, blank=True, default=None)
    events = models.JSONField(default=list, blank=True)
    links = models.JSONField(default=list, blank=True)

    # Resolved by ``process_span`` from span attributes, never set through the API.
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="spans",
    )
    conversation = models.ForeignKey(
        "overbae.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="spans",
    )

    # Keyed by signal name; live scores live under the "trace_scoring" block.
    feedback_score = models.JSONField(null=True, blank=True, default=None)

    received_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Keeps ``usage`` true for ORM saves; bulk_create paths set it explicitly.
        self.usage = usage_slice(self.attributes)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "attributes" in update_fields:
            kwargs["update_fields"] = list({*update_fields, "usage"})
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-start_time_ns"]
        indexes = [
            models.Index(fields=["project", "trace_id"], name="span_project_trace_idx"),
            models.Index(
                fields=["project", "service_name", "-start_time_ns"],
                name="span_proj_svc_time_idx",
            ),
            # Hot path: "list recent traces" — only root spans, ordered by start.
            models.Index(
                fields=["project", "-start_time_ns"],
                condition=Q(parent_span_id__isnull=True),
                name="span_root_recent_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name or '(unnamed)'} ({self.trace_id[:8]}…/{self.span_id})"

    @property
    def is_root(self) -> bool:
        return self.parent_span_id in (None, "")

    @classmethod
    def trace_heads(cls, queryset, project_ids):
        """Head = the root once it has arrived, else the earliest span: the root
        ends last in OTel, so gating on roots hides in-flight or killed runs.
        Membership is computed over ALL spans, so a filter cannot change which
        span represents a trace. Correlated subquery: SQLite lacks DISTINCT ON."""
        head = (
            cls.objects.filter(
                trace_id=models.OuterRef("trace_id"),
                project_id=models.OuterRef("project_id"),
            )
            .annotate(
                _rootness=models.Case(
                    models.When(parent_span_id__isnull=True, then=models.Value(0)),
                    default=models.Value(1),
                    output_field=models.IntegerField(),
                )
            )
            .order_by("_rootness", "start_time_ns", "span_id")
            .values("span_id")[:1]
        )
        return queryset.filter(project_id__in=project_ids, span_id=models.Subquery(head))


class BacktestRun(models.Model):
    class RunStatus(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    capability = models.ForeignKey(
        "overbae.Capability",
        on_delete=models.CASCADE,
        related_name="backtest_runs",
        null=True,
        blank=True,
    )
    prompt_id = models.CharField(max_length=512, blank=True, default="")
    models_config = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=RunStatus.choices, default=RunStatus.PENDING)
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"BacktestRun {self.id} ({self.status})"
