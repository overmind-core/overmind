"""``POST`` / ``GET`` ``/api/v1/sync`` — AgentManifest sync from decorator scan."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.authentication import APITokenBackend
from overbae.api.scoping import project_ids_for
from overbae.models import APIToken, Project
from overbae.services.agent_manifest import apply_manifest, snapshot_of

TRACE_PROVIDERS = ("overmind", "langfuse", "langsmith", "opentelemetry")


class DeclaredSymbolSerializer(serializers.Serializer):
    qualname = serializers.CharField()
    file = serializers.CharField()
    line_start = serializers.IntegerField()
    line_end = serializers.IntegerField()
    role = serializers.ChoiceField(
        choices=("capability", "tool", "llm", "retrieval", "function", "task")
    )
    capability = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    slug = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    name = serializers.CharField(required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    signature = serializers.DictField(required=False)
    expectations = serializers.ListField(child=serializers.DictField(), required=False)
    task_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    unit = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    prompt_template = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    calls = serializers.ListField(child=serializers.CharField(), required=False)
    unresolved = serializers.ListField(child=serializers.CharField(), required=False)


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
    capability_card = serializers.DictField(required=False)
    archived = serializers.BooleanField(required=False, default=False)
    status = serializers.CharField(read_only=True)


class RepositorySnapshotSerializer(serializers.Serializer):
    repository = serializers.CharField(max_length=1024, required=False, allow_blank=True)
    directory = serializers.CharField(max_length=1024, required=False, allow_blank=True)
    branch = serializers.CharField(max_length=255, allow_blank=True, required=False)
    commit = serializers.CharField(max_length=64, allow_blank=True, required=False)
    dirty = serializers.BooleanField(required=False)
    fingerprint = serializers.CharField(max_length=64, required=False, allow_blank=True)
    scanned_at = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate_commit(self, value):
        text = (value or "").strip()
        if not text:
            return text
        if len(text) < 7 or any(c not in "0123456789abcdefABCDEF" for c in text):
            raise serializers.ValidationError("commit must be a hex SHA")
        return text

    def validate_fingerprint(self, value):
        text = (value or "").strip()
        if not text:
            return text
        if len(text) != 64 or any(c not in "0123456789abcdefABCDEF" for c in text):
            raise serializers.ValidationError("fingerprint must be a 64-char hex digest")
        return text


class GraphEdgeSerializer(serializers.Serializer):
    source = serializers.CharField()
    target = serializers.CharField()
    kind = serializers.CharField()
    observed = serializers.IntegerField(required=False)


class SyncSnapshotSerializer(serializers.Serializer):
    """Wire form of the AgentManifest — the unit of sync."""

    project_id = serializers.UUIDField()
    sdk_version = serializers.CharField(required=False, allow_blank=True)
    version = serializers.CharField(required=False, allow_blank=True)
    repository_snapshot = RepositorySnapshotSerializer(required=False, allow_null=True)
    last_synced_at = serializers.CharField(read_only=True, allow_null=True, required=False)
    symbols = DeclaredSymbolSerializer(many=True, required=False)
    capabilities = CapabilitySerializer(many=True, required=False)
    edges = GraphEdgeSerializer(many=True, required=False)
    repo_summary = serializers.CharField(required=False, allow_blank=True)
    trace_provider = serializers.ChoiceField(
        choices=TRACE_PROVIDERS, default="overmind", required=False
    )


class SyncView(APIView):
    """AgentManifest sync from a local decorator AST scan.

    POST pushes the scanned symbols. The server derives capability cards,
    mints behaviours, and stores invokes edges. Absence sets
    ``status=leftover`` (observed rows stay). GET returns the current graph.
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
        return Response(SyncSnapshotSerializer(snapshot_of(project)).data)

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
        applied = apply_manifest(project, data)
        return Response(SyncSnapshotSerializer(snapshot_of(project, capabilities=applied)).data)


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
