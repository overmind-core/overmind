import uuid

from django.db.models.expressions import RawSQL
from django_filters import rest_framework as filters
from rest_framework.exceptions import ValidationError

from overbae.api.span_ordering import (
    genai_evidence_predicate,
    llm_cost_sql,
    llm_model_sql,
    llm_total_tokens_sql,
    usage_sql,
)
from overbae.models import (
    Capability,
    Conversation,
    DeployedModel,
    EvalRun,
    EvalSample,
    Evaluator,
    FinetuningJob,
    Project,
    Score,
    Span,
    TaskExecution,
    Verdict,
)


class ProjectFilter(filters.FilterSet):
    name = filters.CharFilter(lookup_expr="icontains")

    class Meta:
        model = Project
        fields = ["name", "slug"]


class CapabilityFilter(filters.FilterSet):
    name = filters.CharFilter(lookup_expr="icontains")

    class Meta:
        model = Capability
        fields = ["project", "name", "slug", "model", "status"]


class FinetuningJobFilter(filters.FilterSet):
    created_after = filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = FinetuningJob
        # ``group_id`` + ``?ordering=-created_at`` is covered by the
        # ("group_id", "-created_at") index — keep the pair together.
        fields = [
            "project",
            "capability",
            "dataset",
            "status",
            "base_model",
            "provider",
            "group_id",
        ]


