from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from overbae.models import ConnectorImportPreview
from overbae.services.connectors.imports import (
    confirm_import,
    expire_stalled_preview,
    request_preview,
)
from overbae.services.connectors.review import (
    group_payload,
    pending_groups,
    review_group,
    review_groups,
)


class ImportRangeSerializer(serializers.Serializer):
    source_project_id = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=128
    )
    backfill_from = serializers.DateTimeField(required=False, allow_null=True)
    backfill_to = serializers.DateTimeField(required=False)
    lookback_days = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class ImportPreviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConnectorImportPreview
        fields = [
            "id",
            "source_project_id",
            "window_from",
            "window_to",
            "status",
            "trace_count",
            "span_count",
            "estimated_seconds_min",
            "estimated_seconds_max",
            "error",
            "created_at",
            "finished_at",
            "expires_at",
        ]
        read_only_fields = fields


class ConfirmImportSerializer(serializers.Serializer):
    preview_id = serializers.UUIDField()


class TraceGroupEvidenceSerializer(serializers.Serializer):
    tools = serializers.ListField(child=serializers.CharField())
    span_count = serializers.IntegerField()
    root_count = serializers.IntegerField()
    input_shape = serializers.JSONField()
    output_shape = serializers.JSONField()
    incomplete = serializers.BooleanField()
    agent_steps = serializers.IntegerField()


class TraceGroupSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    revision = serializers.IntegerField()
    trace_count = serializers.IntegerField()
    unreviewed_trace_count = serializers.IntegerField()
    needs_review = serializers.BooleanField()
    capability_id = serializers.UUIDField(allow_null=True)
    mixed = serializers.BooleanField()
    evidence = TraceGroupEvidenceSerializer()
    sample_trace_ids = serializers.ListField(child=serializers.CharField())
    last_reviewed_at = serializers.DateTimeField(allow_null=True)
    last_reviewed_trace_count = serializers.IntegerField(allow_null=True)


class ReviewTraceGroupSerializer(serializers.Serializer):
    capability_id = serializers.UUIDField(allow_null=True)
    expected_revision = serializers.IntegerField(min_value=1)


class GroupAssignmentSerializer(ReviewTraceGroupSerializer):
    group_id = serializers.UUIDField()


class ReviewTraceGroupsSerializer(serializers.Serializer):
    assignments = GroupAssignmentSerializer(many=True, allow_empty=False)


class ConnectorReviewSummarySerializer(serializers.Serializer):
    pending_groups = serializers.IntegerField(min_value=0)
    pending_traces = serializers.IntegerField(min_value=0)


class ConnectorReviewActions:
    @extend_schema(request=ReviewTraceGroupsSerializer, responses=TraceGroupSerializer(many=True))
    @action(detail=True, methods=["post"], url_path="review", pagination_class=None)
    def review(self, request, id=None):
        write = ReviewTraceGroupsSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        results = review_groups(
            self.get_object(), write.validated_data["assignments"], actor=request.user
        )
        return Response(TraceGroupSerializer(results, many=True).data)

    @extend_schema(
        methods=["POST"], request=ImportRangeSerializer, responses={202: ImportPreviewSerializer}
    )
    @extend_schema(
        methods=["GET"],
        parameters=[OpenApiParameter("preview_id", str, required=True)],
        responses=ImportPreviewSerializer,
    )
    @action(detail=True, methods=["post", "get"], url_path="preview")
    def preview(self, request, id=None):
        credential = self.get_object()
        if request.method == "GET":
            query = ConfirmImportSerializer(data=request.query_params)
            query.is_valid(raise_exception=True)
            preview = get_object_or_404(
                ConnectorImportPreview, pk=query.validated_data["preview_id"], credential=credential
            )
            return Response(ImportPreviewSerializer(expire_stalled_preview(preview)).data)
        write = ImportRangeSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        preview = request_preview(credential, **write.validated_data)
        return Response(ImportPreviewSerializer(preview).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(request=ConfirmImportSerializer, responses={202: ImportPreviewSerializer})
    @action(detail=True, methods=["post"], url_path="import")
    def import_traces(self, request, id=None):
        write = ConfirmImportSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        preview = confirm_import(self.get_object(), write.validated_data["preview_id"])
        return Response(ImportPreviewSerializer(preview).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        parameters=[OpenApiParameter("pending_only", bool)],
        responses=TraceGroupSerializer(many=True),
    )
    @action(detail=True, methods=["get"], url_path="groups")
    def groups(self, request, id=None):
        credential = self.get_object()
        groups = (
            credential.trace_groups.filter(project_id=credential.project_id, spans__isnull=False)
            .distinct()
            .prefetch_related("reviews")
        )
        if request.query_params.get("pending_only", "").lower() == "true":
            groups = groups.filter(pk__in=pending_groups(credential).values("pk"))
        if request.query_params.get("search"):
            groups = groups.filter(name__icontains=request.query_params["search"])
        page = self.paginate_queryset(groups)
        data = TraceGroupSerializer([group_payload(group) for group in page], many=True).data
        return self.get_paginated_response(data)

    @extend_schema(request=ReviewTraceGroupSerializer, responses=TraceGroupSerializer)
    @action(detail=True, methods=["patch"], url_path=r"groups/(?P<group_id>[^/.]+)")
    def review_group(self, request, id=None, group_id=None):
        write = ReviewTraceGroupSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        result = review_group(
            self.get_object(), group_id, actor=request.user, **write.validated_data
        )
        return Response(TraceGroupSerializer(result).data)
