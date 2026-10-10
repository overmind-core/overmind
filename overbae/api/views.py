import logging
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db.models import Avg, Count, Max, Min, OuterRef, Q, Subquery, Sum
from django.db.models.expressions import RawSQL
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import mixins, status, viewsets
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.response import Response

from overbae.api.filters import (
    DeployedModelFilter,
    FinetuningJobFilter,
    ProjectFilter,
    SessionFilter,
    SpanFilter,
)
from overbae.api.native_evaluation import (
    NativeEvaluationRequestSerializer,
    NativeEvaluationSerializer,
)
from overbae.api.scoping import project_ids_for
from overbae.api.serializers import (
    ConnectorCapabilityMappingResponseSerializer,
    ConnectorCapabilityMappingWriteSerializer,
    ConnectorCredentialSerializer,
    ConnectorDiscoverCapabilitiesResponseSerializer,
    ConnectorPreviewRequestSerializer,
    ConnectorPreviewResponseSerializer,
    ConnectorSyncConfigCreateResponseSerializer,
    ConnectorSyncConfigWriteSerializer,
    ConnectorSyncRunSerializer,
    ConnectorVerifyResponseSerializer,
    DatasetOverlapResponseSerializer,
    DatasetValidateRequestSerializer,
    DatasetValidationResponseSerializer,
    DeployedModelSerializer,
    FeedbackCreateSerializer,
    FinetuningEstimateRequestSerializer,
    FinetuningEstimateResponseSerializer,
    FinetuningExperimentSerializer,
    FinetuningJobEventSerializer,
    FinetuningJobListSerializer,
    FinetuningJobRunSerializer,
    FinetuningJobSerializer,
    FinetuningModelCatalogResponseSerializer,
    FinetuningModelDefaultsRequestSerializer,
    FinetuningRecommendationResponseSerializer,
    InferenceActivityQuerySerializer,
    InferenceActivitySerializer,
    InferenceLiveStatsSerializer,
    InferenceMetricsSerializer,
    InferenceMonitoringQuerySerializer,
    ModelCheckpointsSerializer,
    ModelSwapPromptRequestSerializer,
    ModelSwapPromptSerializer,
    ProjectInviteCreateSerializer,
    ProjectInviteSerializer,
    ProjectMemberSerializer,
    ProjectMembershipCreateSerializer,
    ProjectSerializer,
    RootSpanListSerializer,
    SessionSerializer,
    SpanSerializer,
    TraceDetailSerializer,
    conversation_usage_totals,
    eligible_scoring_capabilities,
    trace_status,
    trace_usage_totals,
    traces_with_finished_scoring_pass,
)
from overbae.api.span_ordering import (
    annotate_sessions_for_ordering,
    annotate_spans_for_ordering,
    llm_model_sql,
)
from overbae.api.training_monitoring import (
    TrainingEvidenceSerializer,
    TrainingMonitoringQuerySerializer,
    TrainingMonitoringSerializer,
)
from overbae.core.errors import InputValidationError
from overbae.models import (
    APIToken,
    Cell,
    ConnectorCredential,
    Conversation,
    Dataset,
    DeployedModel,
    Feedback,
    FinetuningJob,
    FinetuningJobEvent,
    InferenceCall,
    Project,
    ProjectInvite,
    ProjectMembership,
    Span,
    TaskExecution,
    User,
)
from overbae.services import native_evaluation as native_evaluation_service
from overbae.services import training_monitoring
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.deployment import ensure_training_deployment, retry_deployment
from overbae.services.finetuning_checkpoints import (
    CheckpointArchiveError,
    get_checkpoint_download_url,
)
from overbae.services.inference_live import live_worker_stats
from overbae.services.inference_metrics import model_activity, model_metrics, percentile
from overbae.services.training_cancellation import cancel as cancel_training
from overbae.services.training_preparation import (
    live_progress_for_job,
)
from overbae.services.training_preparation import (
    retry_for_job as retry_training_preparation,
)

logger = logging.getLogger(__name__)


# Other api modules import the private name from here.
_user_project_ids = project_ids_for


def _project_membership_count(user) -> int:
    return ProjectMembership.objects.filter(user=user).count()


def _ensure_user_may_acquire_project_membership(user) -> None:
    """Raise PlanLimitExceeded if ``user`` is at their effective Free/Pro project cap."""
    from overbae.services.plan_limits import PlanLimitExceeded, effective_projects_limit

    limit = effective_projects_limit(user)
    if limit is None:
        return
    n = _project_membership_count(user)
    if n >= limit:
        raise PlanLimitExceeded(f"Free plan allows {limit} projects. Upgrade to Pro for unlimited.")


@extend_schema_view(
    list=extend_schema(summary="List projects"),
    retrieve=extend_schema(summary="Get project"),
    create=extend_schema(summary="Create project"),
    partial_update=extend_schema(summary="Update project"),
    destroy=extend_schema(summary="Delete project"),
)
class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    filterset_class = ProjectFilter
    search_fields = ["name", "slug"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]
    lookup_field = "id"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Project.objects.none()
        return (
            Project.objects.filter(id__in=_user_project_ids(self.request.user, self.request.auth))
            .annotate(member_count=Count("memberships"))
            .prefetch_related("memberships__user")
        )

    def perform_create(self, serializer):
        auth = self.request.auth
        if isinstance(auth, APIToken) and not auth.is_account_scoped:
            raise PermissionDenied(
                detail=(
                    "Project-scoped API keys cannot create projects. "
                    "Use an account-scoped key, or set project-id in overmind.toml."
                ),
                code="account_key_required",
            )
        _ensure_user_may_acquire_project_membership(self.request.user)
        project = serializer.save()
        ProjectMembership.objects.create(user=self.request.user, project=project)

    def perform_destroy(self, instance):
        instance.delete()


