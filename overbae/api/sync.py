"""``POST`` / ``GET`` ``/api/v1/sync`` — two-way ``overmind.toml`` sync."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.authentication import APITokenBackend
from overbae.api.scoping import project_ids_for
from overbae.models import APIToken, Project
from overbae.services.sync import apply_snapshot, snapshot_of

TRACE_PROVIDERS = ("overmind", "langfuse", "langsmith", "opentelemetry")
METRIC_TYPES = ("llm_judge_custom", "managed", "deterministic", "custom_judge")


class EvalMetricSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.ChoiceField(choices=METRIC_TYPES, default="llm_judge_custom")
    prompt = serializers.CharField(required=False, allow_blank=True)
    measures = serializers.CharField(required=False, allow_blank=True)
    rationale = serializers.CharField(required=False, allow_blank=True)
    rubric = serializers.CharField(required=False, allow_blank=True)
    requires_reference = serializers.BooleanField(default=False)
    managed_name = serializers.CharField(required=False, allow_blank=True)


class CapabilitySerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False, allow_null=True)
    slug = serializers.SlugField()
    name = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True)
    entrypoint_fn = serializers.CharField(required=False, allow_blank=True)
    model = serializers.CharField(required=False, allow_blank=True)
    source_path = serializers.CharField(required=False, allow_blank=True)
    system_prompt = serializers.CharField(required=False, allow_blank=True)
    tools_summary = serializers.CharField(required=False, allow_blank=True)
    eval_metrics = EvalMetricSerializer(many=True, required=False)
    capability_card = serializers.DictField(required=False)
    eval_matrix = serializers.ListField(child=serializers.DictField(), required=False)
    archived = serializers.BooleanField(required=False, default=False)
    status = serializers.CharField(read_only=True)


class SyncSnapshotSerializer(serializers.Serializer):
    """Wire form of ``overmind.toml`` — the unit of two-way sync."""

    project_id = serializers.UUIDField()
    repo_summary = serializers.CharField(required=False, allow_blank=True)
    trace_provider = serializers.ChoiceField(choices=TRACE_PROVIDERS, default="overmind")
    version = serializers.CharField()
    capabilities = CapabilitySerializer(many=True)


class SyncView(APIView):
    """Two-way ``overmind.toml`` sync.

    POST pushes the local snapshot. The server reconciles capability identity —
    carry / leftover / remount — and never deletes: absence sets
    ``status=leftover`` (observed rows stay). ``archived=true`` keeps leftover.
    POST response is the incoming set with ids filled in. GET returns leftovers
    with ``archived=true`` so toml round-trips them without listing them in the
    console.

    Auth: ``X-Api-Key`` (``APIToken``). Project keys must match ``project_id``;
    account keys may sync any project the user belongs to.
    """

    authentication_classes = [APITokenBackend]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="project_id",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
            ),
        ],
        responses={200: SyncSnapshotSerializer},
    )
    def get(self, request):
        project = _project_for(request, request.query_params.get("project_id"))
        if project is None:
            return Response(
                {"detail": "project_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        if isinstance(project, Response):
            return project
        return Response(snapshot_of(project))

    @extend_schema(
        request=SyncSnapshotSerializer,
        responses={200: SyncSnapshotSerializer},
    )
    def post(self, request):
        ser = SyncSnapshotSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        project = _project_for(request, data["project_id"])
        if isinstance(project, Response):
            return project
        applied = apply_snapshot(project, data)
        return Response(snapshot_of(project, capabilities=applied))


def _project_for(request, project_id):
    token = request.auth
    if not isinstance(token, APIToken):
        return Response({"detail": "API key required"}, status=status.HTTP_401_UNAUTHORIZED)
    if not project_id:
        return None
    if str(project_id) not in {str(pid) for pid in project_ids_for(token.user, token)}:
        return Response(
            {"detail": "API key does not match project_id"},
            status=status.HTTP_403_FORBIDDEN,
        )
    try:
        return Project.objects.get(pk=project_id)
    except Project.DoesNotExist:
        return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
