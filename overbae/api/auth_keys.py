from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.models import APIToken
from overbae.models.iam import account_scope, project_scope


class APITokenScopeSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=["account", "project"])
    resourceIds = serializers.ListField(  # noqa: N815
        child=serializers.UUIDField(), required=False
    )
    permission = serializers.ListField(
        child=serializers.ChoiceField(choices=["read", "write"]),
        required=False,
    )

    def validate(self, data):
        kind = data["scope"]
        resource_ids = data.get("resourceIds") or []
        if kind == "project" and len(resource_ids) != 1:
            raise serializers.ValidationError(
                {"resourceIds": "Project scope requires exactly one project id."}
            )
        if kind == "account" and resource_ids:
            raise serializers.ValidationError(
                {"resourceIds": "Account scope cannot include resourceIds."}
            )
        data["permission"] = list(data.get("permission") or ["read", "write"])
        if not data["permission"]:
            raise serializers.ValidationError({"permission": "Must not be empty."})
        data["resourceIds"] = resource_ids
        return data


class APITokenMetadataSerializer(serializers.ModelSerializer):
    scope = APITokenScopeSerializer()

    class Meta:
        model = APIToken
        fields = [
            "id",
            "name",
            "prefix",
            "project",
            "scope",
            "is_active",
            "expires_at",
            "last_used_at",
            "created_at",
        ]


class APITokenCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    project = serializers.UUIDField(required=False)
    scope = APITokenScopeSerializer(required=False)


class APITokenCreateResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    key = serializers.CharField(help_text="Plaintext secret. Shown only once.")
    name = serializers.CharField()
    prefix = serializers.CharField()
    project = serializers.UUIDField(allow_null=True)
    scope = APITokenScopeSerializer()
    created_at = serializers.DateTimeField()


class APITokenListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = APITokenMetadataSerializer

    queryset = APIToken.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return APIToken.objects.none()
        return APIToken.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.request.method == "POST":
            return APITokenCreateRequestSerializer
        return APITokenMetadataSerializer

    @extend_schema(
        summary="List API tokens",
        description="List metadata for API tokens you created (secrets are never returned).",
        responses={200: APITokenMetadataSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        summary="Generate API token",
        description=(
            "Create a new API token. Passing ``project`` (or ``scope.scope=project``) "
            "mints a key pinned to that project. ``scope.scope=account`` covers every "
            "project the user belongs to."
        ),
        request=APITokenCreateRequestSerializer,
        responses={
            201: OpenApiResponse(
                response=APITokenCreateResponseSerializer,
                description="Plaintext key is returned only in this response.",
            )
        },
    )
    def post(self, request, *args, **kwargs):
        ser = APITokenCreateRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        name = ser.validated_data.get("name") or ""
        project, scope_doc = _resolve_create_scope(ser.validated_data, request.user)
        if isinstance(project, Response):
            return project

        raw, instance = APIToken.create_for_user(
            request.user,
            name=name,
            project=project,
            permission=scope_doc["permission"],
        )
        return Response(
            {
                "id": instance.id,
                "key": raw,
                "name": instance.name,
                "prefix": instance.prefix,
                "project": instance.project_id,
                "scope": instance.scope,
                "created_at": instance.created_at,
            },
            status=status.HTTP_201_CREATED,
        )


def _resolve_create_scope(data: dict, user):
    project_id = data.get("project")
    scope = data.get("scope")
    if project_id is not None:
        return _project_for_member(user, project_id), project_scope(project_id)
    if scope is None:
        return Response(
            {"detail": "Provide project or scope."},
            status=status.HTTP_400_BAD_REQUEST,
        ), None
    permission = scope["permission"]
    if scope["scope"] == "account":
        return None, account_scope(permission=permission)
    return (
        _project_for_member(user, scope["resourceIds"][0]),
        project_scope(scope["resourceIds"][0], permission=permission),
    )


def _project_for_member(user, project_id):
    from overbae.models import Project, ProjectMembership

    proj = Project.objects.filter(id=project_id).first()
    if not proj:
        return Response(
            {"detail": "Project not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    if not ProjectMembership.objects.filter(user=user, project=proj).exists():
        return Response(
            {"detail": "You are not a member of this project."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return proj


class APITokenCurrentView(APIView):
    """Introspect the API key on this request (``X-Api-Key`` / Bearer)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Current API token scope",
        description="Return the scope of the API key on this request.",
        responses={200: APITokenScopeSerializer},
    )
    def get(self, request):
        token = request.auth
        if not isinstance(token, APIToken):
            return Response({"detail": "API key required"}, status=status.HTTP_401_UNAUTHORIZED)
        return Response(APITokenScopeSerializer(token.scope).data)


class APITokenDestroyView(generics.DestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = APITokenMetadataSerializer
    lookup_field = "id"

    def get_queryset(self):
        return APIToken.objects.filter(user=self.request.user)

    @extend_schema(summary="Revoke API token", description="Permanently revoke an API token.")
    def delete(self, request, *args, **kwargs):
        return super().delete(request, *args, **kwargs)
