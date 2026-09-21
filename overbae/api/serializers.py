import logging
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Manager, Max, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from overbae.api.scoping import project_ids_for
from overbae.models import (
    APIToken,
    BillingTelemetry,
    Capability,
    ConnectorCredential,
    ConnectorSyncRun,
    Conversation,
    Dataset,
    DeployedModel,
    EvalSet,
    EvalSetMember,
    Feedback,
    FinetuningJob,
    FinetuningJobEvent,
    Project,
    ProjectInvite,
    ProjectMembership,
    Prompt,
    ScoringPass,
    Span,
    User,
    UserOnboarding,
)
from overbae.services.billing_ledger import MAX_TOPUP_USD, MIN_TOPUP_USD
from overbae.services.codebase.flow import (
    build_capability_flow,
    capability_input_keys,
    capability_tool_names,
)
from overbae.services.datasets import use as dataset_use
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.deployment import deployment_progress
from overbae.services.eval.trace_scoring import STATUS_ERROR

logger = logging.getLogger(__name__)


def _user_project_ids(user):
    return set(ProjectMembership.objects.filter(user=user).values_list("project_id", flat=True))


def _first_float(attrs: dict, *keys: str) -> float | None:
    for key in keys:
        raw = attrs.get(key)
        if raw in (None, ""):
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _total_tokens_from_span_attributes(attrs: dict | None) -> int | None:
    """Key precedence mirrors the frontend ``transformSpan`` and ``overmind_attrs.py``
    so a list row and the trace-detail view agree; ``gen_ai.*`` / ``llm.usage.*``
    are connector fallbacks."""
    if not attrs:
        return None

    reported = _first_float(
        attrs,
        "genai.total_tokens",
        "genai.usage.total_tokens",
        "llm.usage.total_tokens",
        "gen_ai.usage.total_tokens",
    )
    if reported is not None:
        return int(round(reported))

    prompt = _first_float(
        attrs, "genai.prompt_tokens", "genai.usage.prompt_tokens", "gen_ai.usage.input_tokens"
    )
    completion = _first_float(
        attrs,
        "genai.completion_tokens",
        "genai.usage.completion_tokens",
        "gen_ai.usage.output_tokens",
    )
    total = (prompt or 0.0) + (completion or 0.0)
    return int(round(total)) if total > 0 else None


def _model_from_span_attributes(attrs: dict | None) -> str | None:
    """Precedence mirrors ``eval.chatml.MODEL_KEYS``; ``None``, never a fabricated
    value, when absent."""
    if not attrs:
        return None
    for key in (
        "genai.model",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "genai.response.model",
        "llm.model",
        "model",
    ):
        raw = attrs.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _cache_read_tokens_from_span_attributes(attrs: dict | None) -> int | None:
    """Never folded into ``total_tokens`` (docs/tracing-attributes.md §3);
    ``None``, never a fabricated ``0``, when unreported."""
    if not attrs:
        return None
    value = _first_float(attrs, "genai.cache_read_tokens")
    return int(round(value)) if value is not None else None


def _total_cost_from_span_attributes(attrs: dict | None) -> float | None:
    """Keys mirror ``TraceFilter._numeric_expr_llm_cost`` so a row's value matches
    what cost filters threshold against."""
    if not attrs:
        return None
    for key in ("cost", "response_cost", "gen_ai.usage.cost", "genai.cost", "overmind.cost"):
        raw = attrs.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _spans_with_usage(*, project_ids, **field_in):
    return Span.objects.filter(project_id__in=project_ids, usage__isnull=False, **field_in)


def _usage_values(queryset, group_field: str):
    return queryset.order_by("start_time_ns").values_list(
        group_field,
        "span_id",
        "parent_span_id",
        "trace_id",
        "scope_name",
        "start_time_ns",
        "end_time_ns",
        "usage",
    )


def _leaf_usage_rows(rows) -> list[tuple]:
    """Double instrumentation (a framework instrumentor beside a provider one)
    reports the same call twice: as parent and child, or — because each
    instrumentor keeps its own context chain — as two spans anywhere in the
    tree, from different scopes, with the same token total and overlapping
    windows. Count each call once, preferring the row that carries cost and
    model. Rows without a scope name never twin-merge: their identity is
    unknowable."""
    entries = list(rows)
    child_parents = {(e[0], e[2]) for e in entries if e[2]}
    kept = [e for e in entries if (e[0], e[1]) not in child_parents]

    twins: dict[tuple, list[tuple[int, tuple]]] = {}
    solo: list[tuple[int, tuple]] = []
    for idx, entry in enumerate(kept):
        group, _sid, _parent, trace, scope, _start, _end, attrs = entry
        tokens = _total_tokens_from_span_attributes(attrs)
        if tokens is not None and scope:
            twins.setdefault((group, trace, tokens), []).append((idx, entry))
        else:
            solo.append((idx, entry))

    def _richness(entry) -> tuple:
        attrs = entry[7]
        start, end = entry[5] or 0, entry[6] or 0
        return (
            _total_cost_from_span_attributes(attrs) is None,
            _model_from_span_attributes(attrs) is None,
            end - start,
        )

    surviving = list(solo)
    for bucket in twins.values():
        keep: list[tuple[int, tuple]] = []
        for idx, entry in sorted(bucket, key=lambda pair: _richness(pair[1])):
            scope, start, end = entry[4], entry[5] or 0, entry[6] or 0
            if any(k[4] != scope and start < (k[6] or 0) and (k[5] or 0) < end for _i, k in keep):
                continue
            keep.append((idx, entry))
        surviving.extend(keep)

    surviving.sort(key=lambda pair: pair[0])
    return [(entry[0], entry[7]) for _idx, entry in surviving]


def trace_usage_totals(project_ids, trace_ids: list[str]) -> dict[str, dict]:
    """Summed across ALL spans in one query: usage lives on child LLM spans, not
    the root. ``None`` (never a fabricated ``0``) when no span reported a metric;
    ``cache_read_tokens`` is never folded into ``total_tokens``."""
    totals: dict[str, dict] = {}
    if not trace_ids:
        return totals

    # Ordered so the picked ``model`` is the earliest span's, not DB row order.
    rows = _leaf_usage_rows(
        _usage_values(
            _spans_with_usage(project_ids=project_ids, trace_id__in=trace_ids),
            "trace_id",
        )
    )
    for trace_id, attrs in rows:
        bucket = totals.setdefault(
            trace_id,
            {"total_tokens": None, "total_cost": None, "cache_read_tokens": None, "model": None},
        )
        tokens = _total_tokens_from_span_attributes(attrs)
        if tokens is not None:
            bucket["total_tokens"] = (bucket["total_tokens"] or 0) + tokens
        cost = _total_cost_from_span_attributes(attrs)
        if cost is not None:
            bucket["total_cost"] = round((bucket["total_cost"] or 0.0) + cost, 6)
        cache_read = _cache_read_tokens_from_span_attributes(attrs)
        if cache_read is not None:
            bucket["cache_read_tokens"] = (bucket["cache_read_tokens"] or 0) + cache_read
        if bucket["model"] is None:
            bucket["model"] = _model_from_span_attributes(attrs)
    return totals


def eligible_scoring_capabilities(project_ids, capability_ids: list) -> set[str]:
    """The set ``score_trace`` would attempt; backs ``scoring_pending`` so the
    list doesn't reimplement its skip rules."""
    if not capability_ids:
        return set()
    rows = (
        Capability.objects.filter(
            project_id__in=project_ids,
            id__in=capability_ids,
            active_eval_set__members__role=EvalSetMember.Role.TRACE_SCORING,
            active_eval_set__members__enabled=True,
            active_eval_set__members__evaluator__is_archived=False,
        )
        .values_list("id", flat=True)
        .distinct()
    )
    return {str(capability_id) for capability_id in rows}


def traces_with_finished_scoring_pass(project_ids, trace_ids: list) -> set[str]:
    """Pending means "no finished pass", never "no score yet": a pass whose judges
    all abstained is done, and a null-score row must not spin forever."""
    if not trace_ids:
        return set()
    return set(
        ScoringPass.objects.filter(
            project_id__in=project_ids, trace_id__in=trace_ids, finished__isnull=False
        ).values_list("trace_id", flat=True)
    )


def conversation_usage_totals(project_ids, conversation_ids: list) -> dict:
    """Same rules as :func:`trace_usage_totals`, keyed by ``conversation_id`` —
    one query per page."""
    totals: dict = {}
    if not conversation_ids:
        return totals

    rows = _leaf_usage_rows(
        _usage_values(
            _spans_with_usage(project_ids=project_ids, conversation_id__in=conversation_ids),
            "conversation_id",
        )
    )
    for conversation_id, attrs in rows:
        bucket = totals.setdefault(
            conversation_id,
            {"total_tokens": None, "total_cost": None, "model": None},
        )
        tokens = _total_tokens_from_span_attributes(attrs)
        if tokens is not None:
            bucket["total_tokens"] = (bucket["total_tokens"] or 0) + tokens
        cost = _total_cost_from_span_attributes(attrs)
        if cost is not None:
            bucket["total_cost"] = round((bucket["total_cost"] or 0.0) + cost, 6)
        if bucket["model"] is None:
            bucket["model"] = _model_from_span_attributes(attrs)
    return totals