@extend_schema_view(
    list=extend_schema(
        summary="List project members",
        responses={200: ProjectMemberSerializer(many=True)},
    ),
    create=extend_schema(
        summary="Add a user to the project",
        request=ProjectMembershipCreateSerializer,
        responses={201: ProjectMemberSerializer},
    ),
    destroy=extend_schema(summary="Remove a user from the project"),
)
class ProjectMembershipViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Memberships for a single project (nested under ``/api/projects/{project_id}/memberships/``)."""

    queryset = ProjectMembership.objects.all()
    lookup_field = "id"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ProjectMembership.objects.none()
        project_id = self.kwargs.get("project_id")
        if not project_id:
            return ProjectMembership.objects.none()
        allowed = _user_project_ids(self.request.user, self.request.auth)
        if not Project.objects.filter(id=project_id, id__in=allowed).exists():
            return ProjectMembership.objects.none()
        return ProjectMembership.objects.filter(project_id=project_id).select_related("user")

    def get_serializer_class(self):
        if self.action == "create":
            return ProjectMembershipCreateSerializer
        return ProjectMemberSerializer

    def _project(self):
        return get_object_or_404(
            Project.objects.filter(id__in=_user_project_ids(self.request.user, self.request.auth)),
            id=self.kwargs["project_id"],
        )

    def list(self, request, *args, **kwargs):
        self._project()
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        project = self._project()
        serializer = ProjectMembershipCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.get_user()
        if not ProjectMembership.objects.filter(project=project, user=user).exists():
            from overbae.services.plan_limits import require_seat_for_invite

            require_seat_for_invite(request.user, project)
            _ensure_user_may_acquire_project_membership(user)
        membership, _ = ProjectMembership.objects.get_or_create(project=project, user=user)
        return Response(
            ProjectMemberSerializer(membership).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_destroy(self, instance):
        if instance.user_id == self.request.user.pk:
            raise PermissionDenied(
                "You cannot remove yourself from a project. Delete the project instead."
            )
        instance.delete()


@extend_schema_view(
    list=extend_schema(
        summary="List pending project invites",
        responses={200: ProjectInviteSerializer(many=True)},
    ),
    create=extend_schema(
        summary="Invite an email that has no account",
        request=ProjectInviteCreateSerializer,
        responses={201: ProjectInviteSerializer},
    ),
    destroy=extend_schema(summary="Revoke a pending invite"),
)
class ProjectInviteViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Pending invitations for a single project (nested under ``/api/projects/{project_id}/invites/``)."""

    queryset = ProjectInvite.objects.all()
    serializer_class = ProjectInviteSerializer
    lookup_field = "id"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ProjectInvite.objects.none()
        project_id = self.kwargs.get("project_id")
        if not project_id:
            return ProjectInvite.objects.none()
        allowed = _user_project_ids(self.request.user, self.request.auth)
        if not Project.objects.filter(id=project_id, id__in=allowed).exists():
            return ProjectInvite.objects.none()
        return ProjectInvite.objects.filter(project_id=project_id).select_related("invited_by")

    def _project(self):
        return get_object_or_404(
            Project.objects.filter(id__in=_user_project_ids(self.request.user, self.request.auth)),
            id=self.kwargs["project_id"],
        )

    def list(self, request, *args, **kwargs):
        self._project()
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        from overbae.auth import clerk_enabled
        from overbae.services.plan_limits import require_seat_for_invite
        from overbae.services.project_invites import create_clerk_invitation

        project = self._project()
        serializer = ProjectInviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise drf_serializers.ValidationError(
                {
                    "detail": "This email already has an account. Add them as a member instead.",
                    "code": "user_exists",
                }
            )

        existing = ProjectInvite.objects.filter(project=project, email=email).first()
        if existing:
            return Response(ProjectInviteSerializer(existing).data, status=status.HTTP_201_CREATED)

        require_seat_for_invite(request.user, project)
        clerk_invitation_id = ""
        if clerk_enabled():
            try:
                clerk_invitation_id = create_clerk_invitation(email)
            except Exception as exc:
                logger.exception("Clerk invitation create failed for %s", email)
                raise APIException("Failed to send the invitation email. Try again.") from exc
        invite = ProjectInvite.objects.create(
            project=project,
            email=email,
            invited_by=request.user,
            clerk_invitation_id=clerk_invitation_id,
        )
        return Response(ProjectInviteSerializer(invite).data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        from overbae.services.project_invites import revoke_clerk_invitation

        revoke_clerk_invitation(instance.clerk_invitation_id)
        instance.delete()


# Mounted at ``/api/traces/`` but backed entirely by the ``Span`` table — no Trace row.


@extend_schema_view(
    list=extend_schema(
        summary="List traces (root spans)",
        description=(
            "Returns one row per trace — the root span (``parent_span_id IS NULL``) of each trace the caller can see. "
        ),
        responses={200: RootSpanListSerializer(many=True)},
    ),
    retrieve=extend_schema(
        summary="Get a trace and all its spans",
        description=(
            "Looked up by ``trace_id`` (the OTel 32-char hex), not by row id. "
            "Returns the root span plus every span sharing that ``trace_id``."
        ),
        parameters=[
            OpenApiParameter(
                name="trace_id",
                location=OpenApiParameter.PATH,
                required=True,
                type=str,
                description="OTel trace id (32 hex chars).",
            ),
        ],
        responses={200: TraceDetailSerializer},
    ),
)
class SpanViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """The single trace-data viewset — one Span table, two read shapes."""

    filterset_class = SpanFilter
    search_fields = ["name", "service_name", "trace_id", "span_id"]
    ordering_fields = [
        "start_time_ns",
        "end_time_ns",
        "duration_ns",
        "received_at",
        "service_name",
        "name",
        "status_code",
        "status_message",
        "trace_id",
        "span_type",
        "capability",
        "capability__name",
        # Annotated when requested — see ``annotate_spans_for_ordering``.
        "total_tokens",
        "total_cost",
        "model",
        "trace_scores",
    ]
    # ``retrieve`` keys off ``trace_id`` (URL says /traces/{trace_id}/) — the
    # row's UUID PK is never user-visible.
    lookup_field = "trace_id"
    lookup_url_kwarg = "trace_id"
    lookup_value_regex = "[0-9a-f]{1,64}"

    def get_serializer_class(self):
        if self.action == "list":
            return RootSpanListSerializer
        if self.action == "retrieve":
            return TraceDetailSerializer
        return SpanSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Span.objects.none()
        project_ids = _user_project_ids(self.request.user)
        queryset = Span.objects.filter(project_id__in=project_ids).select_related("capability")
        if self.request.query_params.get("all_spans"):
            return queryset  # if value exists, let filterset handle it
        if self.kwargs.get("trace_id"):
            return queryset
        # One row per trace, streaming in as spans arrive — never gated on the
        # root, which exports last (or not at all for a killed run).
        return Span.trace_heads(queryset, project_ids)

    def filter_queryset(self, queryset):
        # Usage/score columns are annotated only when ordered — OrderingFilter
        # must see the annotations, so they land before the filter backends run.
        ordering = self.request.query_params.get("ordering") or ""
        all_spans = str(self.request.query_params.get("all_spans", "")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        queryset = annotate_spans_for_ordering(queryset, ordering, all_spans=all_spans)
        for backend in list(self.filter_backends):
            queryset = backend().filter_queryset(self.request, queryset, self)
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        trace_usage = getattr(self, "_trace_usage", None)
        if trace_usage is not None:
            context["trace_usage"] = trace_usage
        eligible_capabilities = getattr(self, "_eligible_scoring_capabilities", None)
        if eligible_capabilities is not None:
            context["eligible_scoring_capabilities"] = eligible_capabilities
        finished_passes = getattr(self, "_traces_with_finished_pass", None)
        if finished_passes is not None:
            context["traces_with_finished_pass"] = finished_passes
        return context

    def list(self, request, *args, **kwargs):
        # Token/cost live on child LLM spans: per-trace totals for this page in one query.
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else list(queryset)
        # ``all_spans`` rows keep their own usage; the root-span list aggregates the trace.
        # Parsed as a boolean: the client sends the string "false", which is truthy.
        all_spans = str(request.query_params.get("all_spans", "")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if not all_spans:
            project_ids = _user_project_ids(request.user)
            self._trace_usage = trace_usage_totals(project_ids, [span.trace_id for span in rows])
            self._eligible_scoring_capabilities = eligible_scoring_capabilities(
                project_ids, [span.capability_id for span in rows if span.capability_id]
            )
            self._traces_with_finished_pass = traces_with_finished_scoring_pass(
                project_ids, [span.trace_id for span in rows]
            )
        serializer = self.get_serializer(rows, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        trace_id = kwargs[self.lookup_url_kwarg]
        spans_qs = self.get_queryset().filter(trace_id=trace_id).order_by("start_time_ns")
        spans = list(spans_qs)
        if not spans:
            return Response(
                {"detail": f"Trace {trace_id} not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Any span whose parent isn't in the visible set is treated as a root —
        # tolerates broken parent links across services / partial sampling.
        local_ids = {s.span_id for s in spans}
        root = next(
            (s for s in spans if s.parent_span_id is None or s.parent_span_id not in local_ids),
            None,
        )
        project_ids = _user_project_ids(request.user)
        usage = trace_usage_totals(project_ids, [trace_id]).get(trace_id) or {
            "total_tokens": None,
            "total_cost": None,
            "cache_read_tokens": None,
            "model": None,
        }
        payload = {
            "trace_id": trace_id,
            "root": SpanSerializer(root).data if root else None,
            "span_count": len(spans),
            # Lifecycle keys off a STRICT root — the missing-parent tolerance
            # above must not make a killed run read completed.
            "trace_status": trace_status(
                has_root=any(s.parent_span_id is None for s in spans),
                last_received_at=max((s.received_at for s in spans if s.received_at), default=None),
            ),
            "usage": usage,
            "spans": SpanSerializer(spans, many=True).data,
        }
        return Response(payload)

    @extend_schema(
        summary="List distinct service names visible to the caller",
        responses={200: drf_serializers.ListSerializer(child=drf_serializers.CharField())},
    )
    @action(detail=False, methods=["get"], url_path="services")
    def services(self, request):
        names = (
            self.get_queryset()
            .exclude(service_name="")
            .values_list("service_name", flat=True)
            .distinct()
            .order_by("service_name")
        )
        return Response(list(names))

    @extend_schema(
        summary="List distinct models reported by spans visible to the caller",
        parameters=[
            OpenApiParameter("project", OpenApiTypes.UUID, description="Scope to one project.")
        ],
        # Raw schema, not a ListSerializer: spectacular wraps the latter in the
        # viewset's pagination envelope, which this action does not return.
        responses={200: {"type": "array", "items": {"type": "string"}}},
    )
    @action(detail=False, methods=["get"], url_path="models")
    def models(self, request):
        # Models live on child LLM spans, so search every visible span, with the same
        # expression ``?model=`` filters on — option list and filter can never disagree.
        spans = self.filter_queryset(
            Span.objects.filter(project_id__in=_user_project_ids(request.user))
        )
        model_ids = (
            spans.annotate(_model=RawSQL(llm_model_sql(), []))
            .exclude(_model=None)
            .exclude(_model="")
            .values_list("_model", flat=True)
            .distinct()
            .order_by("_model")
        )
        return Response(list(model_ids))


@extend_schema_view(
    list=extend_schema(
        summary="List sessions",
        description=(
            "One row per session — traces grouped by the ``conversation.id`` span "
            "attribute. Aggregates (trace/span counts, activity window, tokens, cost) "
            "are computed across every span of the session."
        ),
        responses={200: SessionSerializer(many=True)},
    ),
    retrieve=extend_schema(
        summary="Get a session",
        responses={200: SessionSerializer},
    ),
)
class SessionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Read-only sessions API — list with aggregates + detail."""

    serializer_class = SessionSerializer
    filterset_class = SessionFilter
    search_fields = ["external_id", "name"]
    ordering_fields = [
        "created_at",
        "first_span_ns",
        "last_span_ns",
        "trace_count",
        "span_count",
        "name",
        "external_id",
        "capability",
        "capability__name",
        "session_score",
        # Annotated when requested — see ``annotate_sessions_for_ordering``.
        "total_tokens",
        "total_cost",
        "model",
        "timespan",
    ]
    ordering = ["-created_at"]
    lookup_field = "id"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Conversation.objects.none()
        return (
            Conversation.objects.filter(project_id__in=_user_project_ids(self.request.user))
            .select_related("capability")
            .annotate(
                trace_count=Count("spans__trace_id", distinct=True),
                span_count=Count("spans"),
                first_span_ns=Min("spans__start_time_ns"),
                last_span_ns=Max("spans__end_time_ns"),
                # The ledger fold writes the same session score to every
                # execution of the conversation; the newest row is current.
                session_score=Subquery(
                    TaskExecution.objects.filter(
                        project_id=OuterRef("project_id"),
                        conversation_id=OuterRef("external_id"),
                    )
                    # A blank external_id must not join the executions that
                    # simply have no conversation.
                    .exclude(conversation_id="")
                    .order_by("-started_at")
                    .values("session_score")[:1]
                ),
            )
        )

    def filter_queryset(self, queryset):
        ordering = self.request.query_params.get("ordering") or ""
        queryset = annotate_sessions_for_ordering(queryset, ordering)
        for backend in list(self.filter_backends):
            queryset = backend().filter_queryset(self.request, queryset, self)
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        session_usage = getattr(self, "_session_usage", None)
        if session_usage is not None:
            context["session_usage"] = session_usage
        return context

    def list(self, request, *args, **kwargs):
        # Token/cost live on span attributes — compute the page's totals in ONE
        # query (no N+1) and hand them to the serializer, like the traces list.
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else list(queryset)
        self._session_usage = conversation_usage_totals(
            _user_project_ids(request.user), [c.id for c in rows]
        )
        serializer = self.get_serializer(rows, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        self._session_usage = conversation_usage_totals(
            _user_project_ids(request.user), [instance.id]
        )
        return Response(self.get_serializer(instance).data)


# The facet actions below return a bare array, so spectacular does not read them
# as list views and skips the filter backends' parameters — without declaring
# them the generated client cannot narrow a facet at all. One shared list: both
# facets run the same filterset, so both accept the same narrowing.
_FINETUNING_FACET_PARAMETERS = [
    OpenApiParameter("project", OpenApiTypes.UUID, description="Scope to one project."),
    OpenApiParameter("capability", OpenApiTypes.UUID, description="Scope to one capability."),
    OpenApiParameter("dataset", OpenApiTypes.UUID, description="Scope to one dataset."),
    OpenApiParameter("base_model", description="Scope to one base model."),
    OpenApiParameter("status", description="Scope to jobs with this status."),
    OpenApiParameter("search", description="Case-insensitive filter over name / use case / model."),
]


@extend_schema_view(
    list=extend_schema(summary="List finetuning jobs"),
    retrieve=extend_schema(summary="Get finetuning job (with events)"),
    create=extend_schema(summary="Schedule a finetuning job"),
    partial_update=extend_schema(summary="Update finetuning job metadata"),
    destroy=extend_schema(summary="Delete finetuning job"),
)
class FinetuningJobViewSet(viewsets.ModelViewSet):
    @extend_schema(
        request=NativeEvaluationRequestSerializer, responses={202: NativeEvaluationSerializer}
    )
    @action(detail=True, methods=["post"], url_path="native-evaluation")
    def native_evaluation(self, request, id=None):
        from overbae.api.credit_gate import require_credits

        require_credits(request.user)
        job = self.get_object()
        body = NativeEvaluationRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        cells = Cell.objects.select_related("dataset").filter(dataset__project_id=job.project_id)
        calibration = get_object_or_404(cells, pk=body.validated_data["calibration_cell"])
        final = get_object_or_404(cells, pk=body.validated_data["final_cell"])
        try:
            plan = native_evaluation_service.schedule(
                job, calibration_cell=calibration, final_cell=final
            )
        except (ValueError, DatasetError) as exc:
            raise drf_serializers.ValidationError(str(exc)) from exc
        return Response(NativeEvaluationSerializer(plan).data, status=202)

    queryset = FinetuningJob.objects.all()
    filterset_class = FinetuningJobFilter
    search_fields = ["name", "use_case", "base_model"]
    ordering_fields = ["created_at", "updated_at", "status"]
    lookup_field = "id"
    http_method_names = ["get", "post", "patch", "delete"]

    def get_serializer_class(self):
        if self.action == "list":
            return FinetuningJobListSerializer
        return FinetuningJobSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return FinetuningJob.objects.none()
        return FinetuningJob.objects.filter(
            project_id__in=_user_project_ids(self.request.user)
        ).select_related(
            "project",
            "capability",
            "dataset",
            "cell__dataset",
            "eval_cell__dataset",
            "eval_set",
            "native_evaluation",
            "validation_cell__dataset",
            "triggered_by",
            "deployed_model",
        )

    def perform_create(self, serializer):
        from overbae.api.credit_gate import require_credits
        from overbae.services.plan_limits import require_plan_quota
        from overbae.tasks.finetuning import run_finetuning

        def admit_new_training():
            require_credits(self.request.user)
            require_plan_quota(self.request.user, "training_jobs")

        serializer.context["admit_new_training"] = admit_new_training
        job = serializer.save(triggered_by=self.request.user)
        if getattr(job, "launch_reused", False):
            return
        try:
            result = run_finetuning.apply_async(kwargs={"job_id": str(job.id)})
        except Exception:  # noqa: BLE001 — broker hiccup shouldn't 500 post-create
            logger.exception("finetuning job %s dispatch failed", job.id)
            FinetuningJob.objects.filter(pk=job.pk).update(
                status=FinetuningJob.Status.FAILED,
                error_message="Could not queue the job. Retry it.",
            )
            return
        FinetuningJob.objects.filter(pk=job.pk).update(celery_task_id=result.id)

    @extend_schema(
        summary="List training runs — whole runs per page, newest first",
        description=(
            "The training history lists *runs*, not jobs: the wizard launches one "
            "job per experiment and stamps them with a shared `group_id`. Paging "
            "jobs would straddle a run across a page boundary and silently corrupt "
            "every per-run aggregate the client derives (status, progress, cost, "
            "duration, and the single-job test that picks the row's navigation "
            "target), so this pages over distinct runs and returns each one whole.\n\n"
            "`count` is the number of matching **runs**. `results` carries one "
            "entry per run — `{run_id, group_id, jobs}` — where `run_id` is the "
            "`group_id` when there is one and the lone job's id otherwise, and "
            "`jobs` is every job of that run so client-side aggregation is always "
            "complete. Runs are ordered by their most recent job; `?ordering=` is "
            "accepted for compatibility but does not reorder runs.\n\n"
            "Every filter of the jobs list applies (`project`, `capability`, `dataset`, "
            "`base_model`, `status`, `provider`, `group_id`, `search`)."
        ),
        responses={200: FinetuningJobRunSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="runs")
    def runs(self, request):
        """Runs (jobs grouped by ``group_id``), paginated over distinct runs.

        ``?status=`` is a run-level predicate here: it selects runs having ANY
        job with that status, and the run is still returned whole. That diverges
        from the client's status badge, which collapses a run to ONE status by
        precedence — a run of 3 succeeded jobs and 1 failed one reads "failed" on
        the badge yet matches both ``status=failed`` and ``status=succeeded``.
        Accepted: a filter that hid jobs would break the aggregates the whole
        endpoint exists to keep intact.
        """
        base = self.get_queryset()
        # Distinct run keys, most-recently-active first. ``order_by()`` first:
        # the model's default ``-created_at`` would otherwise join the GROUP BY
        # and split every run into one row per job.
        runs = (
            self.filter_queryset(base)
            .order_by()
            .annotate(run_key=Coalesce("group_id", "id"))
            .values("run_key")
            .annotate(latest_at=Max("created_at"))
            .order_by("-latest_at")
        )
        page = self.paginate_queryset(runs)
        run_keys = [row["run_key"] for row in (page if page is not None else runs)]
        # Refetched from the project-scoped base, NOT the filtered queryset, so a
        # row-level filter (``status``) can't drop members of a matching run. The
        # explicit Q pair keeps the ("group_id", "-created_at") index usable,
        # which a filter on the COALESCE annotation would not.
        jobs_by_run: dict = defaultdict(list)
        for job in base.filter(
            Q(group_id__in=run_keys) | Q(group_id__isnull=True, id__in=run_keys)
        ).order_by("-created_at"):
            jobs_by_run[job.group_id or job.id].append(job)
        data = FinetuningJobRunSerializer(
            [
                {"run_id": key, "group_id": jobs[0].group_id, "jobs": jobs}
                # A run deleted between the two queries drops off the page rather
                # than 500-ing it; every group's members share one ``group_id``,
                # so the first job is a faithful source for the run's own.
                for key in run_keys
                if (jobs := jobs_by_run.get(key))
            ],
            many=True,
        ).data
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    @extend_schema(
        summary="List distinct datasets used by finetuning jobs visible to the caller",
        description=(
            "Option list for the training-history dataset filter: one entry per "
            "dataset that has at least one job. Read off the *jobs*, not the "
            "project's dataset list, so the option list and the `?dataset=` filter "
            "can never disagree about which values yield rows — deriving options "
            "from the loaded page instead both misses legitimate values and hides "
            "the control entirely once the first page happens to be uniform."
        ),
        parameters=_FINETUNING_FACET_PARAMETERS,
        # Raw schema, not a serializer with many=True: spectacular wraps the
        # latter in the viewset's pagination envelope, which this does not return.
        responses={
            200: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "name": {"type": "string"},
                    },
                    "required": ["id", "name"],
                },
            }
        },
    )
    @action(detail=False, methods=["get"], url_path="datasets")
    def datasets(self, request):
        # Unpaginated by construction: DISTINCT over datasets, not jobs, so the
        # response is bounded by the project's dataset count.
        pairs = (
            self.filter_queryset(self.get_queryset())
            .values_list("dataset_id", "dataset__name")
            .distinct()
            .order_by("dataset__name")
        )
        return Response([{"id": str(dataset_id), "name": name} for dataset_id, name in pairs])

    @extend_schema(
        summary="List distinct base models used by finetuning jobs visible to the caller",
        description=(
            "Option list for the training-history model filter, read off the jobs "
            "for the same reason as the dataset facet: an option that yields no "
            "rows — or a row whose value is missing from the options — is a bug."
        ),
        parameters=_FINETUNING_FACET_PARAMETERS,
        responses={200: {"type": "array", "items": {"type": "string"}}},
    )
    @action(detail=False, methods=["get"], url_path="base-models")
    def base_models(self, request):
        base_model_ids = (
            self.filter_queryset(self.get_queryset())
            .exclude(base_model="")
            .values_list("base_model", flat=True)
            .distinct()
            .order_by("base_model")
        )
        return Response(list(base_model_ids))

    @extend_schema(
        parameters=[TrainingMonitoringQuerySerializer], responses=TrainingMonitoringSerializer
    )
    @action(detail=True, methods=["get"], url_path="monitoring")
    def monitoring(self, request, id=None):
        query = TrainingMonitoringQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        detail = training_monitoring.snapshot(
            self.get_object(),
            offset=query.validated_data["offset"],
            limit=query.validated_data["limit"],
        )
        return Response(TrainingMonitoringSerializer(detail).data)

    @extend_schema(
        parameters=[TrainingMonitoringQuerySerializer], responses=TrainingEvidenceSerializer
    )
    @action(detail=True, methods=["get"], url_path="monitoring-evidence")
    def monitoring_evidence(self, request, id=None):
        query = TrainingMonitoringQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        if sum(key in data for key in ("check", "probe", "field")) != 1:
            raise drf_serializers.ValidationError(
                {"check": "Select a training check, frozen probe or retained receipt field"}
            )
        try:
            detail = (
                training_monitoring.field_page(
                    self.get_object(), data["field"], offset=data["offset"], limit=data["limit"]
                )
                if "field" in data
                else training_monitoring.probe_rows(
                    self.get_object(), data["probe"], offset=data["offset"], limit=data["limit"]
                )
                if "probe" in data
                else training_monitoring.examples(
                    self.get_object(), data["check"], offset=data["offset"], limit=data["limit"]
                )
            )
        except (ValueError, training_monitoring.TrainingValidationRun.DoesNotExist) as exc:
            raise drf_serializers.ValidationError(
                "The requested training evidence is unavailable"
            ) from exc
        return Response(TrainingEvidenceSerializer(detail).data)

    @extend_schema(
        summary="Cancel a finetuning job",
        request=None,
        responses={200: FinetuningJobSerializer},
    )
    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, id=None):
        job = cancel_training(self.get_object())
        return Response(FinetuningJobSerializer(job).data)

    @extend_schema(
        summary="Retry a failed finetuning job",
        request=None,
        responses={200: FinetuningJobSerializer},
    )
    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, id=None):
        from overbae.api.credit_gate import require_credits
        from overbae.services.finetuning_eval import reset_before_evals_for_retry
        from overbae.tasks.finetuning import run_finetuning

        job = self.get_object()
        if job.status not in {
            FinetuningJob.Status.FAILED,
            FinetuningJob.Status.CANCELLED,
        }:
            raise drf_serializers.ValidationError("Only failed/cancelled jobs can be retried.")
        require_credits(request.user)
        try:
            retry_training_preparation(job)
            reset_before_evals_for_retry(job)
        except InputValidationError as exc:
            raise drf_serializers.ValidationError(exc.detail) from exc
        except (ValueError, RuntimeError, OSError) as exc:
            logger.exception("Could not prepare training job %s for retry", job.pk)
            raise drf_serializers.ValidationError("Could not retry training. Try again.") from exc
        FinetuningJob.objects.filter(pk=job.pk).update(
            status=FinetuningJob.Status.QUEUED,
            error_message="",
        )
        try:
            result = run_finetuning.apply_async(kwargs={"job_id": str(job.id)})
        except Exception:  # noqa: BLE001
            logger.exception("finetuning job %s retry dispatch failed", job.id)
            FinetuningJob.objects.filter(pk=job.pk).update(
                status=FinetuningJob.Status.FAILED,
                error_message="Could not queue the retry. Try again.",
            )
            return Response(
                {"detail": "Could not queue the retry. Try again.", "code": "dispatch_failed"},
                status=503,
            )
        FinetuningJob.objects.filter(pk=job.pk).update(celery_task_id=result.id)
        job.refresh_from_db()
        return Response(FinetuningJobSerializer(job).data)

    @extend_schema(
        summary="Copy-paste prompt that points the capability's code at its model alias",
        parameters=[
            OpenApiParameter(
                "pin",
                bool,
                OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Write this deployment's concrete model id instead of the capability alias."
                ),
            ),
        ],
        responses={200: ModelSwapPromptSerializer},
    )
    @action(detail=True, methods=["get"], url_path="model-swap-prompt")
    def model_swap_prompt(self, request, id=None):
        from overbae.services.model_swap_prompt import model_swap_prompt_for_job

        query = ModelSwapPromptRequestSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload, error = model_swap_prompt_for_job(
            self.get_object(),
            pin=query.validated_data["pin"],
            base_url=request.build_absolute_uri("/api/v1"),
        )
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ModelSwapPromptSerializer(payload).data)

    @extend_schema(
        summary="List events for a finetuning job",
        responses={200: FinetuningJobEventSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="events")
    def events(self, request, id=None):
        job = self.get_object()
        events = job.events.all()
        return Response(FinetuningJobEventSerializer(events, many=True).data)

    @extend_schema(
        summary="Loss / metric curve data for a finetuning job",
        responses={200: OpenApiTypes.OBJECT},
    )
    @action(detail=True, methods=["get"], url_path="loss-curves")
    def loss_curves(self, request, id=None):
        """Return step-indexed train/eval loss points for the training monitor.

        Preference order:
        1. ``job.progress.metrics.loss`` (live poll snapshot)
        2. ``job.result.epoch_losses`` (completion snapshot)
        3. ``FinetuningJobEvent`` progress events (legacy)
        """

        job = self.get_object()
        steps, train_loss, eval_loss = [], [], []
        learning_rate, grad_norm = [], []
        checkpoints = []

        progress = live_progress_for_job(job)
        metrics = (progress.get("metrics") or {}) if isinstance(progress, dict) else {}
        loss_points = metrics.get("loss") or []
        if loss_points:
            for p in loss_points:
                steps.append(p.get("step"))
                train_loss.append(p.get("train_loss"))
                eval_loss.append(p.get("eval_loss"))
        else:
            result = job.result if isinstance(job.result, dict) else {}
            for row in result.get("epoch_losses") or []:
                steps.append(row.get("epoch") if "epoch" in row else row.get("step"))
                train_loss.append(row.get("train_loss"))
                eval_loss.append(row.get("valid_loss", row.get("eval_loss")))

        if not steps:
            events = (
                FinetuningJobEvent.objects.filter(job=job, event_type="progress")
                .order_by("created_at")
                .values_list("data", flat=True)
            )
            for d in events:
                if not d or "step" not in d:
                    continue
                steps.append(d.get("step"))
                train_loss.append(d.get("train_loss"))
                eval_loss.append(d.get("eval_loss"))

        for p in metrics.get("learning_rate") or []:
            learning_rate.append({"step": p.get("step"), "value": p.get("value")})
        for p in metrics.get("grad_norm") or []:
            grad_norm.append({"step": p.get("step"), "value": p.get("value")})
        token_accuracy = [
            {"step": p.get("step"), "train": p.get("train"), "eval": p.get("eval")}
            for p in metrics.get("token_accuracy") or []
        ]

        checkpoints = list(progress.get("checkpoints") or [])
        if not checkpoints and isinstance(job.result, dict):
            checkpoints = list(job.result.get("checkpoints") or [])

        from overbae.services.finetuning_eval import serialize_job_evals, sync_eval_scores

        try:
            judge_evals = (
                sync_eval_scores(job)
                if job.eval_dataset_id and job.eval_set_id
                else serialize_job_evals(job)
            )
        except Exception:  # noqa: BLE001 — monitor must still return metrics
            logger.warning("judge_evals sync failed for job %s", job.id, exc_info=True)
            judge_evals = serialize_job_evals(job)

        from overbae.services.finetuning_runner import lifecycle_stage

        # Just-synced judge_evals ride along so an in-flight final eval reads
        # EVALUATION, not COMPLETED.
        stage_progress = {**progress, "judge_evals": judge_evals}
        current_lifecycle = lifecycle_stage(job.status, stage_progress)

        return Response(
            {
                "steps": steps,
                "train_loss": train_loss,
                "eval_loss": eval_loss,
                # Alias for older FE hook that expected epochs/valid_loss.
                "epochs": steps,
                "valid_loss": eval_loss,
                "learning_rate": learning_rate,
                "grad_norm": grad_norm,
                "token_accuracy": token_accuracy,
                "checkpoints": checkpoints,
                "judge_evals": judge_evals,
                "progress": {
                    "trained_steps": progress.get("trained_steps"),
                    "total_steps": progress.get("total_steps"),
                    "percent": progress.get("percent"),
                    "eta_seconds": progress.get("eta_seconds"),
                    "eta_range_seconds": progress.get("eta_range_seconds"),
                    "elapsed_seconds": progress.get("elapsed_seconds"),
                    "estimated_finish": progress.get("estimated_finish"),
                    "tokens_processed": progress.get("tokens_processed"),
                    # The monitor polls this endpoint every 3s, so live histories ride
                    # here rather than the staler jobs-list progress blob.
                    "metrics_history": progress.get("metrics_history") or [],
                    "eval_history": progress.get("eval_history") or [],
                    "activity": progress.get("activity") or [],
                    "train_loss": progress.get("train_loss"),
                    "eval_loss": progress.get("eval_loss"),
                    "token_accuracy": progress.get("token_accuracy"),
                    "eval_token_accuracy": progress.get("eval_token_accuracy"),
                    "current_epoch": progress.get("current_epoch"),
                    "phase": progress.get("phase"),
                    "stage": progress.get("stage"),
                    "preparation": progress.get("preparation"),
                    "diagnostics": progress.get("diagnostics") or {},
                    "download": progress.get("download"),
                    "lifecycle_stage": current_lifecycle,
                    "judge_evals": judge_evals,
                },
            }
        )

    @extend_schema(
        summary="List available fine-tuning models by tier",
        parameters=[
            OpenApiParameter(
                "has_tool_calling",
                bool,
                description=(
                    "When true, only models that support function-calling fine-tuning are returned."
                ),
            ),
            OpenApiParameter(
                "max_context",
                int,
                required=False,
                description=(
                    "Only return models whose context window is at least this many tokens. "
                    "Non-numeric values are ignored. Together only; ignored for Baseten."
                ),
            ),
        ],
        responses={200: FinetuningModelCatalogResponseSerializer},
    )
    @action(detail=False, methods=["get"], url_path="models")
    def models(self, request):
        """Return the curated model catalog organised by tier.

        When ``FINETUNING_BACKEND`` is ``baseten``, the Baseten catalog from
        models.json is returned. Pass ``?has_tool_calling=true`` to receive
        only models that support function-calling fine-tuning. Tiers that
        contain no eligible models are omitted from the response.
        """
        raw = request.query_params.get("has_tool_calling", "").lower()
        has_tool_calling = raw in {"true", "1", "yes"}

        max_context: int | None = None
        raw_ctx = request.query_params.get("max_context", "")
        if raw_ctx.isdigit():
            max_context = int(raw_ctx)

        from overbae.services.finetuning_catalog import fetch_finetuning_model_catalog

        return Response(
            fetch_finetuning_model_catalog(
                has_tool_calling=has_tool_calling,
                max_context=max_context,
            )
        )

    @extend_schema(
        summary="Validate a dataset for fine-tuning",
        request=DatasetValidateRequestSerializer,
        responses={200: DatasetValidationResponseSerializer},
    )
    @action(detail=False, methods=["post"], url_path="validate-dataset")
    def validate_dataset(self, request):
        """Check whether a dataset's datapoints conform to Together AI's format."""
        from overbae.services.finetuning_validator import validate_dataset

        body = DatasetValidateRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        dataset = get_object_or_404(
            Dataset, pk=data["dataset_id"], project_id__in=_user_project_ids(request.user)
        )
        cell = (
            get_object_or_404(dataset.cells, pk=data["cell_id"])
            if data.get("cell_id")
            else dataset.active_cell
        )
        validation = (
            get_object_or_404(
                Dataset, pk=data["validation_dataset_id"], project_id=dataset.project_id
            )
            if data.get("validation_dataset_id")
            else None
        )
        validation_cell = (
            get_object_or_404(validation.cells, pk=data["validation_cell_id"])
            if validation and data.get("validation_cell_id")
            else validation.active_cell
            if validation
            else None
        )
        if data.get("validation_cell_id") and not validation:
            raise drf_serializers.ValidationError(
                {"validation_cell_id": "Select its validation dataset."}
            )
        result = validate_dataset(
            str(dataset.id),
            cell_id=str(cell.id) if cell else None,
            validation_cell_id=str(validation_cell.id) if validation_cell else None,
            validation_enabled=data["validation_enabled"],
            validation_split_ratio=data["validation_split_ratio"],
            validation_dataset_id=str(validation.id) if validation else None,
            split_method=data["split_method"],
        )
        return Response(DatasetValidationResponseSerializer(result.as_dict()).data)

    @extend_schema(
        summary="Rank trainable models for a dataset + capability",
        request=inline_serializer(
            "FinetuningRecommendRequest",
            fields={
                "dataset_id": drf_serializers.UUIDField(),
                "capability_id": drf_serializers.UUIDField(required=False, allow_null=True),
                "eval_dataset_id": drf_serializers.UUIDField(required=False, allow_null=True),
            },
        ),
        responses={200: FinetuningRecommendationResponseSerializer},
    )
    @action(detail=False, methods=["post"], url_path="recommend")
    def recommend(self, request):
        """Rank the trainable catalog for this dataset, with the benchmark evidence
        behind each grade and the reason every excluded model was dropped."""
        from overbae.services.recommendation import get_recommendation

        dataset_id = request.data.get("dataset_id")
        capability_id = request.data.get("capability_id") or None
        if not dataset_id:
            return Response({"detail": "dataset_id is required."}, status=400)

        from overbae.models import Capability

        try:
            dataset = Dataset.objects.get(
                pk=dataset_id,
                project_id__in=_user_project_ids(request.user),
            )
        except (Dataset.DoesNotExist, ValidationError, ValueError):
            return Response({"detail": "Dataset not found."}, status=404)

        if capability_id:
            try:
                Capability.objects.get(pk=capability_id, project_id=dataset.project_id)
            except (Capability.DoesNotExist, ValidationError, ValueError):
                return Response({"detail": "Capability not found."}, status=404)

        eval_dataset_id = request.data.get("eval_dataset_id") or None
        if eval_dataset_id:
            try:
                Dataset.objects.get(
                    pk=eval_dataset_id, project_id=dataset.project_id, intent=Dataset.Intent.EVAL
                )
            except (Dataset.DoesNotExist, ValidationError, ValueError):
                return Response({"detail": "Eval dataset not found."}, status=404)
        try:
            analysis = get_recommendation(
                str(dataset.id), capability_id=capability_id, eval_dataset_id=eval_dataset_id
            )
        except InputValidationError as exc:
            raise drf_serializers.ValidationError({"detail": exc.detail}) from exc
        except (ValueError, RuntimeError, OSError) as exc:
            logger.exception("Could not recommend models for dataset %s", dataset.pk)
            raise drf_serializers.ValidationError(
                {"detail": "Could not recommend models for this dataset. Try again."}
            ) from exc
        return Response(FinetuningRecommendationResponseSerializer(analysis).data)

    @extend_schema(
        summary="Re-estimate fine-tuning cost and duration",
        description=(
            "Uses the dataset's real token stats and published provider pricing "
            "to estimate cost/time for an edited n_epochs / LoRA vs full setting. "
            "Does not create a job."
        ),
        request=FinetuningEstimateRequestSerializer,
        responses={200: FinetuningEstimateResponseSerializer},
    )
    @action(detail=False, methods=["post"], url_path="estimate")
    def estimate(self, request):
        from overbae.services.recommendation import estimate_for_hyperparams

        ser = FinetuningEstimateRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        try:
            selected_dataset = Dataset.objects.get(
                pk=data["dataset_id"],
                project_id__in=_user_project_ids(request.user),
            )
        except (Dataset.DoesNotExist, ValidationError, ValueError):
            return Response({"detail": "Dataset not found."}, status=404)

        try:
            result = estimate_for_hyperparams(
                str(data["dataset_id"]),
                base_model=data["base_model"],
                n_epochs=data["n_epochs"],
                use_lora=data["use_lora"],
                cell=get_object_or_404(selected_dataset.cells, pk=data["cell"])
                if data.get("cell")
                else selected_dataset.active_cell,
                validation_cell=get_object_or_404(
                    Cell.objects.select_related("dataset"),
                    pk=data["validation_cell"],
                    dataset__project=selected_dataset.project,
                )
                if data.get("validation_cell")
                else None,
                validation_enabled=data["validation_enabled"],
                validation_split_ratio=data["validation_split_ratio"],
                split_method=data["split_method"],
                hyperparameters=data["hyperparameters"],
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)

        return Response(FinetuningEstimateResponseSerializer(result).data)

    @extend_schema(
        summary="Recommended hyperparameters for one model",
        description=(
            "Dataset-size-aware defaults (batch, epochs, learning rate, LoRA "
            "config) plus per-knob provenance for an arbitrary catalog model — "
            "the same derivation as the recommendation rows, no LLM call. "
            "Serves the wizard's 'Add model' and model-switch flows so the "
            "client never fabricates hyperparameter defaults."
        ),
        request=FinetuningModelDefaultsRequestSerializer,
        responses={200: FinetuningExperimentSerializer},
    )
    @action(detail=False, methods=["post"], url_path="model-defaults")
    def model_defaults(self, request):
        from overbae.services.recommendation import recommend_hyperparams_for_model

        ser = FinetuningModelDefaultsRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        try:
            Dataset.objects.get(
                pk=data["dataset_id"],
                project_id__in=_user_project_ids(request.user),
            )
        except (Dataset.DoesNotExist, ValidationError, ValueError):
            return Response({"detail": "Dataset not found."}, status=404)

        try:
            result = recommend_hyperparams_for_model(
                str(data["dataset_id"]), base_model=data["base_model"]
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)

        return Response(FinetuningExperimentSerializer(result).data)

    @extend_schema(
        summary="Count train/eval overlapping datapoints",
        description=(
            "Number of training datapoints whose source trace also appears in "
            "the chosen eval dataset. These rows are excluded from training "
            "when the job links that eval dataset."
        ),
        parameters=[
            OpenApiParameter(name="dataset", location=OpenApiParameter.QUERY, type=str),
            OpenApiParameter(name="eval_dataset", location=OpenApiParameter.QUERY, type=str),
        ],
        responses={200: DatasetOverlapResponseSerializer},
    )
    @action(detail=False, methods=["get"], url_path="dataset-overlap")
    def dataset_overlap(self, request):
        from overbae.services.datasets import rows as row_store

        train_id = request.query_params.get("dataset")
        eval_id = request.query_params.get("eval_dataset")
        if not train_id or not eval_id:
            return Response({"detail": "dataset and eval_dataset are required."}, status=400)
        project_ids = _user_project_ids(request.user)
        try:
            train = Dataset.objects.get(pk=train_id, project_id__in=project_ids)
            eval_ds = Dataset.objects.get(pk=eval_id, project_id__in=project_ids)
        except (Dataset.DoesNotExist, ValidationError, ValueError):
            return Response({"detail": "Dataset not found."}, status=404)
        train_v, eval_v = train.active_cell, eval_ds.active_cell
        if train_v is None or eval_v is None:
            return Response(
                {
                    "overlap_count": 0,
                    "train_total": train_v.rows if train_v else 0,
                    "basis": "trace_id",
                }
            )
        return Response(
            DatasetOverlapResponseSerializer(row_store.contamination(train_v, eval_v)).data
        )


@extend_schema_view(
    create=extend_schema(summary="Submit user feedback"),
)
class FeedbackViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = FeedbackCreateSerializer
    queryset = Feedback.objects.none()

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(user=user)


@extend_schema_view(
    list=extend_schema(
        summary="List connector credentials for the current user's projects",
        parameters=[
            OpenApiParameter(
                name="project",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter by project UUID.",
                required=False,
            )
        ],
    ),
    create=extend_schema(summary="Add a new connector credential"),
    retrieve=extend_schema(summary="Retrieve a connector credential"),
    update=extend_schema(summary="Update a connector credential"),
    partial_update=extend_schema(summary="Partially update a connector credential"),
    destroy=extend_schema(
        summary="Disconnect a connector",
        description=(
            "Stops syncing and hides the connection. Imported traces are kept. "
            "Langfuse connections without a finished setup are removed entirely. "
            "Reconnecting with the same API key reactivates the same connection "
            "so span IDs stay stable."
        ),
    ),
)
class ConnectorCredentialViewSet(viewsets.ModelViewSet):
    serializer_class = ConnectorCredentialSerializer
    lookup_field = "id"
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ConnectorCredential.objects.none()
        project_ids = _user_project_ids(self.request.user, self.request.auth)
        qs = ConnectorCredential.objects.filter(project_id__in=project_ids)
        project_id = self.request.query_params.get("project")
        if project_id:
            qs = qs.filter(project_id=project_id)
        # Disconnected integrations stay in the DB (stable span IDs) but are
        # hidden unless explicitly requested.
        if (
            getattr(self, "action", None) == "list"
            and self.request.query_params.get("include_inactive") != "true"
        ):
            qs = qs.filter(is_active=True)
        # Langfuse setup creates a credential on verify, before config exists.
        # Hide those drafts from the integrations list until the wizard finishes
        # (saves a ConnectorSyncConfig). Retrieve/update/sync by id still work.
        from django.db.models import Exists, OuterRef

        from overbae.models import ConnectorSyncConfig

        if (
            getattr(self, "action", None) == "list"
            and self.request.query_params.get("include_drafts") != "true"
        ):
            has_config = ConnectorSyncConfig.objects.filter(credential_id=OuterRef("pk"))
            # Hide unfinished wizard drafts (no sync config yet) for any provider.
            qs = qs.exclude(~Exists(has_config))
        return qs

    def perform_create(self, serializer):
        # Verify keys on create via the adapter, but do NOT auto-kick a backfill —
        # the setup wizard decides when to sync (after config is saved).
        data = serializer.validated_data
        connector_type = data.get("connector_type")
        from types import SimpleNamespace

        from overbae.services.connectors import get_adapter, registered_sources

        result = None
        if connector_type in registered_sources():
            tmp = SimpleNamespace(
                pk=None,
                api_key=data.get("api_key", ""),
                api_secret=data.get("api_secret", ""),
                base_url=data.get("base_url", ""),
                api_version="unknown",
                connector_type=connector_type,
            )
            try:
                result = get_adapter(tmp).verify()
            except Exception as exc:
                raise drf_serializers.ValidationError({"detail": str(exc)}) from exc
            if not result.ok:
                raise drf_serializers.ValidationError({"detail": result.detail or "Verify failed"})
        credential = serializer.save()
        if result is not None:
            from django.utils import timezone

            credential.verified_at = timezone.now()
            if result.api_version:
                credential.api_version = result.api_version
            credential.save(update_fields=["verified_at", "api_version", "updated_at"])

    def perform_destroy(self, instance):
        """Disconnect: soft-deactivate finished integrations; hard-delete drafts."""
        if instance.active_config() is None:
            instance.delete()
            return
        instance.is_active = False
        instance.auto_sync_enabled = False
        instance.next_poll_at = None
        instance.sync_error = ""
        instance.save(
            update_fields=[
                "is_active",
                "auto_sync_enabled",
                "next_poll_at",
                "sync_error",
                "updated_at",
            ]
        )

    def perform_update(self, serializer):
        instance = serializer.instance
        data = serializer.validated_data
        rotated = bool(data.get("api_key") or data.get("api_secret"))
        from overbae.services.connectors import get_adapter, registered_sources

        if rotated and instance.connector_type in registered_sources():
            from copy import copy

            probe = copy(instance)
            if data.get("api_key"):
                probe.api_key = data["api_key"]
            if data.get("api_secret"):
                probe.api_secret = data["api_secret"]
            if "base_url" in data:
                probe.base_url = data["base_url"]
            result = get_adapter(probe).verify()
            if not result.ok:
                raise drf_serializers.ValidationError({"detail": result.detail or "Verify failed"})
        credential = serializer.save()
        changed = set(serializer.validated_data.keys())
        ConnectorCredential.objects.filter(pk=credential.pk).update(
            sync_error="", sync_retry_count=0
        )
        if credential.auto_sync_enabled and ({"api_key", "api_secret", "base_url"} & changed):
            from overbae.services.connectors.sync import enqueue_connector_sync

            enqueue_connector_sync(credential)

    @extend_schema(
        summary="Trigger a background sync for this connector now",
        request=None,
        responses={202: {"type": "object", "properties": {"status": {"type": "string"}}}},
    )
    @action(detail=True, methods=["post"], url_path="sync")
    def sync(self, request, id=None):
        from overbae.services.connectors.sync import enqueue_connector_sync

        enqueue_connector_sync(self.get_object())
        return Response({"status": "queued"}, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        summary="Verify connector credentials and return capabilities",
        request=None,
        responses={200: ConnectorVerifyResponseSerializer, 502: ConnectorVerifyResponseSerializer},
    )
    @action(detail=True, methods=["post"], url_path="verify")
    def verify(self, request, id=None):
        from overbae.services.connectors import get_adapter

        credential = self.get_object()
        try:
            result = get_adapter(credential).verify()
        except ValueError as exc:
            return Response({"ok": False, "detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not result.ok:
            return Response(
                {"ok": False, "detail": result.detail},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        from django.utils import timezone

        caps = result.capabilities
        ConnectorCredential.objects.filter(pk=credential.pk).update(
            api_version=result.api_version or credential.api_version,
            verified_at=timezone.now(),
            sync_error="",
        )
        return Response(
            {
                "ok": True,
                "api_version": result.api_version,
                "projects": [{"id": p.id, "name": p.name} for p in result.projects],
                "capabilities": {
                    "exact_count": caps.exact_count if caps else False,
                    "capability_sources": list(caps.capability_sources) if caps else [],
                    "needs_source_project": caps.needs_source_project if caps else True,
                    "retention_note": (caps.retention_note if caps else "") or "",
                },
            }
        )

    @extend_schema(
        summary="Create a new sync config version",
        request=ConnectorSyncConfigWriteSerializer,
        responses={201: ConnectorSyncConfigCreateResponseSerializer},
    )
    @action(detail=True, methods=["post"], url_path="config")
    def config(self, request, id=None):
        credential = self.get_object()
        from overbae.services.connectors.feedforward import save_sync_config

        write = ConnectorSyncConfigWriteSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        data = write.validated_data

        target_project = None
        target_project_id = data.get("target_project_id")
        if target_project_id:
            target_project = Project.objects.filter(
                id=target_project_id, id__in=_user_project_ids(request.user)
            ).first()
            if not target_project:
                return Response(
                    {"detail": "target_project_id is not accessible."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        config_kwargs = {
            "source_project_id": data.get("source_project_id") or "",
            "target_project": target_project,
            "lookback_days": data.get("lookback_days"),
        }
        if "backfill_from" in data:
            config_kwargs["backfill_from"] = data["backfill_from"]
        if "backfill_to" in data:
            config_kwargs["backfill_to"] = data["backfill_to"]
        config = save_sync_config(
            credential,
            **config_kwargs,
        )
        from overbae.services.connectors.sync import reset_connector_import

        reset_connector_import(credential)
        return Response(
            {
                "version": config.version,
                "effective_from": config.effective_from.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Preview how many traces match the import range",
        request=ConnectorPreviewRequestSerializer,
        responses={200: ConnectorPreviewResponseSerializer},
    )
    @action(detail=True, methods=["post"], url_path="preview")
    def preview(self, request, id=None):
        from overbae.services.connectors import get_adapter

        credential = self.get_object()
        preview = ConnectorPreviewRequestSerializer(data=request.data)
        preview.is_valid(raise_exception=True)
        data = preview.validated_data
        lookback = data.get("lookback_days")
        try:
            count = get_adapter(credential).count(
                lookback_days=int(lookback) if lookback is not None else None,
                source_project_id=data["source_project_id"],
                window_from=data.get("backfill_from"),
                window_to=data.get("backfill_to"),
            )
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"count": count})

    @extend_schema(
        summary="Discover capability-identity candidates from the provider",
        parameters=[
            OpenApiParameter(
                "lookback_days",
                OpenApiTypes.INT,
                description="How far back to sample for discovery (default 30).",
            ),
            OpenApiParameter(
                "source",
                OpenApiTypes.STR,
                description="Optional identity source filter.",
            ),
            OpenApiParameter(
                "key",
                OpenApiTypes.STR,
                description="Optional metadata/tag key when source needs one.",
            ),
            OpenApiParameter(
                "source_project_id",
                OpenApiTypes.STR,
                description=(
                    "Provider project to sample. Required mid-wizard for a connector "
                    "with needs_source_project, whose sync config is not written yet."
                ),
            ),
        ],
        responses={200: ConnectorDiscoverCapabilitiesResponseSerializer},
    )
    @action(detail=True, methods=["get"], url_path="discover-capabilities")
    def discover_capabilities_action(self, request, id=None):
        from overbae.services.connectors import discover_capabilities, get_adapter
        from overbae.services.connectors.capability_resolution import propose_capability_assignments
        from overbae.services.connectors.profiling import profile_capability_candidates

        credential = self.get_object()
        mapping = credential.capability_mapping or {}
        # Never inherit the saved source: the wizard needs every signal the sample
        # holds to offer a different one.
        source = request.query_params.get("source")
        key = request.query_params.get("key") or mapping.get("key")
        raw_lookback = request.query_params.get("lookback_days")
        if raw_lookback is not None and str(raw_lookback).strip() != "":
            lookback_days = max(1, min(int(raw_lookback), 365))
        else:
            cfg = credential.active_config()
            lookback_days = (cfg.lookback_days if cfg and cfg.lookback_days else None) or 30
        try:
            adapter = get_adapter(credential)
            traces = adapter.sample_units(
                lookback_days=lookback_days,
                limit=200,
                source_project_id=request.query_params.get("source_project_id") or "",
            )
            results = discover_capabilities(
                traces,
                {"source": source, "key": key, "names": mapping.get("names") or []}
                if source
                else None,
            )
            # Ranked shapes let the wizard offer a boundary for apps that emit
            # no CAPABILITY observations, where `candidates` finds nothing.
            shapes = profile_capability_candidates(traces, adapter.conventions)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        proposals = propose_capability_assignments(
            credential.project,
            [c["value"] for c in results if c.get("value")] + [s["name"] for s in shapes[:10]],
        )
        return Response(
            {
                "candidates": results,
                "shapes": shapes,
                "proposals": proposals,
                "lookback_days": lookback_days,
                "sampled": len(traces),
            }
        )

    @extend_schema(
        summary="Update capability mapping and relabel existing spans",
        request=ConnectorCapabilityMappingWriteSerializer,
        responses={200: ConnectorCapabilityMappingResponseSerializer},
    )
    @action(detail=True, methods=["put"], url_path="capability-mapping")
    def capability_mapping(self, request, id=None):
        from overbae.services.connectors import (
            capability_source_error,
            relabel_connector_capabilities,
        )

        credential = self.get_object()
        write = ConnectorCapabilityMappingWriteSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        mapping = {k: v for k, v in write.validated_data.items() if v is not None}
        unsupported = capability_source_error(credential.connector_type, mapping.get("source"))
        if unsupported:
            return Response({"source": [unsupported]}, status=status.HTTP_400_BAD_REQUEST)
        if "fallback_capability_id" in mapping:
            mapping["fallback_capability_id"] = str(mapping["fallback_capability_id"])
        credential.capability_mapping = mapping
        credential.pending_capability_mapping = {}
        credential.capability_mapping_confirmed = True
        credential.save(
            update_fields=[
                "capability_mapping",
                "pending_capability_mapping",
                "capability_mapping_confirmed",
                "updated_at",
            ]
        )
        relabeled = relabel_connector_capabilities(credential)
        return Response(
            {"capability_mapping": credential.capability_mapping, "relabeled_span_count": relabeled}
        )

    @extend_schema(
        summary="List recent sync runs for this connector",
        responses={200: ConnectorSyncRunSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="runs")
    def runs(self, request, id=None):
        from overbae.models import ConnectorSyncRun

        credential = self.get_object()
        qs = ConnectorSyncRun.objects.filter(credential=credential)
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(ConnectorSyncRunSerializer(page, many=True).data)
        return Response(ConnectorSyncRunSerializer(qs[:50], many=True).data)

    @extend_schema(
        summary="List source projects available to this connector",
        responses={
            200: {
                "type": "object",
                "properties": {
                    "projects": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                            },
                        },
                    }
                },
            }
        },
    )
    @action(detail=True, methods=["get"], url_path="source-projects")
    def source_projects(self, request, id=None):
        from overbae.services.connectors import get_adapter

        credential = self.get_object()
        try:
            projects = get_adapter(credential).list_source_projects()
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"projects": [{"id": p.id, "name": p.name} for p in projects]})


@extend_schema_view(
    list=extend_schema(summary="List deployed models for the current project"),
    retrieve=extend_schema(summary="Get a deployed model by ID"),
    destroy=extend_schema(summary="Delete (undeploy) a deployed model"),
)
class DeployedModelViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = DeployedModelSerializer
    filterset_class = DeployedModelFilter
    search_fields = ["model_id", "base_model_id", "finetuning_job__name"]
    # ``avg_latency_ms`` / ``avg_tokens_per_second`` are deliberately absent: the
    # SQL means they are annotated with are replaced by medians in ``list()``, so
    # sorting on them would order the page by numbers it does not display.
    ordering_fields = [
        "created_at",
        "status",
        "model_id",
        "base_model_id",
        "request_count",
        "last_active_at",
        "total_tokens",
        "cost_total",
        "cost_this_month",
    ]
    lookup_field = "id"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DeployedModel.objects.none()
        from django.utils import timezone

        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        queryset = DeployedModel.objects.filter(project_id__in=_user_project_ids(self.request.user))
        if self.action == "list":
            queryset = queryset.filter(finetuning_job__isnull=False)
        return (
            queryset.select_related("finetuning_job", "finetuning_job__capability")
            .annotate(
                request_count=Count("inference_calls"),
                last_active_at=Max("inference_calls__created_at"),
                total_tokens=Sum("inference_calls__prompt_tokens")
                + Sum("inference_calls__completion_tokens"),
                avg_tokens_per_second=Avg("inference_calls__tokens_per_second"),
                # Warm calls only, to match the detail page's headline latency.
                avg_latency_ms=Avg(
                    "inference_calls__latency_ms",
                    filter=Q(inference_calls__is_cold=False, inference_calls__outcome="succeeded"),
                ),
                cost_total=Sum("inference_calls__cost"),
                cost_this_month=Sum(
                    "inference_calls__cost",
                    filter=Q(inference_calls__created_at__gte=month_start),
                ),
            )
            .order_by("-created_at")
        )

    def list(self, request, *args, **kwargs):
        """Overlay robust (median) latency/throughput onto each row.

        The queryset annotates SQL means for these two, but a few slow-client or
        very-long-generation outliers skew a mean badly; the median is computed
        per model in Python here so the table stays comparable across models
        with very different baselines.
        """
        response = super().list(request, *args, **kwargs)
        items = response.data.get("results") if isinstance(response.data, dict) else response.data
        if not items:
            return response
        lat: dict = defaultdict(list)
        tps: dict = defaultdict(list)
        ids = [it["id"] for it in items]
        for mid, latency, t in (
            InferenceCall.objects.filter(
                deployed_model_id__in=ids, is_cold=False, outcome="succeeded"
            )
            .order_by("-created_at")
            .values_list("deployed_model_id", "latency_ms", "tokens_per_second")
        ):
            if latency is not None:
                lat[str(mid)].append(latency)
            if t is not None:
                tps[str(mid)].append(t)
        for it in items:
            mid = str(it["id"])
            it["avg_latency_ms"] = percentile(sorted(lat[mid]), 0.5)
            it["avg_tokens_per_second"] = percentile(sorted(tps[mid]), 0.5)
        return response

    def perform_destroy(self, instance):
        from overbae.services.inference_client import get_inference_client

        DeployedModel.objects.filter(pk=instance.pk).update(status=DeployedModel.Status.DELETING)
        get_inference_client().delete_model(instance.model_id)

    @extend_schema(
        summary="Register or re-register a fine-tuned model (trigger quantize + register)",
        request=None,
        responses={200: DeployedModelSerializer},
    )
    @action(detail=True, methods=["post"], url_path="deploy")
    def deploy(self, request, id=None):
        """Trigger (re-)registration of a fine-tuned model via the vLLM pipeline."""
        from overbae.api.credit_gate import require_credits

        instance = self.get_object()
        if instance.finetuning_job_id is None:
            return Response(
                {"detail": "No associated finetuning job."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        require_credits(request.user)
        try:
            if instance.status in ("failed", "deleted"):
                retry_deployment(instance.pk)
            else:
                ensure_training_deployment(str(instance.finetuning_job_id))
        except InputValidationError as exc:
            raise drf_serializers.ValidationError({"detail": exc.detail}) from exc
        except (ValueError, RuntimeError, OSError) as exc:
            logger.exception("Could not deploy model %s", instance.pk)
            raise drf_serializers.ValidationError(
                {"detail": "Could not start model deployment. Try again."}
            ) from exc
        instance.refresh_from_db()
        return Response(DeployedModelSerializer(instance).data)

    @extend_schema(
        summary="Retry a failed model registration",
        request=None,
        responses={200: DeployedModelSerializer},
    )
    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, id=None):
        """Reset a FAILED model and re-queue registration."""
        from overbae.api.credit_gate import require_credits

        instance = self.get_object()
        if instance.status not in (
            DeployedModel.Status.FAILED,
            DeployedModel.Status.DELETED,
        ):
            return Response(
                {"detail": "Only FAILED or DELETED models can be retried."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if instance.finetuning_job_id is None:
            return Response(
                {"detail": "No associated finetuning job."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # register_finetuned_model silently returns unless the job is deployable —
        # dispatching anyway would park the model in QUEUED forever.
        if instance.finetuning_job.status not in (
            FinetuningJob.Status.SUCCEEDED,
            FinetuningJob.Status.DEPLOYING,
        ):
            return Response(
                {"detail": "Training job has no usable checkpoint — retry the training job first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        require_credits(request.user)
        try:
            retry_deployment(instance.pk)
        except InputValidationError as exc:
            raise drf_serializers.ValidationError({"detail": exc.detail}) from exc
        except (ValueError, RuntimeError, OSError) as exc:
            logger.exception("Could not retry model deployment %s", instance.pk)
            raise drf_serializers.ValidationError(
                {"detail": "Could not retry model deployment. Try again."}
            ) from exc
        instance.refresh_from_db()
        return Response(DeployedModelSerializer(instance).data)

    @extend_schema(
        summary="Undeploy a model (clears inference URL and marks DELETED; weights preserved)",
        request=None,
        responses={200: DeployedModelSerializer},
    )
    @action(detail=True, methods=["post"], url_path="undeploy")
    def undeploy(self, request, id=None):
        """Clear the inference URL so no new requests are routed here.

        The GPU pool scales to zero on its own once idle. Weights are preserved —
        use deploy/retry to bring it back.
        """
        from overbae.services.inference_client import get_inference_client

        instance = self.get_object()
        get_inference_client().delete_model(instance.model_id)
        instance.refresh_from_db()
        return Response(DeployedModelSerializer(instance).data)

    @extend_schema(
        summary="Aggregate inference metrics (tokens, latency, TPS, crude cost)",
        parameters=[InferenceMonitoringQuerySerializer],
        responses={200: InferenceMetricsSerializer},
    )
    @action(detail=True, methods=["get"], url_path="metrics")
    def metrics(self, request, id=None):
        instance = self.get_object()
        query = InferenceMonitoringQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(
            InferenceMetricsSerializer(model_metrics(instance, **query.validated_data)).data
        )

    @extend_schema(
        summary="Time-bucketed inference activity (requests + tokens over time)",
        parameters=[InferenceActivityQuerySerializer],
        responses={200: InferenceActivitySerializer},
    )
    @action(detail=True, methods=["get"], url_path="activity")
    def activity(self, request, id=None):
        instance = self.get_object()
        query = InferenceActivityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(
            InferenceActivitySerializer(model_activity(instance, **query.validated_data)).data
        )

    @extend_schema(
        summary="List downloadable weight/checkpoint files for this model",
        responses={200: ModelCheckpointsSerializer},
    )
    @action(detail=True, methods=["get"], url_path="checkpoints")
    def checkpoints(self, request, id=None):
        instance = self.get_object()
        if instance.finetuning_job_id is None:
            return Response(
                {"detail": "No associated finetuning job."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            file = get_checkpoint_download_url(instance.finetuning_job)
        except CheckpointArchiveError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        files = [file] if file is not None else []
        return Response(ModelCheckpointsSerializer({"files": files}).data)

    @extend_schema(
        summary="Live serving stats (backlog / running / workers)",
        responses={200: InferenceLiveStatsSerializer},
    )
    @action(detail=True, methods=["get"], url_path="live")
    def live(self, request, id=None):
        instance = self.get_object()
        data = live_worker_stats(instance)
        return Response(InferenceLiveStatsSerializer(data).data)
