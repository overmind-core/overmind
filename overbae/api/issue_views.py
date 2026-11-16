from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from overbae.api.issue_serializers import (
    CapabilityActivityListSerializer,
    IssueDetailQuerySerializer,
    IssueDetailSerializer,
    IssuePageQuerySerializer,
    IssuePageSerializer,
    IssueQuerySerializer,
    IssueStateSerializer,
    ObservabilityOverviewSerializer,
    ObservabilityTrendsSerializer,
    TraceIssueListSerializer,
    TraceIssueQuerySerializer,
    TrainingPlanQuerySerializer,
    TrainingPlanSerializer,
)
from overbae.api.views import _user_project_ids
from overbae.models import Issue, Project
from overbae.services.issue_overview import (
    capability_rows,
    change_issue,
    issue_detail,
    issue_page,
    overview,
    trace_issues,
    training_plan,
    trends,
)

QUERY = [
    OpenApiParameter("project", str, required=True),
    OpenApiParameter("range", str, description="24h, 7d, 30d, 90d or 180d; defaults to 7d."),
    OpenApiParameter("capability", str),
]


class IssueViewSet(viewsets.GenericViewSet):
    serializer_class = IssueDetailSerializer
    filter_backends = []
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Issue.objects.none()
        return Issue.objects.filter(
            project_id__in=_user_project_ids(self.request.user, self.request.auth)
        ).select_related("capability")

    def scope(self, request, serializer=IssueQuerySerializer) -> dict:
        query = serializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        allowed = _user_project_ids(request.user, request.auth)
        if not Project.objects.filter(id=values["project"], id__in=allowed).exists():
            raise PermissionDenied("You are not a member of this project.")
        return values

    @extend_schema(exclude=True)
    def list(self, request):
        return self.ranked(request)

    @extend_schema(
        summary="Repeating issues, ranked",
        parameters=[
            *QUERY,
            OpenApiParameter("cause", str, description="model, system or data."),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses=IssuePageSerializer,
    )
    @action(detail=False, methods=["get"], url_path="ranked")
    def ranked(self, request):
        scope = self.scope(request, IssuePageQuerySerializer)
        data = issue_page(
            scope["project"],
            scope["range"],
            scope.get("capability"),
            scope.get("cause"),
            scope["page"],
            scope["page_size"],
        )
        return Response(IssuePageSerializer(data).data)

    @extend_schema(
        summary="What to know first: signals to train and system issues",
        parameters=QUERY,
        responses=ObservabilityOverviewSerializer,
    )
    @action(detail=False, methods=["get"], url_path="overview")
    def read_overview(self, request):
        scope = self.scope(request)
        data = overview(scope["project"], scope["range"], scope.get("capability"))
        return Response(ObservabilityOverviewSerializer(data).data)

    @extend_schema(
        summary="Traffic, failure rate, score and cost over time",
        parameters=QUERY,
        responses=ObservabilityTrendsSerializer,
    )
    @action(detail=False, methods=["get"], url_path="trends")
    def read_trends(self, request):
        scope = self.scope(request)
        data = trends(scope["project"], scope["range"], scope.get("capability"))
        return Response(ObservabilityTrendsSerializer(data).data)

    @extend_schema(
        summary="Every capability, ranked by its signal to train",
        parameters=QUERY,
        responses=CapabilityActivityListSerializer,
    )
    @action(detail=False, methods=["get"], url_path="capabilities")
    def read_capabilities(self, request):
        scope = self.scope(request)
        rows = capability_rows(scope["project"], scope["range"])
        return Response(CapabilityActivityListSerializer({"capabilities": rows}).data)

    @extend_schema(
        summary="The traces a capability's training run would start from",
        parameters=QUERY,
        responses=TrainingPlanSerializer,
    )
    @action(detail=False, methods=["get"], url_path="training-plan")
    def read_training_plan(self, request):
        scope = self.scope(request, TrainingPlanQuerySerializer)
        data = training_plan(scope["project"], scope["capability"], scope["range"])
        return Response(TrainingPlanSerializer(data).data)

    @extend_schema(
        summary="The repeating issues one trace belongs to",
        parameters=[
            OpenApiParameter("project", str, required=True),
            OpenApiParameter("trace", str, required=True),
        ],
        responses=TraceIssueListSerializer,
    )
    @action(detail=False, methods=["get"], url_path="for-trace")
    def for_trace(self, request):
        scope = self.scope(request, TraceIssueQuerySerializer)
        data = trace_issues(scope["project"], scope["trace"])
        return Response(TraceIssueListSerializer({"issues": data}).data)

    @extend_schema(
        summary="One issue with its trend, causes and examples",
        parameters=[OpenApiParameter("range", str, description="Defaults to 7d.")],
        responses=IssueDetailSerializer,
    )
    def retrieve(self, request, pk=None):
        query = IssueDetailQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = issue_detail(self.get_object(), query.validated_data["range"])
        return Response(IssueDetailSerializer(data).data)

    @extend_schema(
        summary="Resolve, mute or reopen an issue",
        request=IssueStateSerializer,
        responses=IssueDetailSerializer,
    )
    @action(detail=True, methods=["post"], url_path="state")
    def set_state(self, request, pk=None):
        body = IssueStateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        issue = self.get_object()
        change_issue(issue, state=body.validated_data["action"])
        return Response(IssueDetailSerializer(issue_detail(issue, "7d")).data)
