"""Chunked file upload: bytes to disk, never through browser memory. Parsing
happens when the dataset lands."""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import BaseParser, JSONParser
from rest_framework.response import Response

from overbae.services.datasets import files


class BeginUploadSerializer(serializers.Serializer):
    filename = serializers.CharField(max_length=255)


class UploadStateSerializer(serializers.Serializer):
    upload_id = serializers.CharField()
    received = serializers.IntegerField()


class UploadReservedSerializer(serializers.Serializer):
    upload_id = serializers.CharField()
    filename = serializers.CharField()
    chunk_bytes = serializers.IntegerField()
    max_bytes = serializers.IntegerField()


class OctetStreamParser(BaseParser):
    """Hands the view the raw body. Without it DRF's FileUploadParser matches
    ``*/*`` and 400s every chunk for having no Content-Disposition filename."""

    media_type = "*/*"

    def parse(self, stream, media_type=None, parser_context=None):
        return stream.read()


class UploadViewSet(viewsets.ViewSet):
    """Parsers must stay declared on the class: ``@action`` initkwargs only apply
    through the router, so an action-level parser silently reverts elsewhere."""

    parser_classes = [JSONParser, OctetStreamParser]
    lookup_value_regex = "[0-9a-f-]{36}"

    @extend_schema(
        summary="Reserve a chunked upload",
        request=BeginUploadSerializer,
        responses={201: UploadReservedSerializer},
    )
    def create(self, request):
        payload = BeginUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            upload_id, name = files.begin_upload(payload.validated_data["filename"])
        except files.FileError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "upload_id": upload_id,
                "filename": name,
                "chunk_bytes": files.CHUNK_BYTES,
                "max_bytes": files.MAX_UPLOAD_BYTES,
            },
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Bytes stored so far — where a resuming client continues",
        responses={200: UploadStateSerializer},
    )
    def retrieve(self, request, pk=None):
        return Response({"upload_id": pk, "received": files.upload_received(pk)})

    @extend_schema(
        summary="Append one chunk at a byte offset",
        parameters=[OpenApiParameter("offset", OpenApiTypes.INT, OpenApiParameter.QUERY)],
        request={"application/octet-stream": OpenApiTypes.BINARY},
        responses={200: UploadStateSerializer},
    )
    @action(detail=True, methods=["put"], url_path="chunk")
    def chunk(self, request, pk=None):
        try:
            offset = int(request.query_params.get("offset", "0"))
        except (TypeError, ValueError):
            return Response({"detail": "offset must be an integer."}, status=400)
        body = request.data if isinstance(request.data, bytes) else b""
        try:
            size = files.append_chunk(pk, offset, body)
        except files.FileError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"upload_id": pk, "received": size})