def _unique_project_slug(slug: str, *, exclude_pk=None) -> str:
    """Auto-generated slugs collide with projects the user cannot see; projects
    route by UUID, so resolve the collision rather than 400."""
    base = slug or "project"
    candidate = base
    n = 2
    queryset = Project.objects.all()
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    while queryset.filter(slug=candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


class ProjectSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()
    member_emails = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]
        # ``_unique_project_slug`` resolves collisions; the auto
        # ``UniqueTogetherValidator`` would hard-400 them first.
        validators = []

    def get_member_count(self, obj) -> int:
        # List/retrieve annotate this; create/update responses fall back to a query.
        annotated = getattr(obj, "member_count", None)
        return annotated if annotated is not None else obj.memberships.count()

    def get_member_emails(self, obj) -> list[str]:
        return [m.user.email for m in obj.memberships.all()]

    def create(self, validated_data):
        validated_data["slug"] = _unique_project_slug(validated_data.get("slug", ""))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        new_type = validated_data.get("integration_type")
        if new_type is not None and new_type != instance.integration_type:
            raise serializers.ValidationError(
                {"integration_type": "Integration type cannot be changed after project creation."}
            )
        if "slug" in validated_data:
            validated_data["slug"] = _unique_project_slug(
                validated_data["slug"], exclude_pk=instance.pk
            )
        return super().update(instance, validated_data)


class ProjectMemberSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = ProjectMembership
        fields = ["id", "project", "user", "user_email", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at", "user_email"]


class ProjectInviteSerializer(serializers.ModelSerializer):
    invited_by_email = serializers.SerializerMethodField()

    class Meta:
        model = ProjectInvite
        fields = ["id", "project", "email", "invited_by_email", "created_at"]
        read_only_fields = fields

    def get_invited_by_email(self, obj) -> str | None:
        return obj.invited_by.email if obj.invited_by else None


class ProjectInviteCreateSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)


class ProjectMembershipCreateSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

    def get_user(self) -> User:
        email = self.validated_data.get("email", "").strip().lower()
        # first(), not get(): case-duplicate accounts predating the iexact
        # provisioning match would make get() raise MultipleObjectsReturned.
        user = User.objects.filter(email__iexact=email).order_by("date_joined").first()
        if user is None:
            raise serializers.ValidationError(
                {
                    "detail": "No user found with that email address.",
                    "code": "user_not_found",
                }
            )
        return user


class UserOnboardingSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserOnboarding
        fields = "__all__"
        read_only_fields = ["id", "user", "created_at"]


def _validate_project_ownership(serializer_instance, value):
    """Shared validation: ensure project belongs to the requesting user."""
    user = serializer_instance.context["request"].user
    if value.id not in _user_project_ids(user):
        raise serializers.ValidationError("You are not a member of this project.")
    return value


def _capability_product(capability):
    """The newest active cell among the datasets bound to the capability."""
    from overbae.models import Dataset  # noqa: PLC0415

    for dataset in Dataset.objects.filter(capability=capability).order_by("-updated_at")[:5]:
        cell = dataset.active_cell
        if cell is not None:
            return cell
    return None


def _capability_live_dataset_size(capability) -> int:
    product = _capability_product(capability)
    return int(product.rows) if product is not None else 0


def _capability_has_expected_output(capability) -> bool:
    product = _capability_product(capability)
    if product is None:
        return False
    return bool(((product.intent_report or {}).get("eval") or {}).get("has_reference"))


class CapabilityListSerializer(serializers.ModelSerializer):
    dataset_size = serializers.SerializerMethodField()
    dataset_has_expected_output = serializers.SerializerMethodField()
    modality = serializers.SerializerMethodField()
    tool_names = serializers.SerializerMethodField()
    dataset_input_keys = serializers.SerializerMethodField()
    trace_count = serializers.SerializerMethodField()
    suggested_name = serializers.SerializerMethodField()

    class Meta:
        model = Capability
        fields = [
            "id",
            "project",
            "name",
            "slug",
            "description",
            "source_path",
            "model",
            "active_model",
            "benchmark_model",
            "structure_weight",
            "total_points",
            "tool_usage_weight",
            "status",
            "observed",
            "suggested_name",
            "entrypoint_fn",
            "analyzer_model",
            "dataset_size",
            "dataset_has_expected_output",
            "dataset_input_keys",
            "tool_names",
            "cli_version",
            "modality",
            "last_activity_at",
            "trace_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_trace_count(self, obj) -> int | None:
        """Distinct-trace count annotated by CapabilityViewSet; null elsewhere."""
        return getattr(obj, "trace_count", None)

    def get_suggested_name(self, obj) -> str:
        meta = obj.improvement_metadata if isinstance(obj.improvement_metadata, dict) else {}
        return str(meta.get("suggested_name") or "")

    def get_dataset_size(self, obj) -> int:
        return _capability_live_dataset_size(obj)

    def get_dataset_has_expected_output(self, obj) -> bool:
        return _capability_has_expected_output(obj)

    def get_tool_names(self, obj) -> list[str]:
        """Card ``tool_spec`` wins over capture ``tool_config``."""
        return capability_tool_names(obj)

    def get_dataset_input_keys(self, obj) -> list[str]:
        """Card / ``input_schema`` wins over capture ``dataset_input_keys``."""
        return capability_input_keys(obj)

    def get_modality(self, obj) -> str:
        """Capability-card modality (text/code/tabular/multimodal…), "" when unanalyzed."""
        meta = obj.improvement_metadata if isinstance(obj.improvement_metadata, dict) else {}
        card = meta.get("capability_card")
        card = card if isinstance(card, dict) else {}
        return str(card.get("modality") or "").strip()


class PromptSerializer(serializers.ModelSerializer):
    """Each row carries the full ``system_prompt`` so the picker previews it
    without a second round-trip."""

    class Meta:
        model = Prompt
        fields = [
            "id",
            "capability",
            "version",
            "label",
            "system_prompt",
            "model",
            "created_at",
        ]
        read_only_fields = fields


class CapabilityFlowToolArgumentSerializer(serializers.Serializer):
    """One structured argument of a tool (the granular form of the flat ``args`` string)."""

    name = serializers.CharField(allow_blank=True)
    type = serializers.CharField(allow_blank=True, required=False, default="")
    required = serializers.BooleanField(required=False, default=False)
    description = serializers.CharField(allow_blank=True, required=False, default="")


class CapabilityFlowToolSerializer(serializers.Serializer):
    name = serializers.CharField()
    purpose = serializers.CharField(allow_blank=True)
    args = serializers.CharField(allow_blank=True)
    # Additive descriptive fields — optional so older bundles still serialize.
    side_effect = serializers.CharField(allow_blank=True, required=False, default="none")
    returns = serializers.CharField(allow_blank=True, required=False, default="")
    arguments = CapabilityFlowToolArgumentSerializer(many=True, required=False, default=list)
    integration = serializers.CharField(allow_blank=True, required=False, default="")
    cluster = serializers.CharField(allow_blank=True, required=False, default="")
    provenance = serializers.ListField(child=serializers.CharField(), required=False, default=list)


class CapabilityFlowModeSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True)
    entrypoint_fn = serializers.CharField(allow_blank=True)
    source_path = serializers.CharField(allow_blank=True)
    prompt_builder = serializers.CharField(allow_blank=True, required=False)
    # Additive descriptive fields — optional so older bundles still serialize.
    purpose = serializers.CharField(allow_blank=True, required=False, default="")
    routing = serializers.CharField(allow_blank=True, required=False, default="")
    model = serializers.CharField(allow_blank=True, required=False, default="")
    output = serializers.CharField(allow_blank=True, required=False, default="")
    prompt = serializers.CharField(allow_blank=True, required=False, default="")
    prompt_excerpt = serializers.CharField(allow_blank=True, required=False, default="")


class CapabilityFlowUtilitySerializer(serializers.Serializer):
    name = serializers.CharField()
    purpose = serializers.CharField(allow_blank=True)
    called_by = serializers.CharField(allow_blank=True)
    source_path = serializers.CharField(allow_blank=True)
    entrypoint_fn = serializers.CharField(allow_blank=True)
    model = serializers.CharField(allow_blank=True)
    # Additive descriptive fields — optional so older bundles still serialize.
    io_contract = serializers.CharField(allow_blank=True, required=False, default="")
    cardinality = serializers.CharField(allow_blank=True, required=False, default="unknown")
    prompt_excerpt = serializers.CharField(allow_blank=True, required=False, default="")
    structured_output = serializers.BooleanField(required=False, default=False)


class CapabilityFlowExpectedOutputSerializer(serializers.Serializer):
    description = serializers.CharField(allow_blank=True)
    example = serializers.JSONField(required=False, allow_null=True)
    quality_signals = serializers.ListField(child=serializers.CharField(), required=False)


class CapabilityFlowTrajectoryTerminalSerializer(serializers.Serializer):
    kind = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)


class CapabilityFlowTrajectoryToolUseSerializer(serializers.Serializer):
    tool = serializers.CharField()
    when = serializers.CharField(allow_blank=True)


class CapabilityFlowTrajectoryStepSerializer(serializers.Serializer):
    step = serializers.CharField()
    kind = serializers.CharField(allow_blank=True, required=False, default="agent_step")
    anchors = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    # In/Action/Out contract; blank on agent steps.
    input = serializers.CharField(allow_blank=True, required=False, default="")
    action = serializers.CharField(allow_blank=True, required=False, default="")
    output = serializers.CharField(allow_blank=True, required=False, default="")
    may_use = CapabilityFlowTrajectoryToolUseSerializer(many=True, required=False, default=list)


class CapabilityFlowTrajectoryPathSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    # code_path | declared_task | decision_surface — strongest claim the code supports.
    claim = serializers.CharField(allow_blank=True, required=False, default="code_path")
    prompt_quote = serializers.CharField(allow_blank=True, required=False, default="")
    verified = serializers.BooleanField(required=False, default=False)
    routing = serializers.CharField(allow_blank=True)
    steps = CapabilityFlowTrajectoryStepSerializer(many=True, required=False, default=list)
    tools = serializers.ListField(child=serializers.CharField())
    terminal = CapabilityFlowTrajectoryTerminalSerializer()
    divergences = serializers.ListField(child=serializers.CharField())
    provenance = serializers.ListField(child=serializers.CharField())


