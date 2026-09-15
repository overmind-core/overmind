from __future__ import annotations

from datetime import UTC, datetime

from django.db.models import Avg, Case, CharField, Count, F, Max, Q, Value, When
from django.db.models.functions import Cast, Concat
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_field,
    extend_schema_view,
)
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from overbae.api.filters import TaskExecutionFilter
from overbae.api.scoping import project_ids_for
from overbae.api.serializers import (
    eligible_scoring_capabilities,
    trace_usage_totals,
    traces_with_finished_scoring_pass,
)
from overbae.models import Behaviour, Capability, Evaluator, Span, TaskExecution
from overbae.services.behaviour import conversation
from overbae.services.eval.trace_scoring import FEEDBACK_KEY, SKIPPED_MEMBERS_KEY


class BehaviourSerializer(serializers.ModelSerializer):
    execution_count = serializers.IntegerField(read_only=True, default=0)
    avg_success_score = serializers.FloatField(read_only=True, allow_null=True, default=None)

    class Meta:
        model = Behaviour
        fields = [
            "id",
            "capability",
            "key",
            "display_name",
            "entry_anchor",
            "status",
            "execution_count",
            "avg_success_score",
        ]


class TaskExecutionListSerializer(serializers.ModelSerializer):
    behaviour_key = serializers.CharField(source="behaviour.key", read_only=True, default="")
    total_tokens = serializers.SerializerMethodField()
    total_cost = serializers.SerializerMethodField()
    model = serializers.SerializerMethodField()
    scoring_pending = serializers.SerializerMethodField()

    class Meta:
        model = TaskExecution
        fields = [
            "id",
            "capability",
            "behaviour",
            "behaviour_key",
            "trace_id",
            "unit_span_id",
            "conversation_id",
            "binding_source",
            "success_score",
            "session_score",
            "session_rationale",
            "route_flags",
            "terminal_kind",
            "status",
            "started_at",
            "duration_ms",
            "total_tokens",
            "total_cost",
            "model",
            "scoring_pending",
        ]

    def _trace_usage(self, obj) -> dict:
        return (self.context.get("trace_usage") or {}).get(obj.trace_id) or {}

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_total_tokens(self, obj) -> int | None:
        return self._trace_usage(obj).get("total_tokens")

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_total_cost(self, obj) -> float | None:
        return self._trace_usage(obj).get("total_cost")

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_model(self, obj) -> str | None:
        return self._trace_usage(obj).get("model")

    # No `eligible_scoring_capabilities` context reads False, never a per-row query.
    def get_scoring_pending(self, obj) -> bool:
        if obj.success_score is not None:
            return False
        if obj.status == TaskExecution.Status.ERROR:
            return False
        # A finished pass with no composite (every judge abstained) is done; pending
        # means "no finished pass", or the row spins forever.
        if obj.trace_id in (self.context.get("traces_with_finished_pass") or set()):
            return False
        eligible = self.context.get("eligible_scoring_capabilities")
        if eligible is None:
            return False
        return str(obj.capability_id) in eligible


class ExecutionConflictSerializer(serializers.Serializer):
    lane = serializers.CharField()
    spread = serializers.FloatField()
    members = serializers.DictField(child=serializers.FloatField())


class ExecutionFlowStepSerializer(serializers.Serializer):
    anchor = serializers.CharField()
    matched = serializers.BooleanField()
    # One step_results entry (untyped JSON, same shape as step_results rows).
    verdict = serializers.JSONField(allow_null=True)


class ExecutionFlowTerminalSerializer(serializers.Serializer):
    kind = serializers.CharField(allow_blank=True)
    verdict = serializers.JSONField(allow_null=True)


class ExecutionFlowSerializer(serializers.Serializer):
    steps = ExecutionFlowStepSerializer(many=True)
    terminal = ExecutionFlowTerminalSerializer()