class LLMActivityFilterMixin:
    """Shared model/usage filters for the traces toolbar, joined through span
    ``attributes``. Subclasses provide ``_filter_usage`` — the two
    implementations deliberately diverge and must not be merged: SpanFilter
    applies the correlated usage SQL directly to its own rows, while
    TaskExecutionFilter must evaluate it on the Span table first (an ``__in``
    subquery would alias the span table and break the correlated SQL's
    ``overbae_span.trace_id`` reference)."""

    def _scoped_project_ids(self, queryset):
        """Projects from the form, else the OUTER queryset — guards against
        unbounded scans (``services.trace_selection`` builds SpanFilter from a
        payload with no ``project``). ``project`` cleans to a model instance on
        SpanFilter and a bare UUID on TaskExecutionFilter."""
        project = self.form.cleaned_data.get("project")
        if project:
            return [getattr(project, "pk", project)]
        return list(queryset.order_by().values_list("project_id", flat=True).distinct())

    def _trace_ids_with_llm_activity(self, queryset, model=None):
        """Trace ids of spans that ran an LLM call — a specific model, or any evidence.

        The SQL references ``attributes`` unqualified on purpose: compiled as an
        ``IN`` subquery over the same table, SQL's innermost-scope rule keeps it
        uncorrelated even when Django renames the subquery alias.

        Deliberately NOT narrowed by the request's time window: a root span is
        exported when it *ends*, so its model-bearing children routinely land in
        an earlier OTLP batch (earlier ``received_at``) and would drop out.
        """
        spans = Span.objects.filter(project_id__in=self._scoped_project_ids(queryset))
        if model is None:
            spans = spans.extra(where=[genai_evidence_predicate()])
        else:
            spans = spans.annotate(_model=RawSQL(llm_model_sql(), [])).filter(_model=model)
        return spans.values_list("trace_id", flat=True).distinct()

    def filter_model(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(trace_id__in=self._trace_ids_with_llm_activity(queryset, value))

    def filter_has_model(self, queryset, name, value):
        trace_ids = self._trace_ids_with_llm_activity(queryset)
        return (
            queryset.filter(trace_id__in=trace_ids)
            if value
            else queryset.exclude(trace_id__in=trace_ids)
        )

    def filter_total_tokens_gte(self, queryset, name, value):
        return self._filter_usage(queryset, llm_total_tokens_sql, ">=", value)

    def filter_total_tokens_lte(self, queryset, name, value):
        return self._filter_usage(queryset, llm_total_tokens_sql, "<=", value)

    def filter_total_cost_gte(self, queryset, name, value):
        return self._filter_usage(queryset, llm_cost_sql, ">=", value)

    def filter_total_cost_lte(self, queryset, name, value):
        return self._filter_usage(queryset, llm_cost_sql, "<=", value)


class SpanFilter(LLMActivityFilterMixin, filters.FilterSet):
    name = filters.CharFilter(lookup_expr="icontains")
    operation = filters.CharFilter(lookup_expr="icontains")
    service_name = filters.CharFilter(lookup_expr="icontains")
    service_name__in = filters.BaseInFilter(field_name="service_name", lookup_expr="in")

    duration_ns__gte = filters.NumberFilter(field_name="duration_ns", lookup_expr="gte")
    duration_ns__lte = filters.NumberFilter(field_name="duration_ns", lookup_expr="lte")
    min_duration_ms = filters.NumberFilter(method="filter_min_duration_ms")
    max_duration_ms = filters.NumberFilter(method="filter_max_duration_ms")

    start_time_ns__gte = filters.NumberFilter(field_name="start_time_ns", lookup_expr="gte")
    start_time_ns__lte = filters.NumberFilter(field_name="start_time_ns", lookup_expr="lte")
    received_at__gte = filters.IsoDateTimeFilter(field_name="received_at", lookup_expr="gte")
    received_at__lte = filters.IsoDateTimeFilter(field_name="received_at", lookup_expr="lte")

    has_error = filters.BooleanFilter(method="filter_has_error")

    total_tokens__gte = filters.NumberFilter(method="filter_total_tokens_gte")
    total_tokens__lte = filters.NumberFilter(method="filter_total_tokens_lte")
    total_cost__gte = filters.NumberFilter(method="filter_total_cost_gte")
    total_cost__lte = filters.NumberFilter(method="filter_total_cost_lte")
    all_spans = filters.BooleanFilter(method="filter_all_spans")
    project_id = filters.NumberFilter(field_name="project_id", lookup_expr="exact")
    session = filters.UUIDFilter(field_name="conversation_id")
    # The model lives in child-span ``attributes``, so match trace ids first.
    model = filters.CharFilter(method="filter_model")
    # Any GenAI evidence, not just a model — and narrower than
    # ``span_type=llm_call``, which ingest stamps on unlabelled spans by default.
    # The param name is frozen: it is in the OpenAPI schema and in saved filters.
    has_model = filters.BooleanFilter(method="filter_has_model")
    # Spans ingest could not attribute to a current capability — the graph floor.
    unbound = filters.BooleanFilter(field_name="capability", lookup_expr="isnull")

    class Meta:
        model = Span
        fields = [
            "project",
            "capability",
            "conversation",
            "trace_id",
            "span_id",
            "kind",
            "span_type",
            "status_code",
            "service_name",
            "name",
            "operation",
        ]

    def filter_all_spans(self, queryset, name, value):
        if value:
            return queryset
        # One row per trace by head span — must match `SpanViewSet.get_queryset`.
        return Span.trace_heads(queryset, self._scoped_project_ids(queryset))

    def filter_min_duration_ms(self, queryset, name, value):
        return queryset.filter(duration_ns__gte=int(value) * 1_000_000)

    def filter_max_duration_ms(self, queryset, name, value):
        return queryset.filter(duration_ns__lte=int(value) * 1_000_000)

    def filter_has_error(self, queryset, name, value):
        # OTel StatusCode.ERROR == 2
        return queryset.filter(status_code=2) if value else queryset.exclude(status_code=2)

    def _filter_usage(self, queryset, per_span_sql, operator, value):
        # Match whatever the row displays: its own usage in the span list, the
        # trace-wide sum in the root-span list.
        all_spans = bool(self.form.cleaned_data.get("all_spans"))
        expr = usage_sql(per_span_sql, all_spans=all_spans)
        return queryset.extra(where=[f"({expr}) {operator} %s"], params=[float(value)])


class TaskExecutionFilter(LLMActivityFilterMixin, filters.FilterSet):
    """Same query-param names as ``SpanFilter`` so the Console traces toolbar
    can reuse its URL on the executions list. Span-level fields join through
    the unit span or the execution's trace."""

    project = filters.UUIDFilter(field_name="project_id")
    capability = filters.UUIDFilter(field_name="capability_id")
    behaviour = filters.UUIDFilter(field_name="behaviour_id")
    binding_source = filters.CharFilter()
    trace_id = filters.CharFilter()
    status = filters.CharFilter()

    started_at__gte = filters.IsoDateTimeFilter(field_name="started_at", lookup_expr="gte")
    started_at__lte = filters.IsoDateTimeFilter(field_name="started_at", lookup_expr="lte")
    received_at__gte = filters.IsoDateTimeFilter(field_name="started_at", lookup_expr="gte")
    received_at__lte = filters.IsoDateTimeFilter(field_name="started_at", lookup_expr="lte")

    min_duration_ms = filters.NumberFilter(field_name="duration_ms", lookup_expr="gte")
    max_duration_ms = filters.NumberFilter(field_name="duration_ms", lookup_expr="lte")
    has_error = filters.BooleanFilter(method="filter_has_error")

    service_name = filters.CharFilter(method="filter_service_name")
    operation = filters.CharFilter(method="filter_operation")
    span_type = filters.CharFilter(method="filter_span_type")
    status_code = filters.NumberFilter(method="filter_status_code")

    model = filters.CharFilter(method="filter_model")
    has_model = filters.BooleanFilter(method="filter_has_model")
    total_tokens__gte = filters.NumberFilter(method="filter_total_tokens_gte")
    total_tokens__lte = filters.NumberFilter(method="filter_total_tokens_lte")
    total_cost__gte = filters.NumberFilter(method="filter_total_cost_gte")
    total_cost__lte = filters.NumberFilter(method="filter_total_cost_lte")

    class Meta:
        model = TaskExecution
        fields = ["project", "capability", "behaviour", "binding_source", "trace_id", "status"]

    def _filter_unit_span(self, queryset, **lookups):
        from django.db.models import Exists, OuterRef  # noqa: PLC0415

        return queryset.filter(
            Exists(
                Span.objects.filter(
                    project_id=OuterRef("project_id"),
                    span_id=OuterRef("unit_span_id"),
                    **lookups,
                )
            )
        )

    def filter_has_error(self, queryset, name, value):
        return (
            queryset.filter(status=TaskExecution.Status.ERROR)
            if value
            else queryset.exclude(status=TaskExecution.Status.ERROR)
        )

    def filter_service_name(self, queryset, name, value):
        if not value:
            return queryset
        return self._filter_unit_span(queryset, service_name__icontains=value)

    def filter_operation(self, queryset, name, value):
        if not value:
            return queryset
        return self._filter_unit_span(queryset, operation__icontains=value)

    def filter_span_type(self, queryset, name, value):
        if not value:
            return queryset
        return self._filter_unit_span(queryset, span_type=value)

    def filter_status_code(self, queryset, name, value):
        if value is None:
            return queryset
        # OTel codes from the shared traces toolbar: 2 = error, 1 = OK.
        if int(value) == 2:
            return queryset.filter(status=TaskExecution.Status.ERROR)
        if int(value) == 1:
            return queryset.exclude(status=TaskExecution.Status.ERROR)
        return self._filter_unit_span(queryset, status_code=value)

    def _filter_usage(self, queryset, per_span_sql, operator, value):
        # Evaluate here, on the Span table: an `__in` subquery would alias the
        # span table and the correlated usage SQL still names
        # `overbae_span.trace_id`. Do not merge with SpanFilter's version.
        expr = usage_sql(per_span_sql, all_spans=False)
        matching = list(
            Span.objects.filter(project_id__in=self._scoped_project_ids(queryset))
            .extra(where=[f"({expr}) {operator} %s"], params=[float(value)])
            .values_list("trace_id", flat=True)
            .distinct()
        )
        return queryset.filter(trace_id__in=matching)


class SessionFilter(filters.FilterSet):
    """``capability`` matches sessions with at least one span attributed to that capability
    — sessions are project-wide and ``Conversation.capability`` is only a convenience
    pointer. EXISTS keeps it from disturbing the queryset's count/min/max
    annotations.
    """

    capability = filters.UUIDFilter(method="filter_capability")
    created_after = filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = Conversation
        fields = ["project"]

    def filter_capability(self, queryset, name, value):
        from django.db.models import Exists, OuterRef  # noqa: PLC0415

        return queryset.filter(
            Exists(Span.objects.filter(conversation_id=OuterRef("pk"), capability_id=value))
        )


class EvaluatorFilter(filters.FilterSet):
    name = filters.CharFilter(lookup_expr="icontains")
    # A method filter, not an exact match: global managed templates
    # (project IS NULL) must stay visible inside a project-scoped list.
    project = filters.UUIDFilter(method="filter_project")
    include_managed = filters.BooleanFilter(method="filter_include_managed")

    class Meta:
        model = Evaluator
        fields = ["capability", "kind", "scope", "score_type", "is_managed", "is_archived"]

    def filter_project(self, queryset, name, value):
        from django.db.models import Q  # noqa: PLC0415

        include_managed = str(self.data.get("include_managed", "true")).lower() != "false"
        q = Q(project_id=value)
        if include_managed:
            q |= Q(is_managed=True, project__isnull=True)
        return queryset.filter(q)

    def filter_include_managed(self, queryset, name, value):
        # Applied inside filter_project; a no-op here so the param still reaches
        # the schema.
        return queryset


class EvalRunFilter(filters.FilterSet):
    created_after = filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")
    # An EvalRun has no capability FK; the list serializer annotates it from the dataset.
    capability = filters.UUIDFilter(field_name="dataset__capability")

    class Meta:
        model = EvalRun
        fields = ["project", "status", "data_source", "dataset"]


class EvalSampleFilter(filters.FilterSet):
    class Meta:
        model = EvalSample
        fields = ["run", "variant", "row_index", "source_trace_id"]


class ScoreFilter(filters.FilterSet):
    name = filters.CharFilter(lookup_expr="icontains")
    value_min = filters.NumberFilter(field_name="value", lookup_expr="gte")
    value_max = filters.NumberFilter(field_name="value", lookup_expr="lte")

    class Meta:
        model = Score
        fields = [
            "run",
            "variant",
            "sample",
            "evaluator",
            "scope",
            "data_type",
            "source",
            "failure_role",
        ]


class VerdictFilter(filters.FilterSet):
    target_id__in = filters.BaseInFilter(field_name="target_id")
    # All verdicts of one trace in a single call: span-target verdicts key on
    # span_id, so the trace resolves through its spans.
    trace_id = filters.CharFilter(method="filter_trace_id")

    class Meta:
        model = Verdict
        fields = ["target_id", "target_kind", "evaluator_name", "outcome"]

    def filter_trace_id(self, queryset, name, value):
        return queryset.filter(
            target_kind=Verdict.TargetKind.SPAN,
            target_id__in=Span.objects.filter(trace_id=value).values("span_id"),
        )


# Same sentinel the datasets list uses for its unassigned rows.
NO_CAPABILITY = "__none__"


class DeployedModelFilter(filters.FilterSet):
    """``capability`` is reached through the owning fine-tuning job — a deployment has
    no capability FK — and also accepts ``NO_CAPABILITY``."""

    capability = filters.CharFilter(method="filter_capability")

    class Meta:
        model = DeployedModel
        fields = ["project", "status"]

    def filter_capability(self, queryset, name, value):
        if value == NO_CAPABILITY:
            return queryset.filter(finetuning_job__capability__isnull=True)
        try:
            capability_id = uuid.UUID(value)
        except ValueError as exc:
            # CharFilter does no UUID validation; without this a malformed id
            # reaches the driver and 500s instead of 400ing.
            raise ValidationError(
                {"capability": f'Enter a valid UUID or "{NO_CAPABILITY}".'},
            ) from exc
        return queryset.filter(finetuning_job__capability=capability_id)