class CapabilityFlowSerializer(serializers.Serializer):
    """Typed execution-graph contract for one capability (see ``build_capability_flow``)."""

    has_card = serializers.BooleanField()
    is_fallback = serializers.BooleanField()
    source = serializers.CharField()
    task = serializers.CharField(allow_blank=True)
    modality = serializers.CharField(allow_blank=True)
    domain = serializers.CharField(allow_blank=True)
    model = serializers.CharField(allow_blank=True)
    system_prompt = serializers.CharField(allow_blank=True)
    system_prompt_excerpt = serializers.CharField(allow_blank=True)
    takes_files = serializers.BooleanField()
    input_schema = serializers.DictField()
    output_fields = serializers.DictField()
    expected_output = CapabilityFlowExpectedOutputSerializer()
    tool_spec = CapabilityFlowToolSerializer(many=True)
    modes = CapabilityFlowModeSerializer(many=True)
    llm_utilities = CapabilityFlowUtilitySerializer(many=True)
    vocabulary = serializers.DictField()
    success_criteria = serializers.ListField(child=serializers.CharField())
    failure_modes = serializers.ListField(child=serializers.CharField())
    trajectory_map = CapabilityFlowTrajectoryPathSerializer(many=True)
    provenance_paths = serializers.ListField(child=serializers.CharField())


class CapabilitySerializer(serializers.ModelSerializer):
    tool_config = serializers.JSONField(required=False, allow_null=True)
    consistency_rules = serializers.JSONField(required=False, allow_null=True)
    optimizable_elements = serializers.JSONField(required=False, allow_null=True)
    fixed_elements = serializers.JSONField(required=False, allow_null=True)
    dataset_size = serializers.SerializerMethodField()
    flow = serializers.SerializerMethodField()

    class Meta:
        model = Capability
        fields = "__all__"
        # ``slug`` stays immutable: OTLP ingest, optimizer runs and external
        # references resolve by it.
        read_only_fields = [
            "id",
            "slug",
            "created_at",
            "updated_at",
        ]

    def get_dataset_size(self, obj) -> int:
        return _capability_live_dataset_size(obj)

    @extend_schema_field(CapabilityFlowSerializer)
    def get_flow(self, obj):
        return build_capability_flow(obj)

    def validate_project(self, value):
        return _validate_project_ownership(self, value)

    def validate_active_model(self, value):
        """Project + READY, not capability: base deployments have no capability
        and sharing one fine-tune across sibling capabilities is intentional.
        Refusing non-READY keeps the alias off a dead deployment."""
        if value is None:
            return value
        project_id = self.instance.project_id if self.instance else self.initial_data.get("project")
        if project_id and str(value.project_id) != str(project_id):
            raise serializers.ValidationError(
                "The deployed model must belong to the same project as the capability."
            )
        if value.status != DeployedModel.Status.READY:
            raise serializers.ValidationError(
                "Only a ready deployment can be set as the capability's active model."
            )
        return value

    def validate_benchmark_model(self, value):
        if value is None:
            return value
        project_id = self.instance.project_id if self.instance else self.initial_data.get("project")
        if str(value.project_id) != str(project_id):
            raise serializers.ValidationError("The benchmark model must belong to this project.")
        if not value.finetuning_job_id:
            raise serializers.ValidationError(
                "Select a trained model, not evaluation infrastructure."
            )
        if value.status != DeployedModel.Status.READY:
            raise serializers.ValidationError("Only a ready trained model can be the benchmark.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # Back-filling defaults on a partial update would clobber JSONB columns
        # (a PATCH of ``policy_markdown`` alone would wipe ``tool_config``).
        if self.partial:
            return attrs
        defaults = {
            "tool_config": {},
            "consistency_rules": [],
            "optimizable_elements": [],
            "fixed_elements": [],
        }
        for key, default in defaults.items():
            if attrs.get(key) is None:
                attrs[key] = default.copy() if isinstance(default, (dict, list)) else default
        return attrs


class SpanSerializer(serializers.ModelSerializer):
    """Full span — used both for ``/api/traces/{trace_id}/`` (one span row in
    the per-trace list) and as the per-row representation for any span query."""

    is_root = serializers.BooleanField(read_only=True)

    class Meta:
        model = Span
        fields = [
            "span_id",
            "trace_id",
            "parent_span_id",
            "is_root",
            "project",
            "span_type",
            "operation",
            "name",
            "kind",
            "start_time_ns",
            "end_time_ns",
            "duration_ns",
            "status_code",
            "status_message",
            "service_name",
            "resource_attrs",
            "scope_name",
            "scope_version",
            "attributes",
            "events",
            "links",
            "capability",
            "conversation",
            "feedback_score",
            "received_at",
        ]
        read_only_fields = fields


TRACE_STATUS_CHOICES = ("completed", "live", "interrupted")


def trace_status(*, has_root: bool, last_received_at) -> str:
    """The root ends last, so its presence means the run finished; without it the
    trace is live until quiet past the settle window, then interrupted."""
    if has_root:
        return "completed"
    if last_received_at is None:
        return "live"
    settle = timedelta(seconds=settings.TRACE_SETTLE_SECONDS)
    return "live" if timezone.now() - last_received_at < settle else "interrupted"


def trace_status_map(project_ids, trace_ids: list[str]) -> dict[str, str]:
    """One aggregate query for the whole page."""
    if not trace_ids:
        return {}
    rows = (
        Span.objects.filter(project_id__in=project_ids, trace_id__in=trace_ids)
        .values("trace_id")
        .annotate(
            root_count=Count("span_id", filter=Q(parent_span_id__isnull=True)),
            last_received_at=Max("received_at"),
        )
    )
    return {
        row["trace_id"]: trace_status(
            has_root=row["root_count"] > 0, last_received_at=row["last_received_at"]
        )
        for row in rows
    }


class TraceStatusListSerializer(serializers.ListSerializer):
    """Batches the page's trace-status query — no view-provided context to forget."""

    def to_representation(self, data):
        spans = list(data.all() if isinstance(data, Manager) else data)
        self.child._trace_status_map = trace_status_map(
            {span.project_id for span in spans}, list({span.trace_id for span in spans})
        )
        return [self.child.to_representation(span) for span in spans]


class RootSpanListSerializer(serializers.ModelSerializer):
    """``GET /api/traces/`` row — one span per trace, its head: the root when it
    has arrived, else the earliest span."""

    total_tokens = serializers.SerializerMethodField()
    total_cost = serializers.SerializerMethodField()
    cache_read_tokens = serializers.SerializerMethodField()
    model = serializers.SerializerMethodField()
    # Human-readable capability name for the list chip; `capability` (UUID) still links.
    capability_name = serializers.CharField(
        source="capability.name", allow_null=True, read_only=True
    )
    source = serializers.SerializerMethodField()
    scoring_pending = serializers.SerializerMethodField()
    trace_status = serializers.SerializerMethodField()

    class Meta:
        model = Span
        fields = [
            "trace_id",
            "span_id",
            "project",
            "capability",
            "capability_name",
            "span_type",
            "operation",
            "name",
            "service_name",
            "kind",
            "status_code",
            "status_message",
            "start_time_ns",
            "end_time_ns",
            "duration_ns",
            "total_tokens",
            "total_cost",
            "cache_read_tokens",
            "model",
            "conversation",
            "feedback_score",
            "received_at",
            "source",
            "scoring_pending",
            "trace_status",
        ]
        read_only_fields = fields
        list_serializer_class = TraceStatusListSerializer

    # Usage lives on child LLM spans, not the root: ``SpanViewSet.list`` injects
    # per-trace totals as ``trace_usage`` context; without it, the root's own.
    def _trace_totals(self, obj: Span) -> dict | None:
        trace_usage = self.context.get("trace_usage")
        if trace_usage is None:
            return None
        return trace_usage.get(obj.trace_id) or {}

    def get_total_tokens(self, obj: Span) -> int | None:
        totals = self._trace_totals(obj)
        if totals is not None:
            return totals.get("total_tokens")
        return _total_tokens_from_span_attributes(obj.attributes)

    def get_total_cost(self, obj: Span) -> float | None:
        totals = self._trace_totals(obj)
        if totals is not None:
            return totals.get("total_cost")
        return _total_cost_from_span_attributes(obj.attributes)

    def get_cache_read_tokens(self, obj: Span) -> int | None:
        totals = self._trace_totals(obj)
        if totals is not None:
            return totals.get("cache_read_tokens")
        return _cache_read_tokens_from_span_attributes(obj.attributes)

    def get_model(self, obj: Span) -> str | None:
        totals = self._trace_totals(obj)
        if totals is not None:
            return totals.get("model")
        return _model_from_span_attributes(obj.attributes)

    # Absence means native; connector polling is the only writer of this key.
    def get_source(self, obj: Span) -> str:
        return (obj.resource_attrs or {}).get("connector.source") or "overmind"

    # Derived from the trace's spans, never the single row; indexing (not
    # ``.get``) fails loudly if the list serializer didn't batch.
    @extend_schema_field(serializers.ChoiceField(choices=list(TRACE_STATUS_CHOICES)))
    def get_trace_status(self, obj: Span) -> str:
        statuses = getattr(self, "_trace_status_map", None)
        if statuses is None:
            statuses = trace_status_map([obj.project_id], [obj.trace_id])
        return statuses[obj.trace_id]

    # Requires `eligible_scoring_capabilities` context (see `SpanViewSet.list`); a
    # trace whose root has no capability but a scoreable descendant does errs
    # toward the dash rather than a per-row eligibility query.
    def get_scoring_pending(self, obj: Span) -> bool:
        feedback = obj.feedback_score or {}
        if "trace_scoring" in feedback:
            return False
        if obj.status_code == STATUS_ERROR:
            return False
        if obj.capability_id is None:
            return False
        # A finished pass is done even when it wrote no block (all members
        # skipped or abstained) — without this a null result spins forever.
        if obj.trace_id in (self.context.get("traces_with_finished_pass") or set()):
            return False
        eligible = self.context.get("eligible_scoring_capabilities")
        if eligible is None:
            return False
        return str(obj.capability_id) in eligible


class SessionSerializer(serializers.ModelSerializer):
    """``GET /api/sessions/`` row. Counts come from ``SessionViewSet`` annotations;
    token/cost totals from the per-page ``session_usage`` context map."""

    capability_name = serializers.CharField(
        source="capability.name", allow_null=True, read_only=True
    )
    trace_count = serializers.IntegerField(read_only=True)
    span_count = serializers.IntegerField(read_only=True)
    first_span_ns = serializers.IntegerField(read_only=True, allow_null=True)
    last_span_ns = serializers.IntegerField(read_only=True, allow_null=True)
    session_score = serializers.FloatField(read_only=True, allow_null=True)
    total_tokens = serializers.SerializerMethodField()
    total_cost = serializers.SerializerMethodField()
    model = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "external_id",
            "name",
            "project",
            "capability",
            "capability_name",
            "trace_count",
            "span_count",
            "first_span_ns",
            "last_span_ns",
            "session_score",
            "total_tokens",
            "total_cost",
            "model",
            "created_at",
        ]
        read_only_fields = fields

    def _totals(self, obj: Conversation) -> dict:
        usage = self.context.get("session_usage")
        if usage is None:
            return {}
        return usage.get(obj.id) or {}

    def get_total_tokens(self, obj: Conversation) -> int | None:
        return self._totals(obj).get("total_tokens")

    def get_total_cost(self, obj: Conversation) -> float | None:
        return self._totals(obj).get("total_cost")

    def get_model(self, obj: Conversation) -> str | None:
        return self._totals(obj).get("model")