class TaskExecutionSerializer(TaskExecutionListSerializer):
    execution_score = serializers.SerializerMethodField()
    conflict = serializers.SerializerMethodField()
    skipped_members = serializers.SerializerMethodField()
    flow = serializers.SerializerMethodField()

    class Meta(TaskExecutionListSerializer.Meta):
        fields = [
            *TaskExecutionListSerializer.Meta.fields,
            "observed_route",
            "step_results",
            "user_intent",
            "execution_score",
            "conflict",
            "skipped_members",
            "flow",
        ]

    @extend_schema_field(ExecutionFlowSerializer)
    def get_flow(self, obj) -> dict:
        return conversation.execution_flow(obj.observed_route, obj.step_results, obj.terminal_kind)

    # The verdict markers ride the UNIT span's feedback block, not the
    # execution row. Detail-only: one span read per retrieve; the list serializer
    # never pays it.
    def _unit_block(self, obj) -> dict:
        if not hasattr(obj, "_unit_block_cache"):
            feedback = (
                Span.objects.filter(
                    project_id=obj.project_id, trace_id=obj.trace_id, span_id=obj.unit_span_id
                )
                .values_list("feedback_score", flat=True)
                .first()
            )
            block = feedback.get(FEEDBACK_KEY) if isinstance(feedback, dict) else None
            obj._unit_block_cache = block if isinstance(block, dict) else {}
        return obj._unit_block_cache

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_execution_score(self, obj) -> float | None:
        execution = self._unit_block(obj).get("_execution")
        score = execution.get("score") if isinstance(execution, dict) else None
        return float(score) if isinstance(score, (int, float)) else None

    @extend_schema_field(ExecutionConflictSerializer(allow_null=True))
    def get_conflict(self, obj) -> dict | None:
        execution = self._unit_block(obj).get("_execution")
        conflict = execution.get("conflict") if isinstance(execution, dict) else None
        return conflict if isinstance(conflict, dict) else None

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_skipped_members(self, obj) -> list[str]:
        skipped = self._unit_block(obj).get(SKIPPED_MEMBERS_KEY)
        if not isinstance(skipped, list):
            return []
        return [name for name in skipped if isinstance(name, str)]


class ExecutionIntentSerializer(serializers.Serializer):
    text = serializers.CharField()
    source = serializers.CharField(allow_blank=True)
    current = serializers.CharField(required=False)


class ExecutionTaskStateSerializer(serializers.Serializer):
    status = serializers.CharField()
    outstanding_asks = serializers.ListField(child=serializers.CharField())
    asked = serializers.ListField(child=serializers.CharField())
    reason = serializers.CharField(allow_blank=True)
    delivered_wrong = serializers.BooleanField()


class ExecutionToolSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    title = serializers.CharField()
    failed = serializers.BooleanField()
    input = serializers.CharField(allow_blank=True)
    output = serializers.CharField(allow_blank=True)


class ConversationTurnSerializer(serializers.Serializer):
    id = serializers.CharField()
    trace_id = serializers.CharField()
    conversation_id = serializers.CharField(allow_blank=True)
    capability = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    success_score = serializers.FloatField(allow_null=True)
    session_score = serializers.FloatField(allow_null=True)
    session_rationale = serializers.CharField(allow_blank=True)
    intent = ExecutionIntentSerializer(allow_null=True)
    input_text = serializers.CharField(allow_blank=True)
    output_text = serializers.CharField(allow_blank=True)
    task_state = ExecutionTaskStateSerializer(allow_null=True)
    step_results = serializers.JSONField()
    tools = ExecutionToolSerializer(many=True)


class BehaviourEvaluatorSerializer(serializers.ModelSerializer):
    behaviour_binding = serializers.SerializerMethodField()

    class Meta:
        model = Evaluator
        fields = [
            "id",
            "name",
            "display_name",
            "description",
            "kind",
            "scope",
            "score_type",
            "behaviour_binding",
        ]

    def get_behaviour_binding(self, obj) -> dict:
        return (obj.config or {}).get("behaviour") or {}


