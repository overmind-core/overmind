from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from overbae.api.scoping import project_ids_for
from overbae.api.uploads import OctetStreamParser
from overbae.models import APIToken, Capability, Dataset, DatasetTransfer, Project
from overbae.services.datasets import files, transfers
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.mcp.auth import ip_allowed


class TransferInputSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    request_key = serializers.CharField(max_length=128)
    filename = serializers.CharField(max_length=200)
    size = serializers.IntegerField(min_value=1, max_value=files.MAX_UPLOAD_BYTES)
    sha256 = serializers.RegexField(r"^[0-9a-f]{64}$")
    json_rows_field = serializers.CharField(max_length=255, required=False)
    dataset = serializers.UUIDField(required=False)
    capability = serializers.UUIDField(required=False)
    intent = serializers.ChoiceField(choices=["train", "eval", "explore"], required=False)
    brief = serializers.CharField(default="", allow_blank=True, max_length=10000)
    split = serializers.IntegerField(min_value=1, max_value=99, required=False)
    split_position = serializers.ChoiceField(choices=["head", "tail", "random"], default="tail")

    def validate(self, data):
        if data.get("json_rows_field") and not data["filename"].lower().endswith(".json"):
            raise ValidationError("An explicit JSON rows field requires a .json file.")
        if data.get("dataset") and any(
            data.get(key) for key in ("capability", "intent", "brief", "split")
        ):
            raise ValidationError("An attachment preserves its destination's settings.")
        if data.get("split") and data.get("intent"):
            raise ValidationError("A split fixes train and eval intents.")
        if data["size"] > files.upload_byte_limit(data["filename"]):
            raise ValidationError(
                {
                    "code": "file_too_large",
                    "detail": "The file exceeds the server limit for this file type.",
                }
            )
        return data


class TransferSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    project_id = serializers.UUIDField()
    request_key = serializers.CharField()
    upload_id = serializers.UUIDField()
    filename = serializers.CharField()
    sha256 = serializers.CharField()
    size = serializers.IntegerField()
    received = serializers.IntegerField()
    state = serializers.CharField()
    staging_available = serializers.BooleanField()
    next_action = serializers.CharField()
    result = serializers.JSONField()
    chunk_bytes = serializers.IntegerField()
    max_bytes = serializers.IntegerField()
    dispatch = serializers.CharField()
    updated_at = serializers.DateTimeField()
    published_at = serializers.DateTimeField(allow_null=True)
    resource_uri = serializers.CharField()


class TransferCheckSerializer(serializers.Serializer):
    project = serializers.UUIDField()


class TransferReadinessSerializer(serializers.Serializer):
    protocol_version = serializers.IntegerField()
    project_id = serializers.UUIDField()
    user_id = serializers.CharField()
    credential_scope = serializers.CharField()
    can_upload = serializers.BooleanField()
    can_export = serializers.BooleanField()
    mcp_url = serializers.URLField()


class TransferErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
    code = serializers.CharField()


class DatasetTransferViewSet(viewsets.ViewSet):
    parser_classes = [JSONParser, OctetStreamParser]
    lookup_value_regex = "[0-9a-f-]{36}"

    def permissions_for_transfer(self):
        token = self.request.auth
        if isinstance(token, APIToken):
            if not ip_allowed(token, self.request.META.get("REMOTE_ADDR")):
                raise PermissionDenied("The API key is not valid for this address.")
            return set(token.scope.get("permission", []))
        return {"read"} if getattr(self.request.user, "is_guest", False) else {"read", "write"}

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        needed = "read" if request.method in {"GET", "HEAD", "OPTIONS"} else "write"
        if needed not in self.permissions_for_transfer():
            raise PermissionDenied("This connection does not permit this transfer operation.")

    def target_project(self, value):
        return get_object_or_404(
            Project,
            pk=value,
            is_active=True,
            memberships__user=self.request.user,
            id__in=project_ids_for(self.request.user, self.request.auth),
        )

    def get_object(self, pk):
        return get_object_or_404(
            DatasetTransfer,
            pk=pk,
            project__is_active=True,
            project__memberships__user=self.request.user,
            project_id__in=project_ids_for(self.request.user, self.request.auth),
        )

    def result(self, operation, *, status=200):
        try:
            transfer = operation()
        except (DatasetError, files.FileError) as exc:
            return Response(
                TransferErrorSerializer(
                    {"detail": exc.detail, "code": getattr(exc, "code", "invalid_file")}
                ).data,
                status=409,
            )
        transfer.refresh_from_db()
        return Response(TransferSerializer(transfers.describe(transfer)).data, status=status)

    @extend_schema(parameters=[TransferCheckSerializer], responses=TransferReadinessSerializer)
    @action(detail=False, methods=["get"])
    def readiness(self, request):
        body = TransferCheckSerializer(data=request.query_params)
        body.is_valid(raise_exception=True)
        project = self.target_project(body.validated_data["project"])
        permissions = self.permissions_for_transfer()
        return Response(
            TransferReadinessSerializer(
                {
                    "protocol_version": transfers.PROTOCOL_VERSION,
                    "project_id": project.pk,
                    "user_id": str(request.user.pk),
                    "can_upload": "write" in permissions,
                    "credential_scope": request.auth.scope.get("scope", "unknown")
                    if isinstance(request.auth, APIToken)
                    else "session",
                    "can_export": "read" in permissions,
                    "mcp_url": request.build_absolute_uri("/api/mcp/"),
                }
            ).data
        )

    @extend_schema(request=TransferInputSerializer, responses={201: TransferSerializer})
    def create(self, request):
        body = TransferInputSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = body.validated_data
        project = self.target_project(values.pop("project"))
        key = values.pop("request_key")
        for field, model in (("dataset", Dataset), ("capability", Capability)):
            if values.get(field):
                get_object_or_404(model, project=project, pk=values[field])
                values[field] = str(values[field])
        return self.result(
            lambda: transfers.reserve(project, request.user, key, values), status=201
        )

    @extend_schema(responses=TransferSerializer)
    def retrieve(self, request, pk=None):
        return self.result(lambda: self.get_object(pk))

    @extend_schema(request=None, responses=TransferSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        transfer = self.get_object(pk)
        return self.result(lambda: transfers.complete(transfer))

    @extend_schema(
        parameters=[
            OpenApiParameter("offset", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True)
        ],
        request={"application/octet-stream": OpenApiTypes.BINARY},
        responses=TransferSerializer,
    )
    @action(detail=True, methods=["put"])
    def chunk(self, request, pk=None):
        transfer = self.get_object(pk)
        offset = serializers.IntegerField(min_value=0).run_validation(
            request.query_params.get("offset")
        )
        return self.result(
            lambda: transfers.append(
                transfer, offset, request.data if isinstance(request.data, bytes) else b""
            )
        )