class TraceUsageSerializer(serializers.Serializer):
    """One :func:`trace_usage_totals` bucket — the only usage rollup clients read."""

    total_tokens = serializers.IntegerField(allow_null=True)
    total_cost = serializers.FloatField(allow_null=True)
    cache_read_tokens = serializers.IntegerField(allow_null=True)
    model = serializers.CharField(allow_null=True)


class TraceDetailSerializer(serializers.Serializer):
    """``GET /api/traces/{trace_id}/`` payload — root summary + every span."""

    trace_id = serializers.CharField()
    root = SpanSerializer(allow_null=True)
    span_count = serializers.IntegerField()
    trace_status = serializers.ChoiceField(choices=list(TRACE_STATUS_CHOICES))
    usage = TraceUsageSerializer()
    spans = SpanSerializer(many=True)


class FeedbackCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feedback
        fields = ["id", "category", "feedback", "created_at"]
        read_only_fields = ["id", "created_at"]


class FinetuningJobEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = FinetuningJobEvent
        fields = ["id", "job", "event_type", "message", "data", "created_at"]
        read_only_fields = fields


def _deployed_model_id(job: FinetuningJob) -> str | None:
    """DeployedModel UUID for ``/inference/$modelId``, if registration created one."""
    try:
        return str(job.deployed_model.id)
    except DeployedModel.DoesNotExist:
        return None


class ModelSwapPromptRequestSerializer(serializers.Serializer):
    pin = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "Write this one deployment's concrete model id into the code instead of the "
            "capability's permanent alias."
        ),
    )


class ModelSwapPromptSerializer(serializers.Serializer):
    prompt = serializers.CharField()
    pin = serializers.BooleanField()
    capability_id = serializers.UUIDField()
    capability_name = serializers.CharField()
    old_model = serializers.CharField()
    new_model = serializers.CharField()


def _describe_cell(job: FinetuningJob) -> dict | None:
    from overbae.services.datasets import use  # noqa: PLC0415 — avoid import cycle

    return use.describe(job.cell if job.cell_id else None)


class FinetuningJobListSerializer(serializers.ModelSerializer):
    deployed_model_id = serializers.SerializerMethodField()
    cell_info = serializers.SerializerMethodField()

    class Meta:
        model = FinetuningJob
        fields = [
            "id",
            "project",
            "capability",
            "dataset",
            "eval_dataset",
            "eval_cell",
            "eval_set",
            "eval_incumbent_before",
            "eval_incumbent_after",
            "eval_model_before",
            "eval_model_after",
            "validation_enabled",
            "validation_split_ratio",
            "validation_dataset",
            "cell",
            "cell_info",
            "validation_cell",
            "split_method",
            "name",
            "use_case",
            "base_model",
            "status",
            "group_id",
            "model_tier",
            "provider",
            "output_model_name",
            "deployed_model_id",
            "model_weights_location",
            "progress",
            "retry_count",
            "max_retries",
            "error_message",
            "cost_usd",
            "billed_minutes",
            "cost_synced_at",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
        ]
        read_only_fields = fields

    def get_cell_info(self, obj: FinetuningJob) -> dict | None:
        return _describe_cell(obj)

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_deployed_model_id(self, obj: FinetuningJob) -> str | None:
        return _deployed_model_id(obj)


class FinetuningJobRunSerializer(serializers.Serializer):
    """Every job the wizard launched together. ``run_id`` is the ``group_id`` when
    the jobs carry one, else the lone job's id; ``group_id`` stays null for a
    single-job run so ``?group_id=`` never matches it."""

    run_id = serializers.UUIDField()
    group_id = serializers.UUIDField(allow_null=True)
    jobs = FinetuningJobListSerializer(many=True)