class BehaviourStepCoverageSerializer(serializers.Serializer):
    segment = serializers.ListField(child=serializers.CharField())
    evaluators = serializers.ListField(child=serializers.CharField())
    covered = serializers.BooleanField()


class BehaviourCoverageEntrySerializer(serializers.Serializer):
    behaviour_id = serializers.CharField()
    outcome_evaluators = serializers.ListField(child=serializers.CharField())
    outcome_covered = serializers.BooleanField()
    steps = BehaviourStepCoverageSerializer(many=True)


class BehaviourCoverageResponseSerializer(serializers.Serializer):
    behaviours = BehaviourCoverageEntrySerializer(many=True)


class BehaviourToolSerializer(serializers.Serializer):
    name = serializers.CharField()
    declared_name = serializers.CharField(allow_blank=True)
    purpose = serializers.CharField(allow_blank=True)
    side_effect = serializers.CharField(allow_blank=True)


class BehaviourAuthoringStepSerializer(serializers.Serializer):
    step = serializers.CharField()
    kind = serializers.CharField(required=False, default="")
    anchors = serializers.ListField(child=serializers.CharField(), required=False, default=list)


class BehaviourAuthoringContractSerializer(serializers.Serializer):
    anchor_sequence = serializers.ListField(child=serializers.CharField())
    steps = BehaviourAuthoringStepSerializer(many=True)
    tool_set = BehaviourToolSerializer(many=True)
    terminal = serializers.JSONField()


class BehaviourAuthoringContextResponseSerializer(serializers.Serializer):
    behaviour_id = serializers.CharField()
    behaviour_key = serializers.CharField()
    display_name = serializers.CharField()
    contract = BehaviourAuthoringContractSerializer(allow_null=True)


class BehaviourViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = BehaviourSerializer
    queryset = Behaviour.objects.all()
    lookup_field = "id"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Behaviour.objects.none()
        return (
            Behaviour.objects.filter(
                project_id__in=project_ids_for(self.request.user, self.request.auth)
            )
            .annotate(
                execution_count=Count("executions", distinct=True),
                avg_success_score=Avg("executions__success_score"),
            )
            .order_by("created_at")
        )

    def _capability_from_query(self) -> Capability:
        return get_object_or_404(
            Capability.objects.filter(
                project_id__in=project_ids_for(self.request.user, self.request.auth)
            ),
            id=self.request.query_params.get("capability"),
        )

    @extend_schema(
        parameters=[OpenApiParameter("capability", OpenApiTypes.UUID, required=True)],
        responses=BehaviourCoverageResponseSerializer,
    )
    @action(detail=False, methods=["get"], url_path="coverage")
    def coverage(self, request):
        """Per-behaviour and per-step eval-coverage rollups for ?capability=<id>."""
        from overbae.services.behaviour.coverage import behaviour_coverage

        capability = self._capability_from_query()
        return Response({"behaviours": behaviour_coverage(capability)})

    # pagination_class=None keeps spectacular from wrapping the response in the
    # pagination envelope the action never emits.
    @extend_schema(responses=BehaviourEvaluatorSerializer(many=True))
    @action(detail=True, methods=["get"], url_path="evaluators", pagination_class=None)
    def evaluators(self, request, id=None):
        """The behaviour's compiled eval suite (step + outcome evaluators)."""
        behaviour = self.get_object()
        rows = [
            e
            for e in Evaluator.objects.filter(
                capability_id=behaviour.capability_id,
                is_archived=False,
                config__has_key="behaviour",
            )
            if ((e.config or {}).get("behaviour") or {}).get("behaviour_key") == behaviour.key
        ]
        return Response(BehaviourEvaluatorSerializer(rows, many=True).data)

    @extend_schema(responses=BehaviourAuthoringContextResponseSerializer)
    @action(detail=True, methods=["get"], url_path="authoring-context", pagination_class=None)
    def authoring_context(self, request, id=None):
        """Preloaded context for task-scoped eval authoring: the behaviour's
        latest contract, so the picker can list the steps a judge may target."""
        from overbae.services.behaviour.contract import available_steps

        behaviour = self.get_object()
        version = behaviour.versions.order_by("-created_at").first()
        contract = version.contract if version else {}

        contract_payload = (
            {
                "anchor_sequence": contract.get("anchor_sequence") or [],
                "steps": available_steps(contract),
                "tool_set": contract.get("tool_set") or [],
                "terminal": contract.get("terminal") or {},
            }
            if contract
            else None
        )

        return Response(
            BehaviourAuthoringContextResponseSerializer(
                {
                    "behaviour_id": behaviour.id,
                    "behaviour_key": behaviour.key,
                    "display_name": behaviour.display_name,
                    "contract": contract_payload,
                }
            ).data
        )


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("project", OpenApiTypes.UUID, description="Scope to one project."),
            OpenApiParameter(
                "capability", OpenApiTypes.UUID, description="Scope to one capability."
            ),
            OpenApiParameter("behaviour", OpenApiTypes.UUID, description="Scope to one behaviour."),
            OpenApiParameter(
                "binding_source",
                str,
                enum=[c.value for c in TaskExecution.BindingSource],
                description="Scope to one binding source.",
            ),
            OpenApiParameter("trace_id", str, description="Scope to one trace."),
            OpenApiParameter("status", str, description="Scope to one lifecycle status."),
            OpenApiParameter(
                "has_error",
                OpenApiTypes.BOOL,
                description="True = status is error; false = exclude errors.",
            ),
            OpenApiParameter("model", str, description="Trace invoked this model."),
            OpenApiParameter(
                "has_model",
                OpenApiTypes.BOOL,
                description="True = some span on the trace reported a model.",
            ),
            OpenApiParameter("min_duration_ms", OpenApiTypes.NUMBER),
            OpenApiParameter("max_duration_ms", OpenApiTypes.NUMBER),
            OpenApiParameter("total_tokens__gte", OpenApiTypes.NUMBER),
            OpenApiParameter("total_tokens__lte", OpenApiTypes.NUMBER),
            OpenApiParameter("total_cost__gte", OpenApiTypes.NUMBER),
            OpenApiParameter("total_cost__lte", OpenApiTypes.NUMBER),
            OpenApiParameter("service_name", str),
            OpenApiParameter("operation", str),
            OpenApiParameter("span_type", str),
            OpenApiParameter("status_code", OpenApiTypes.NUMBER),
            OpenApiParameter("received_at__gte", OpenApiTypes.DATETIME),
            OpenApiParameter("received_at__lte", OpenApiTypes.DATETIME),
            OpenApiParameter("started_at__gte", OpenApiTypes.DATETIME),
            OpenApiParameter("started_at__lte", OpenApiTypes.DATETIME),
        ]
    )
)
class TaskExecutionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TaskExecution.objects.all()
    lookup_field = "id"
    filterset_class = TaskExecutionFilter
    search_fields = [
        "trace_id",
        "conversation_id",
        "unit_span_id",
        "behaviour__key",
        "terminal_kind",
        "capability__name",
    ]
    ordering_fields = [
        "behaviour__key",
        "binding_source",
        "conversation_id",
        "duration_ms",
        "started_at",
        "status",
        "success_score",
        "terminal_kind",
        "trace_id",
    ]

    def get_serializer_class(self):
        if self.action == "list":
            return TaskExecutionListSerializer
        return TaskExecutionSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter("conversation_id", str, required=True),
            OpenApiParameter("project", OpenApiTypes.UUID, description="Scope to one project."),
        ],
        responses=ConversationTurnSerializer(many=True),
    )
    @action(detail=False, methods=["get"], url_path="conversation-turns", pagination_class=None)
    def conversation_turns(self, request):
        """Every turn of one conversation with its ask, delivery, task state and
        tool activities — assembled server-side from the executions and their
        traces' spans."""
        conversation_id = (request.query_params.get("conversation_id") or "").strip()
        if not conversation_id:
            raise serializers.ValidationError({"conversation_id": "required"})
        queryset = self.get_queryset().filter(conversation_id=conversation_id)
        project = request.query_params.get("project")
        if project:
            queryset = queryset.filter(project_id=project)
        rows = list(queryset.order_by("started_at"))
        project_ids = project_ids_for(request.user, request.auth)
        turns = conversation.conversation_turns(rows, project_ids)
        return Response(ConversationTurnSerializer(turns, many=True).data)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TaskExecution.objects.none()
        return TaskExecution.objects.filter(
            project_id__in=project_ids_for(self.request.user, self.request.auth)
        ).select_related("behaviour", "capability")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        trace_usage = getattr(self, "_trace_usage", None)
        if trace_usage is not None:
            context["trace_usage"] = trace_usage
        eligible = getattr(self, "_eligible_scoring_capabilities", None)
        if eligible is not None:
            context["eligible_scoring_capabilities"] = eligible
        finished_passes = getattr(self, "_traces_with_finished_pass", None)
        if finished_passes is not None:
            context["traces_with_finished_pass"] = finished_passes
        return context

    def _conversation_grouped_page(self, queryset) -> tuple[list[TaskExecution], bool]:
        """One page = whole conversations, so a thread never splits across page
        boundaries. Groups: shared conversation_id; conversation-less rows of a
        multi-capability (handoff) trace group by trace; everything else is a
        singleton. Groups order by latest started_at, members chronological
        inside; count is the number of groups."""
        # order_by() before values(): default ordering would leak into GROUP BY.
        handoff_traces = set(
            queryset.order_by()
            .values("trace_id")
            .annotate(
                caps=Count("capability", distinct=True),
                unbound=Count("id", filter=Q(capability__isnull=True)),
            )
            .filter(Q(caps__gte=2) | Q(caps__gte=1, unbound__gte=1))
            .values_list("trace_id", flat=True)
        )
        keyed = queryset.annotate(
            group_key=Case(
                When(~Q(conversation_id=""), then=Concat(Value("conv:"), "conversation_id")),
                When(trace_id__in=handoff_traces, then=Concat(Value("trace:"), "trace_id")),
                default=Cast("id", output_field=CharField()),
                output_field=CharField(),
            )
        )
        groups = (
            keyed.order_by()
            .values("group_key")
            .annotate(anchor=Max("started_at"))
            .order_by(F("anchor").desc(nulls_last=True))
        )
        page = self.paginate_queryset(groups)
        group_page = page if page is not None else list(groups)
        position = {group["group_key"]: i for i, group in enumerate(group_page)}
        rows = list(keyed.filter(group_key__in=list(position)))
        epoch = datetime.min.replace(tzinfo=UTC)
        rows.sort(key=lambda row: (position[row.group_key], row.started_at or epoch))
        return rows, page is not None

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "group",
                str,
                description="`conversation` paginates whole conversations: shared "
                "conversation_id (or handoff trace) rows stay on one page, ordered by "
                "latest activity with turns chronological inside. `ordering` is ignored.",
            )
        ]
    )
    def list(self, request, *args, **kwargs):
        # Trace-level totals for the page in one query, as the /traces list does.
        queryset = self.filter_queryset(self.get_queryset())
        if request.query_params.get("group") == "conversation":
            rows, paginated = self._conversation_grouped_page(queryset)
        else:
            page = self.paginate_queryset(queryset)
            rows = page if page is not None else list(queryset)
            paginated = page is not None
        project_ids = project_ids_for(request.user, request.auth)
        self._trace_usage = trace_usage_totals(project_ids, [row.trace_id for row in rows])
        self._eligible_scoring_capabilities = eligible_scoring_capabilities(
            project_ids, [row.capability_id for row in rows]
        )
        self._traces_with_finished_pass = traces_with_finished_scoring_pass(
            project_ids, [row.trace_id for row in rows]
        )
        serializer = self.get_serializer(rows, many=True)
        if paginated:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)