class FinetuningJobSerializer(serializers.ModelSerializer):
    events = FinetuningJobEventSerializer(many=True, read_only=True)
    deployed_model_id = serializers.SerializerMethodField()
    cell_info = serializers.SerializerMethodField()
    eval_dataset = serializers.PrimaryKeyRelatedField(queryset=Dataset.objects.all())
    eval_set = serializers.PrimaryKeyRelatedField(queryset=EvalSet.objects.all())
    baseline_model = serializers.CharField(required=False, max_length=255)

    class Meta:
        model = FinetuningJob
        fields = [
            "id",
            "project",
            "capability",
            "dataset",
            "eval_dataset",
            "eval_cell",
            "eval_set",
            "eval_incumbent_before",
            "eval_incumbent_after",
            "eval_model_before",
            "eval_model_after",
            "validation_enabled",
            "validation_split_ratio",
            "validation_dataset",
            "cell",
            "cell_info",
            "validation_cell",
            "split_method",
            "triggered_by",
            "name",
            "use_case",
            "base_model",
            "baseline_model",
            "hyperparameters",
            "status",
            "group_id",
            "model_tier",
            "provider",
            "model_weights_location",
            "output_model_name",
            "deployed_model_id",
            "progress",
            "result",
            "error_message",
            "retry_count",
            "max_retries",
            "celery_task_id",
            "cost_usd",
            "billed_minutes",
            "cost_synced_at",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "events",
        ]
        read_only_fields = [
            "id",
            "triggered_by",
            "status",
            "provider",
            "model_weights_location",
            "output_model_name",
            "deployed_model_id",
            "progress",
            "result",
            "error_message",
            "retry_count",
            "celery_task_id",
            "cost_usd",
            "billed_minutes",
            "cost_synced_at",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "events",
        ]

    def get_cell_info(self, obj: FinetuningJob) -> dict | None:
        return _describe_cell(obj)

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_deployed_model_id(self, obj: FinetuningJob) -> str | None:
        return _deployed_model_id(obj)

    def validate_project(self, value):
        return _validate_project_ownership(self, value)

    def validate_validation_split_ratio(self, value):
        if value < 0.05 or value > 0.5:
            raise serializers.ValidationError("Must be between 0.05 and 0.5.")
        return value

    def _check_cell(self, dataset: Dataset, intent: str, *, field: str, explicit=None):
        try:
            return dataset_use.check(dataset, intent, cell=explicit)
        except DatasetError as exc:
            raise serializers.ValidationError({field: exc.detail}) from exc

    @transaction.atomic
    def create(self, validated_data):
        datasets = [
            validated_data.get(key) for key in ("dataset", "validation_dataset", "eval_dataset")
        ]
        # Consistent lock order prevents concurrent jobs over the same pair from deadlocking.
        locked = {
            dataset.pk: dataset
            for dataset in Dataset.objects.select_for_update(of=("self",))
            .select_related("capability")
            .filter(pk__in=[dataset.pk for dataset in datasets if dataset is not None])
            .order_by("pk")
        }
        for field, cell_field, intent in (
            ("dataset", "cell", "train"),
            ("validation_dataset", "validation_cell", "train"),
            ("eval_dataset", "eval_cell", "eval"),
        ):
            dataset = validated_data.get(field)
            if dataset is None:
                continue
            try:
                validated_data[cell_field] = dataset_use.use(
                    locked[dataset.pk],
                    intent,
                    cell=validated_data.get(cell_field),
                )
            except DatasetError as exc:
                raise serializers.ValidationError({field: exc.detail}) from exc
        return super().create(validated_data)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is not None:
            for field in (
                "project",
                "capability",
                "dataset",
                "cell",
                "validation_dataset",
                "validation_cell",
                "eval_dataset",
                "eval_cell",
                "baseline_model",
            ):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError(
                        {
                            field: "Dataset versions, capability and benchmark model are fixed when the job is created."
                        }
                    )
        project = attrs.get("project") or getattr(self.instance, "project", None)
        dataset = attrs.get("dataset") or getattr(self.instance, "dataset", None)
        capability = attrs.get("capability") or getattr(self.instance, "capability", None)
        validation_dataset = attrs.get("validation_dataset")
        if "validation_dataset" not in attrs and self.instance is not None:
            validation_dataset = self.instance.validation_dataset

        if dataset and project and dataset.project_id != project.id:
            raise serializers.ValidationError(
                {"dataset": "Dataset does not belong to this project."}
            )
        if capability and project and capability.project_id != project.id:
            raise serializers.ValidationError(
                {"capability": "Capability does not belong to this project."}
            )
        from overbae.services.finetuning_eval import resolve_baseline_model

        incumbent = attrs.get(
            "baseline_model",
            resolve_baseline_model(
                SimpleNamespace(
                    capability=capability,
                    baseline_model=getattr(self.instance, "baseline_model", ""),
                )
            ),
        )
        if self.instance is None and "baseline_model" in attrs and incumbent:
            codebase_model = (getattr(capability, "model", "") or "").strip()
            if (
                incumbent != codebase_model
                and not DeployedModel.objects.filter(
                    project=project,
                    model_id=incumbent,
                    status=DeployedModel.Status.READY,
                    finetuning_job__isnull=False,
                ).exists()
            ):
                raise serializers.ValidationError(
                    {
                        "baseline_model": "Select the codebase incumbent or a ready trained model in this project."
                    }
                )
        if self.instance is not None and self.instance.status != FinetuningJob.Status.QUEUED:
            for field in (
                "eval_incumbent_before",
                "eval_incumbent_after",
                "eval_model_before",
                "eval_model_after",
            ):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError(
                        {field: "Evaluation choices cannot change after setup starts."}
                    )
        if self.instance is None:
            attrs.setdefault("eval_incumbent_before", False)
            attrs.setdefault("eval_model_before", True)
            attrs["baseline_model"] = incumbent
        for field in ("eval_incumbent_before", "eval_incumbent_after"):
            if not attrs.get(field, getattr(self.instance, field, False)):
                continue
            if not incumbent:
                raise serializers.ValidationError(
                    {field: "Select a benchmark model in training setup."}
                )
            if self.instance is None:
                selected = DeployedModel.objects.filter(project=project, model_id=incumbent).first()
                if selected is not None and selected.status != DeployedModel.Status.READY:
                    raise serializers.ValidationError(
                        {
                            field: "The benchmark model is unavailable. Select another in training setup."
                        }
                    )
            break
        if dataset and (self.instance is None or "dataset" in attrs or "cell" in attrs):
            attrs["cell"] = self._check_cell(
                dataset, "train", field="dataset", explicit=attrs.get("cell")
            )

        base_model = attrs.get("base_model") or getattr(self.instance, "base_model", None)
        entry = None
        training_kind_field = "lora"
        if base_model:
            from overbae.modal.training_type import training_enabled
            from overbae.services.recommendation import find_catalog_model

            entry = find_catalog_model(base_model)
            if entry is None:
                raise serializers.ValidationError(
                    {
                        "base_model": (
                            f"{base_model!r} is not in the trainable model catalog for the "
                            "active finetuning backend. Pick a model from the training "
                            "catalog (e.g. Qwen/… or meta-llama/Llama-3.x-…-Instruct)."
                        )
                    }
                )
            hp = (
                attrs.get("hyperparameters")
                or getattr(self.instance, "hyperparameters", None)
                or {}
            )
            tt = (hp.get("training_type") or {}) if isinstance(hp, dict) else {}
            training_kind = str(tt.get("type") or "Lora")
            training_kind_field = "lora" if training_kind == "Lora" else "full"
            if not training_enabled(entry, training_kind_field):
                raise serializers.ValidationError(
                    {
                        "hyperparameters": (
                            f"{base_model} does not support "
                            f"{'LoRA' if training_kind_field == 'lora' else 'full'} fine-tuning."
                        )
                    }
                )

        if validation_dataset:
            if project and validation_dataset.project_id != project.id:
                raise serializers.ValidationError(
                    {"validation_dataset": "Dataset does not belong to this project."}
                )
            attrs["validation_cell"] = self._check_cell(
                validation_dataset,
                "train",
                field="validation_dataset",
                explicit=attrs.get("validation_cell"),
            )
            if dataset and validation_dataset.id == dataset.id:
                raise serializers.ValidationError(
                    {"validation_dataset": "Validation dataset must differ from training dataset."}
                )

        eval_dataset = attrs.get("eval_dataset") or getattr(self.instance, "eval_dataset", None)
        if eval_dataset:
            if project and eval_dataset.project_id != project.id:
                raise serializers.ValidationError(
                    {"eval_dataset": "Eval dataset does not belong to this project."}
                )
            eval_product = self._check_cell(
                eval_dataset,
                "eval",
                field="eval_dataset",
                explicit=attrs.get("eval_cell") or getattr(self.instance, "eval_cell", None),
            )
            attrs["eval_cell"] = eval_product
        eval_set = attrs.get("eval_set") or getattr(self.instance, "eval_set", None)
        if eval_set and project and eval_set.project_id != project.id:
            raise serializers.ValidationError(
                {"eval_set": "Eval set does not belong to this project."}
            )
        if (
            eval_set
            and not eval_set.members.filter(
                role=EvalSetMember.Role.GENERATIVE, enabled=True, evaluator_id__isnull=False
            ).exists()
        ):
            raise serializers.ValidationError(
                {"eval_set": "This eval set has no generative evaluators."}
            )

        # Baseten/Modal: rows over the *selected training kind's* max fine-tuning
        # context are rejected outright — MAX_LENGTH truncation would corrupt
        # training targets. Check train + validation; require headroom.
        from django.conf import settings

        if getattr(settings, "FINETUNING_BACKEND", "") == "baseten" and entry:
            from overbae.modal.model_registry import context_headroom
            from overbae.modal.training_type import training_context_length, training_enabled

            model_max = training_context_length(entry, training_kind_field)
            headroom = context_headroom("baseten")
            other_kind = "full" if training_kind_field == "lora" else "lora"
            other_max = training_context_length(entry, other_kind)
            other_supported = training_enabled(entry, other_kind)
            for field_name, product in (
                ("dataset", attrs.get("cell") or getattr(self.instance, "cell", None)),
                ("validation_dataset", attrs.get("validation_cell")),
            ):
                if product is None or model_max is None:
                    continue
                stats = dict(product.stats or {})
                try:
                    max_tokens = int(stats.get("max_token_length") or 0)
                except (TypeError, ValueError):
                    max_tokens = 0
                if max_tokens and int(model_max) < max_tokens + headroom:
                    kind_label = "LoRA" if training_kind_field == "lora" else "full"
                    suggestion = ". Shorten or split the long rows, or pick a longer-context model."
                    if (
                        other_supported
                        and other_max is not None
                        and other_max >= max_tokens + headroom
                    ):
                        other_label = "full" if other_kind == "full" else "LoRA"
                        suggestion = f". Switch to {other_label} fine-tuning, which supports {other_max:,} tokens for this model."
                    raise serializers.ValidationError(
                        {
                            field_name: (
                                f"Longest dataset row is ≈{max_tokens:,} tokens but this model's "
                                f"{kind_label} fine-tuning context is {int(model_max):,} tokens"
                                + (
                                    f" (need {max_tokens + headroom:,} with headroom)"
                                    if headroom
                                    else ""
                                )
                                + suggestion
                            )
                        }
                    )

        return attrs


# Read-only shapes so the generated TypeScript client is fully typed.


class FinetuningGpuConfigSerializer(serializers.Serializer):
    gpu_type = serializers.CharField(allow_null=True)
    num_gpus = serializers.IntegerField()
    max_model_len = serializers.IntegerField(allow_null=True)


class FinetuningCostEstimateSerializer(serializers.Serializer):
    usd = serializers.FloatField()
    # Null on the self-hosted backends: they bill GPU-minutes, not tokens.
    price_per_million_usd = serializers.FloatField(allow_null=True)
    trained_tokens = serializers.IntegerField()
    minimum_applied = serializers.BooleanField()


class FinetuningTimeEstimateSerializer(serializers.Serializer):
    seconds = serializers.IntegerField()
    human = serializers.CharField()


class FinetuningTrainingMethodSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    context_length = serializers.IntegerField(allow_null=True, required=False)
    validated_context_length = serializers.BooleanField(required=False)


class FinetuningCatalogTrainingTypeSerializer(serializers.Serializer):
    """models.json ``finetuning.training_type`` — which FT methods are allowed."""

    lora = FinetuningTrainingMethodSerializer()
    full = FinetuningTrainingMethodSerializer()


class FinetuningSkillScoreSerializer(serializers.Serializer):
    """One weighted skill, read against the graded candidates for this dataset —
    ``rank_in_field`` of ``field_n``. ``percentile_global`` is the same skill read against
    every model the benchmark artifact tracks, the population the benchmark rows use."""

    skill = serializers.CharField()
    weight = serializers.FloatField()
    percentile_in_field = serializers.FloatField()
    rank_in_field = serializers.IntegerField()
    field_n = serializers.IntegerField()
    percentile_global = serializers.FloatField()


class FinetuningEvidenceSerializer(serializers.Serializer):
    """One benchmark behind a grade. ``percentile`` is standing inside that benchmark's
    own cohort — raw scores are not comparable across benchmarks and are never sent."""

    benchmark = serializers.CharField()
    skill = serializers.CharField()
    percentile = serializers.FloatField()
    cohort_n = serializers.IntegerField()
    source = serializers.CharField()
    url = serializers.CharField(allow_blank=True)
    provenance = serializers.ChoiceField(choices=["measured", "lab_claimed"])


class FinetuningExperimentSerializer(serializers.Serializer):
    """``match`` and ``skill_scores`` are standing among the ``match_pool`` graded candidates
    for this dataset; ``grade`` and below are percentiles over every model the benchmark
    artifact tracks."""

    tier = serializers.CharField()
    model = serializers.CharField()
    display_name = serializers.CharField()
    params = serializers.CharField()
    total_params_b = serializers.FloatField(required=False)
    context_length_sft = serializers.IntegerField(allow_null=True, required=False)
    max_batch_size = serializers.IntegerField(allow_null=True, required=False)
    min_batch_size = serializers.IntegerField(allow_null=True, required=False)
    match = serializers.FloatField(allow_null=True)
    match_rank = serializers.IntegerField(allow_null=True)
    match_pool = serializers.IntegerField()
    grade = serializers.FloatField(allow_null=True)
    adjusted_grade = serializers.FloatField(allow_null=True)
    lower_bound = serializers.FloatField(allow_null=True)
    confidence = serializers.ChoiceField(choices=["high", "medium", "low", "none"])
    n_benchmarks = serializers.IntegerField()
    skill_scores = FinetuningSkillScoreSerializer(many=True)
    evidence = FinetuningEvidenceSerializer(many=True)
    selected = serializers.BooleanField()
    hyperparams = serializers.JSONField()
    use_lora = serializers.BooleanField()
    training_type = FinetuningCatalogTrainingTypeSerializer(required=False)
    gpu_config = FinetuningGpuConfigSerializer()
    cost_estimate = FinetuningCostEstimateSerializer(allow_null=True)
    time_estimate = FinetuningTimeEstimateSerializer()
    learning_rate_lora = serializers.FloatField()
    learning_rate_full = serializers.FloatField()
    hyperparam_reasons = serializers.JSONField()


class FinetuningExcludedModelSerializer(serializers.Serializer):
    model = serializers.CharField()
    reason = serializers.CharField()


class FinetuningRecommendationDatasetSerializer(serializers.Serializer):
    rows = serializers.IntegerField()
    total_tokens = serializers.IntegerField()
    max_token_length = serializers.IntegerField()
    has_tool_calling = serializers.BooleanField()


class FinetuningBenchmarkSnapshotSerializer(serializers.Serializer):
    """Which artifact the grades came from. Each evidence row carries its own benchmark
    reference, so the snapshot only dates them."""

    generated_at = serializers.CharField()


class FinetuningRecommendationResponseSerializer(serializers.Serializer):
    task_type = serializers.CharField()
    task_type_source = serializers.ChoiceField(
        choices=["capability", "semantic", "heuristic", "unknown"]
    )
    capability_context = serializers.DictField(allow_null=True)
    skill_weights = serializers.DictField(child=serializers.FloatField())
    dataset = FinetuningRecommendationDatasetSerializer()
    candidates = FinetuningExperimentSerializer(many=True)
    excluded = FinetuningExcludedModelSerializer(many=True)
    #: The models the wizard opens with. Each candidate's own ``selected`` says which of
    #: them starts checked.
    shown = serializers.ListField(child=serializers.CharField())
    benchmark_snapshot = FinetuningBenchmarkSnapshotSerializer()


class FinetuningEstimateRequestSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    base_model = serializers.CharField()
    n_epochs = serializers.IntegerField(min_value=1)
    use_lora = serializers.BooleanField()


class FinetuningModelDefaultsRequestSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    base_model = serializers.CharField()


class FinetuningEstimateResponseSerializer(serializers.Serializer):
    cost_estimate = FinetuningCostEstimateSerializer(allow_null=True)
    time_estimate = FinetuningTimeEstimateSerializer()
    trained_tokens = serializers.IntegerField()


class DatasetOverlapResponseSerializer(serializers.Serializer):
    overlap_count = serializers.IntegerField()
    train_total = serializers.IntegerField()
    basis = serializers.CharField()
    examples = serializers.ListField(child=serializers.JSONField(), required=False)
    near_duplicate_check = serializers.CharField(required=False)


class FinetuningModelCatalogEntrySerializer(serializers.Serializer):
    """One model in the fine-tuning catalog (not the OpenRouter one below)."""

    id = serializers.CharField()
    display = serializers.CharField()
    params = serializers.CharField()
    total_params_b = serializers.FloatField()
    context_length_sft = serializers.IntegerField()
    max_batch_size = serializers.IntegerField()
    min_batch_size = serializers.IntegerField()
    supports_tool_calling = serializers.BooleanField()
    training_type = FinetuningCatalogTrainingTypeSerializer()
    # Optional keys; some backends omit them.
    context_length = serializers.IntegerField(required=False)
    disabled = serializers.BooleanField(required=False)
    disabled_reason = serializers.CharField(required=False, allow_blank=True)


class FinetuningModelCatalogResponseSerializer(serializers.Serializer):
    """Response of ``GET /api/finetuning-jobs/models/``."""

    backend = serializers.ChoiceField(choices=["together", "baseten", "modal"])
    tiers = serializers.ListField(child=serializers.ChoiceField(choices=FinetuningJob.Tier.choices))
    models = serializers.DictField(child=FinetuningModelCatalogEntrySerializer(many=True))
    has_tool_calling = serializers.BooleanField()
    max_context = serializers.IntegerField(allow_null=True)


class DatasetValidationStatsSerializer(serializers.Serializer):
    """``ValidationResult.stats`` — sparse; keys depend on validation path."""

    total_examples = serializers.IntegerField(required=False)
    trainable_examples = serializers.IntegerField(required=False)
    train_examples = serializers.IntegerField(required=False)
    val_examples = serializers.IntegerField(required=False)
    validation_mode = serializers.CharField(required=False, allow_blank=True)
    split_method = serializers.CharField(required=False, allow_blank=True)
    format = serializers.CharField(required=False, allow_blank=True)
    tool_calling_examples = serializers.IntegerField(required=False)
    max_token_length = serializers.IntegerField(required=False)


class DatasetValidationResponseSerializer(serializers.Serializer):
    """Mirrors ``ValidationResult.as_dict()``."""

    valid = serializers.BooleanField()
    format = serializers.CharField(allow_blank=True)
    num_examples = serializers.IntegerField()
    errors = serializers.ListField(child=serializers.CharField())
    warnings = serializers.ListField(child=serializers.CharField())
    stats = DatasetValidationStatsSerializer()


class ConnectorSourceProjectSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class ConnectorCapabilitiesSerializer(serializers.Serializer):
    exact_count = serializers.BooleanField()
    capability_sources = serializers.ListField(child=serializers.CharField())
    needs_source_project = serializers.BooleanField()
    retention_note = serializers.CharField(required=False, allow_blank=True)


class ConnectorVerifyResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    api_version = serializers.CharField(required=False)
    projects = ConnectorSourceProjectSerializer(many=True, required=False)
    capabilities = ConnectorCapabilitiesSerializer(required=False)
    detail = serializers.CharField(required=False)


class ConnectorActiveConfigSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    version = serializers.IntegerField()
    source_project_id = serializers.CharField(allow_blank=True)
    target_project_id = serializers.UUIDField(allow_null=True)
    lookback_days = serializers.IntegerField(allow_null=True)
    backfill_from = serializers.DateTimeField(allow_null=True)
    backfill_to = serializers.DateTimeField(allow_null=True)
    effective_from = serializers.DateTimeField()


class ConnectorSyncConfigWriteSerializer(serializers.Serializer):
    source_project_id = serializers.CharField(required=False, allow_blank=True, default="")
    target_project_id = serializers.UUIDField(required=False, allow_null=True)
    lookback_days = serializers.IntegerField(required=False, allow_null=True)
    backfill_from = serializers.DateTimeField(required=False, allow_null=True)
    backfill_to = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        if (
            attrs.get("backfill_from") is not None
            and attrs.get("backfill_to") is not None
            and attrs["backfill_from"] >= attrs["backfill_to"]
        ):
            raise serializers.ValidationError("backfill_from must be before backfill_to.")
        return attrs


class ConnectorSyncConfigCreateResponseSerializer(serializers.Serializer):
    version = serializers.IntegerField()
    effective_from = serializers.DateTimeField()


class ConnectorPreviewRequestSerializer(serializers.Serializer):
    lookback_days = serializers.IntegerField(required=False, allow_null=True)
    source_project_id = serializers.CharField(required=False, allow_blank=True, default="")
    backfill_from = serializers.DateTimeField(required=False, allow_null=True)
    backfill_to = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        if (
            attrs.get("backfill_from") is not None
            and attrs.get("backfill_to") is not None
            and attrs["backfill_from"] >= attrs["backfill_to"]
        ):
            raise serializers.ValidationError("backfill_from must be before backfill_to.")
        return attrs


class ConnectorPreviewResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField(allow_null=True)


class ConnectorCapabilityCandidateSerializer(serializers.Serializer):
    value = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    count = serializers.IntegerField()
    source = serializers.CharField(required=False, allow_blank=True)
    key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    metadata_keys = serializers.JSONField(required=False)


class ConnectorShapeCandidateSerializer(serializers.Serializer):
    """One recurring observation shape, ranked as a possible capability boundary."""

    name = serializers.CharField()
    type = serializers.CharField()
    traces = serializers.IntegerField()
    occurrences = serializers.IntegerField()
    max_per_trace = serializers.IntegerField()
    model_calls = serializers.IntegerField()
    is_root = serializers.BooleanField()
    parent_name = serializers.CharField(allow_null=True)
    score = serializers.IntegerField()
    reasons = serializers.ListField(child=serializers.CharField())


class ConnectorCapabilityProposalSerializer(serializers.Serializer):
    """A suggested Capability for one discovered key, and where it came from."""

    capability_id = serializers.UUIDField()
    capability_name = serializers.CharField()
    method = serializers.ChoiceField(choices=["source", "name", "capability_card"])
    evidence = serializers.CharField(allow_blank=True)


class ConnectorDiscoverCapabilitiesResponseSerializer(serializers.Serializer):
    candidates = ConnectorCapabilityCandidateSerializer(many=True)
    shapes = ConnectorShapeCandidateSerializer(many=True, required=False)
    proposals = serializers.DictField(child=ConnectorCapabilityProposalSerializer(), required=False)
    lookback_days = serializers.IntegerField(required=False)
    sampled = serializers.IntegerField(required=False)


class ConnectorCapabilityMappingWriteSerializer(serializers.Serializer):
    source = serializers.ChoiceField(
        choices=["observation_name", "metadata", "tag", "trace_name"],
        required=False,
        allow_blank=True,
    )
    key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    names = serializers.ListField(child=serializers.CharField(), required=False)
    assignments = serializers.DictField(child=serializers.CharField(), required=False, default=dict)
    auto_create = serializers.BooleanField(required=False, default=False)
    fallback_capability_id = serializers.UUIDField(required=False, allow_null=True)


class ConnectorCapabilityMappingResponseSerializer(serializers.Serializer):
    capability_mapping = serializers.JSONField()
    relabeled_span_count = serializers.IntegerField()


class ConnectorCredentialSerializer(serializers.ModelSerializer):
    api_key = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=512,
    )
    api_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    api_key_hint = serializers.SerializerMethodField(read_only=True)
    active_config = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ConnectorCredential
        fields = [
            "id",
            "project",
            "name",
            "connector_type",
            "base_url",
            "api_key",
            "api_key_hint",
            "api_secret",
            "is_active",
            "auto_sync_enabled",
            "poll_interval_seconds",
            "last_synced_at",
            "sync_status",
            "backfill_imported",
            "backfill_total",
            "sync_error",
            "api_version",
            "verified_at",
            "total_spans_imported",
            "total_traces_imported",
            "next_poll_at",
            "capability_mapping",
            "active_config",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "last_synced_at",
            "api_key_hint",
            "is_active",
            "sync_status",
            "backfill_imported",
            "backfill_total",
            "sync_error",
            "api_version",
            "verified_at",
            "total_spans_imported",
            "total_traces_imported",
            "next_poll_at",
            "active_config",
        ]
        # Only connected integrations own a name; a soft-disconnected row (kept
        # for its span-id namespace) or an unfinished wizard draft may be reused.
        validators = [
            UniqueTogetherValidator(
                queryset=ConnectorCredential.objects.filter(is_active=True).exclude(
                    configs__isnull=True
                ),
                fields=["project", "connector_type", "name"],
            )
        ]

    def get_api_key_hint(self, obj: ConnectorCredential) -> str:
        return obj.api_key_hint

    @extend_schema_field(ConnectorActiveConfigSerializer(allow_null=True))
    def get_active_config(self, obj: ConnectorCredential) -> dict | None:
        config = obj.active_config()
        if config is None:
            return None
        return {
            "id": str(config.id),
            "version": config.version,
            "source_project_id": config.source_project_id,
            "target_project_id": str(config.target_project_id)
            if config.target_project_id
            else None,
            "lookback_days": config.lookback_days,
            "effective_from": config.effective_from.isoformat(),
        }

    def validate_project(self, value: Project) -> Project:
        request = self.context.get("request")
        if request is None:
            return value
        allowed = {str(pid) for pid in project_ids_for(request.user, request.auth)}
        if str(value.pk) not in allowed:
            raise serializers.ValidationError(
                "This API key is scoped to another project."
                if isinstance(request.auth, APIToken)
                else "You are not a member of this project."
            )
        return value

    def validate_poll_interval_seconds(self, value: int) -> int:
        return max(60, min(value, 86400))

    def _apply_credential_fields(self, instance: ConnectorCredential, validated_data: dict) -> None:
        """Write api_key / api_secret only when non-blank values are provided."""
        api_key = validated_data.pop("api_key", None)
        api_secret = validated_data.pop("api_secret", None)
        if api_key:
            instance.api_key = api_key
        if api_secret:
            instance.api_secret = api_secret

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        return (url or "").strip().rstrip("/")

    def _find_reusable_same_key(
        self,
        *,
        project: Project,
        connector_type: str,
        api_key: str,
        base_url: str,
    ) -> ConnectorCredential | None:
        """A live configured connection is never matched — reconnecting must not
        take over an integration that is already syncing."""
        if not api_key:
            return None
        want_host = self._normalize_base_url(base_url)
        # EncryptedField — compare in Python after decrypt-on-read.
        candidates = ConnectorCredential.objects.filter(
            project=project,
            connector_type=connector_type,
        ).filter(Q(is_active=False) | Q(configs__isnull=True))
        for cred in candidates:
            if cred.api_key == api_key and self._normalize_base_url(cred.base_url) == want_host:
                return cred
        return None

    def _free_name_slot(self, *, project: Project, connector_type: str, name: str) -> None:
        """``unique_together`` is enforced in the database, so the slot must be
        vacated. A draft is disposable; a disconnected integration is renamed
        because its span ids must survive."""
        conflict = (
            ConnectorCredential.objects.filter(
                project=project,
                connector_type=connector_type,
                name=name,
            )
            .filter(Q(is_active=False) | Q(configs__isnull=True))
            .order_by("-updated_at")
            .first()
        )
        if not conflict:
            return
        if conflict.active_config() is None:
            conflict.delete()
            return
        conflict.name = f"{name} · disconnected · {str(conflict.id)[:8]}"
        conflict.save(update_fields=["name", "updated_at"])

    def create(self, validated_data: dict) -> ConnectorCredential:
        api_key = validated_data.pop("api_key", "") or ""
        api_secret = validated_data.pop("api_secret", "") or ""
        project = validated_data["project"]
        connector_type = validated_data["connector_type"]
        base_url = validated_data.get("base_url", "") or ""

        inactive = self._find_reusable_same_key(
            project=project,
            connector_type=connector_type,
            api_key=api_key,
            base_url=base_url,
        )
        if inactive is not None:
            # Reconnect: same row → same span_id namespace.
            if validated_data.get("name"):
                inactive.name = validated_data["name"]
            inactive.base_url = base_url
            if api_key:
                inactive.api_key = api_key
            if api_secret:
                inactive.api_secret = api_secret
            inactive.is_active = True
            inactive.auto_sync_enabled = bool(validated_data.get("auto_sync_enabled", False))
            inactive.sync_error = ""
            inactive.sync_retry_count = 0
            inactive.next_poll_at = None
            inactive.save()
            return inactive

        self._free_name_slot(
            project=project,
            connector_type=connector_type,
            name=validated_data.get("name") or "",
        )
        instance = ConnectorCredential(**validated_data)
        instance.api_key = api_key
        instance.api_secret = api_secret
        instance.save()
        return instance

    def update(self, instance: ConnectorCredential, validated_data: dict) -> ConnectorCredential:
        # Re-enabling auto-sync (or rotating credentials) is the user's "try
        # again" — clear the failure counter the sync task uses to give up.
        re_enabled = validated_data.get("auto_sync_enabled") and not instance.auto_sync_enabled
        rotated = bool(validated_data.get("api_key") or validated_data.get("api_secret"))
        self._apply_credential_fields(instance, validated_data)
        if re_enabled or rotated:
            instance.sync_retry_count = 0
            instance.sync_error = ""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class ConnectorSyncRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConnectorSyncRun
        fields = [
            "id",
            "credential",
            "config_version",
            "mode",
            "window_from",
            "window_to",
            "traces_seen",
            "spans_created",
            "spans_skipped",
            "status",
            "error",
            "started_at",
            "finished_at",
        ]
        read_only_fields = fields


class CatalogModelSerializer(serializers.Serializer):
    """``id`` is the OpenRouter slug and exactly what ``EvalVariant.model_name``
    carries — ``call_llm`` routes slash-style slugs through OpenRouter verbatim."""

    id = serializers.CharField()
    name = serializers.CharField()
    provider = serializers.CharField()
    context_length = serializers.IntegerField(allow_null=True)
    # $ per 1M tokens, normalized from OpenRouter's $-per-token strings.
    prompt_price = serializers.FloatField(allow_null=True)
    completion_price = serializers.FloatField(allow_null=True)
    # True when the slug maps onto our curated SUPPORTED_LLM_MODELS catalog.
    curated = serializers.BooleanField()


class ModelDefaultsSerializer(serializers.Serializer):
    judge_model = serializers.CharField()
    judge_models = serializers.ListField(child=serializers.CharField())
    backtest_models = serializers.ListField(child=serializers.CharField())


class ModelCatalogResponseSerializer(serializers.Serializer):
    models = CatalogModelSerializer(many=True)
    # False when the upstream fetch failed (models is then empty, not a 500).
    upstream_available = serializers.BooleanField()
    defaults = ModelDefaultsSerializer()


class PublicModelFinetuningSerializer(serializers.Serializer):
    context_length = serializers.IntegerField(allow_null=True)
    training_type = FinetuningCatalogTrainingTypeSerializer()


class PublicModelAvailabilitySerializer(serializers.Serializer):
    finetuning = serializers.BooleanField()
    inference = serializers.BooleanField()


class PublicModelPricingSerializer(serializers.Serializer):
    """Marketing 'from' floors sourced from models.json pricing."""

    train_from_usd = serializers.FloatField()
    run_from_usd_per_1m_output = serializers.FloatField()


class PublicModelMarketingSerializer(serializers.Serializer):
    """Buyer-facing copy from models.json ``marketing`` (per model)."""

    pitch = serializers.CharField(allow_null=True)
    overview = serializers.CharField(allow_null=True)
    description = serializers.CharField(allow_null=True)
    good_for = serializers.ListField(child=serializers.CharField(), allow_empty=True)


class PublicLibraryFilterOptionSerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    min_context_length = serializers.IntegerField(required=False)


class PublicLibraryFiltersSerializer(serializers.Serializer):
    """Filter definitions from models.json ``library_filters``."""

    use_case = PublicLibraryFilterOptionSerializer(many=True)
    tier = PublicLibraryFilterOptionSerializer(many=True)
    context = PublicLibraryFilterOptionSerializer(many=True)


class PublicModelSerializer(serializers.Serializer):
    """Public, unauthenticated library entry; never reflects a user's own
    fine-tuned instances."""

    id = serializers.CharField()
    slug = serializers.CharField()
    display = serializers.CharField()
    provider = serializers.CharField()
    group = serializers.CharField(allow_null=True)
    category = serializers.ChoiceField(choices=["open-weight", "frontier"])
    hf_model_id = serializers.CharField(allow_null=True)
    tier = serializers.CharField(allow_null=True)
    params = serializers.CharField(allow_null=True)
    total_params_b = serializers.FloatField(allow_null=True)
    context_length = serializers.IntegerField(allow_null=True)
    supports_tool_calling = serializers.BooleanField()
    use_cases = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    type = serializers.CharField(allow_null=True)
    hardware = serializers.CharField(allow_null=True)
    available_for = PublicModelAvailabilitySerializer()
    finetuning = PublicModelFinetuningSerializer(allow_null=True)
    pricing = PublicModelPricingSerializer(allow_null=True)
    marketing = PublicModelMarketingSerializer(allow_null=True)


class PublicModelListResponseSerializer(serializers.Serializer):
    models = PublicModelSerializer(many=True)
    filters = PublicLibraryFiltersSerializer()


class CheckoutSessionResponseSerializer(serializers.Serializer):
    """Stripe Checkout Session URL for redirecting the browser."""

    checkout_url = serializers.URLField()


class PlanUsageUnitSerializer(serializers.Serializer):
    used = serializers.IntegerField(required=False)
    limit = serializers.IntegerField(allow_null=True)


class PlanUsageSerializer(serializers.Serializer):
    optimize_runs = PlanUsageUnitSerializer()
    training_jobs = PlanUsageUnitSerializer()
    deploy_jobs = PlanUsageUnitSerializer()
    projects = PlanUsageUnitSerializer()
    seats = PlanUsageUnitSerializer()


class CreditTopUpRequestSerializer(serializers.Serializer):
    """One-time credit purchase in whole dollars. 1 USD = 100 credits."""

    amount_usd = serializers.IntegerField(min_value=MIN_TOPUP_USD, max_value=MAX_TOPUP_USD)


class SubscriptionSerializer(serializers.Serializer):
    """Current plan / subscription state for the authenticated user."""

    billing_enabled = serializers.BooleanField()
    plan = serializers.ChoiceField(choices=["free", "pro"])
    status = serializers.CharField(allow_null=True)
    credits_usd = serializers.DecimalField(max_digits=12, decimal_places=4)
    credits_granted_usd = serializers.DecimalField(max_digits=12, decimal_places=4)
    credits_spent_usd = serializers.DecimalField(max_digits=12, decimal_places=4)
    start_date = serializers.DateTimeField(allow_null=True)
    end_date = serializers.DateTimeField(allow_null=True)
    cancel_at_period_end = serializers.BooleanField()
    usage = PlanUsageSerializer()


class BillingTelemetrySerializer(serializers.ModelSerializer):
    """One credits ledger row for the authenticated user."""

    project_id = serializers.UUIDField(allow_null=True, read_only=True)
    project_name = serializers.CharField(source="project.name", allow_null=True, read_only=True)
    service_label = serializers.CharField(source="get_service_display", read_only=True)

    class Meta:
        model = BillingTelemetry
        fields = [
            "id",
            "amount",
            "timestamp",
            "service",
            "service_label",
            "project_id",
            "project_name",
        ]


class DeploymentProgressSerializer(serializers.Serializer):
    stage = serializers.CharField()
    label = serializers.CharField()
    attempt = serializers.IntegerField()
    retry_at = serializers.DateTimeField(allow_null=True)
    deadline = serializers.DateTimeField(allow_null=True)
    last_error = serializers.CharField(allow_null=True)


class DeployedModelSerializer(serializers.ModelSerializer):
    deployment_progress = serializers.SerializerMethodField()
    finetuning_job_id = serializers.UUIDField(read_only=True)
    finetuning_job_name = serializers.SerializerMethodField()
    capability_id = serializers.SerializerMethodField()
    capability_name = serializers.SerializerMethodField()
    # Aggregates annotated in DeployedModelViewSet.get_queryset (default 0/null
    # so the serializer stays usable for un-annotated instances).
    request_count = serializers.IntegerField(read_only=True, default=0)
    last_active_at = serializers.DateTimeField(read_only=True, allow_null=True, default=None)
    total_tokens = serializers.IntegerField(read_only=True, allow_null=True, default=None)
    avg_tokens_per_second = serializers.FloatField(read_only=True, allow_null=True, default=None)
    avg_latency_ms = serializers.FloatField(read_only=True, allow_null=True, default=None)
    cost_total = serializers.FloatField(read_only=True, allow_null=True, default=None)
    cost_this_month = serializers.FloatField(read_only=True, allow_null=True, default=None)

    class Meta:
        model = DeployedModel
        fields = [
            "id",
            "project",
            "model_id",
            "deployment_progress",
            "status",
            "quantization",
            "base_model_id",
            "gpu_type",
            "is_lora",
            "lora_rank",
            "weights_path",
            "checkpoint_hash",
            "max_model_len",
            "num_parameters",
            "sla_tier",
            "inference_url",
            "error_message",
            "created_at",
            "deployed_at",
            "finetuning_job_id",
            "finetuning_job_name",
            "capability_id",
            "capability_name",
            "request_count",
            "last_active_at",
            "total_tokens",
            "avg_tokens_per_second",
            "avg_latency_ms",
            "cost_total",
            "cost_this_month",
        ]
        read_only_fields = fields

    @extend_schema_field(DeploymentProgressSerializer)
    def get_deployment_progress(self, obj):
        return deployment_progress(obj)

    def get_finetuning_job_name(self, obj) -> str | None:
        if obj.finetuning_job_id:
            return getattr(obj.finetuning_job, "name", None)
        return None

    def get_capability_id(self, obj) -> str | None:
        job = obj.finetuning_job if obj.finetuning_job_id else None
        return str(job.capability_id) if job and job.capability_id else None

    def get_capability_name(self, obj) -> str | None:
        job = obj.finetuning_job if obj.finetuning_job_id else None
        capability = getattr(job, "capability", None) if job else None
        return getattr(capability, "name", None)


class InferenceMetricsSerializer(serializers.Serializer):
    """Aggregate usage/latency/cost for a deployed model (from InferenceCall)."""

    request_count = serializers.IntegerField()
    prompt_tokens = serializers.IntegerField()
    completion_tokens = serializers.IntegerField()
    total_tokens = serializers.IntegerField()
    cost = serializers.FloatField(allow_null=True)
    cost_this_month = serializers.FloatField(allow_null=True)
    cost_is_estimate = serializers.BooleanField()
    # Median (p50) of warm-call throughput (vLLM engine TPS) — robust to outliers.
    avg_tokens_per_second = serializers.FloatField(allow_null=True)
    # Median (p50) warm-call model-inference latency (vLLM TTFT + decode, so
    # queue/network/cold-start excluded). Median (not mean) so long-generation
    # outliers don't skew the headline.
    avg_latency_ms = serializers.FloatField(allow_null=True)
    # p95 warm-call model-inference latency — the tail, surfaced alongside median.
    latency_p95_ms = serializers.FloatField(allow_null=True)
    # Average wall-clock of cold-start calls (includes container boot); null when
    # none. Kept off the headline — surfaced quietly.
    cold_start_ms = serializers.FloatField(allow_null=True)
    # Savings vs the capability's original (frontier) model: what these tokens would
    # have cost there, minus our GPU-time cost. Null when the baseline model or
    # its pricing is unknown.
    baseline_model = serializers.CharField(allow_null=True, allow_blank=True)
    baseline_cost = serializers.FloatField(allow_null=True)
    savings = serializers.FloatField(allow_null=True)


class InferenceActivityPointSerializer(serializers.Serializer):
    """One time-bucket in the activity chart."""

    bucket = serializers.DateTimeField()
    request_count = serializers.IntegerField()
    total_tokens = serializers.IntegerField()
    avg_tokens_per_second = serializers.FloatField(allow_null=True)
    avg_latency_ms = serializers.FloatField(allow_null=True)


class CheckpointFileSerializer(serializers.Serializer):
    """A downloadable weight/checkpoint file (presigned URL)."""

    name = serializers.CharField()
    size_bytes = serializers.IntegerField(allow_null=True)
    download_url = serializers.URLField()


class InferenceLiveStatsSerializer(serializers.Serializer):
    """``recently_active`` is the authoritative live signal; the raw worker counts
    are flaky (web_server workers bypass the input queue) and only supplement it."""

    backlog = serializers.IntegerField(allow_null=True)
    num_running_inputs = serializers.IntegerField(allow_null=True)
    num_total_runners = serializers.IntegerField(allow_null=True)
    recently_active = serializers.BooleanField()
    warming = serializers.BooleanField()
    available = serializers.BooleanField()


class InferenceActivitySerializer(serializers.Serializer):
    """Container so the activity series isn't wrapped in a pagination envelope."""

    points = InferenceActivityPointSerializer(many=True)


class ModelCheckpointsSerializer(serializers.Serializer):
    files = CheckpointFileSerializer(many=True)
