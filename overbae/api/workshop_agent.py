import asyncio
import json
import time

from asgiref.sync import sync_to_async
from django.db import connections
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_serializer
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from overbae.api.workbench import ProjectPipelineViewSet
from overbae.models import Dataset, Project, WorkshopTask
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.workshop_agent import conversation, lifecycle, provisioning
from overbae.services.workshop_agent.contracts import ControlMessage, SendMessage, StartTask


class WorkshopStartSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    dataset = serializers.UUIDField()
    context_datasets = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=100
    )
    objective = serializers.CharField(max_length=16000, trim_whitespace=True)
    request_key = serializers.CharField(max_length=128)


class WorkshopQuerySerializer(serializers.Serializer):
    project = serializers.UUIDField()
    dataset = serializers.UUIDField()


class WorkshopControlSerializer(serializers.Serializer):
    command = serializers.ChoiceField(choices=["pause", "resume", "cancel"])


class WorkshopInteractionSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=180)
    component = serializers.CharField(max_length=160)
    values = serializers.DictField()


class WorkshopMessageRequestSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    dataset = serializers.UUIDField()
    text = serializers.CharField(max_length=16000, trim_whitespace=True)
    request_key = serializers.CharField(max_length=128)
    cell = serializers.UUIDField(required=False, allow_null=True)
    interaction = WorkshopInteractionSerializer(required=False)


class WorkshopCellReferenceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    dataset = serializers.UUIDField()
    name = serializers.CharField()
    rows = serializers.IntegerField()
    columns = serializers.ListField(child=serializers.DictField(), required=False)
    checks = serializers.DictField(required=False)
    fingerprint = serializers.CharField(required=False)


class WorkshopPresentationTableSerializer(serializers.Serializer):
    rows = serializers.ListField(child=serializers.DictField())
    sha256 = serializers.CharField()


class WorkshopPresentationImageSerializer(serializers.Serializer):
    url = serializers.CharField()
    sha256 = serializers.CharField()


class WorkshopPresentationSerializer(serializers.Serializer):
    program = serializers.CharField()
    document = serializers.JSONField()
    data = serializers.DictField(child=WorkshopPresentationTableSerializer())
    images = serializers.DictField(child=WorkshopPresentationImageSerializer())


class WorkshopMessageControlSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=180)
    command = serializers.ChoiceField(choices=["edit", "remove", "move"])
    text = serializers.CharField(max_length=16000, required=False, allow_null=True)
    position = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    revision = serializers.IntegerField(min_value=0)
    request_key = serializers.CharField(max_length=128)


class WorkshopMessageSerializer(serializers.Serializer):
    id = serializers.CharField()
    role = serializers.CharField()
    text = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    revision = serializers.IntegerField(default=0)
    queue_position = serializers.IntegerField(required=False)
    cells = WorkshopCellReferenceSerializer(many=True, required=False)
    tool = serializers.CharField(required=False)
    code = serializers.CharField(required=False, allow_blank=True)
    error = serializers.CharField(required=False, allow_null=True)
    presentation = WorkshopPresentationSerializer(required=False)
    interaction = WorkshopInteractionSerializer(required=False)


class WorkshopChatEventSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    task = serializers.UUIDField()
    message = WorkshopMessageSerializer()


class WorkshopHistorySerializer(serializers.Serializer):
    events = WorkshopChatEventSerializer(many=True)
    cursor = serializers.IntegerField()


class WorkshopHistoryQuerySerializer(WorkshopQuerySerializer):
    after = serializers.IntegerField(required=False, default=0, min_value=0)


class WorkshopTaskSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    dataset = serializers.UUIDField()
    objective = serializers.CharField()
    context_sources = serializers.ListField(child=serializers.DictField())
    state = serializers.CharField()
    desired_state = serializers.CharField()
    step = serializers.IntegerField()
    used_tokens = serializers.IntegerField(allow_null=True)
    result = serializers.DictField()
    updated_at = serializers.DateTimeField()


@extend_schema_serializer(many=False)
class WorkshopListSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    tasks = WorkshopTaskSerializer(many=True)


class WorkshopTaskViewSet(ProjectPipelineViewSet):
    @extend_schema(request=WorkshopMessageControlSerializer, responses=WorkshopMessageSerializer)
    @action(detail=True, methods=["post"], url_path="messages/control")
    def messages_control(self, request, pk=None):
        task = self.resolve(WorkshopTask, pk)
        serializer = WorkshopMessageControlSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            message = conversation.control_message(
                lifecycle.NativeGrant(task.project, request.user),
                ControlMessage(task=task.pk, **serializer.validated_data),
            )
        except DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc
        return Response(WorkshopMessageSerializer(message).data)

    def conversation_scope(self, request):
        query = WorkshopHistoryQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        project = self.project(query.validated_data["project"])
        dataset = get_object_or_404(Dataset, pk=query.validated_data["dataset"], project=project)
        return project, dataset, query.validated_data["after"]

    @extend_schema(parameters=[WorkshopHistoryQuerySerializer], responses=WorkshopHistorySerializer)
    @action(detail=False, methods=["get"])
    def history(self, request):
        project, dataset, after = self.conversation_scope(request)
        return Response(
            WorkshopHistorySerializer(conversation.page(project, dataset.pk, after)).data
        )

    @extend_schema(
        parameters=[WorkshopHistoryQuerySerializer],
        responses={(200, "text/event-stream"): OpenApiTypes.BINARY},
    )
    @action(detail=False, methods=["get"])
    def events(self, request):
        project, dataset, after = self.conversation_scope(request)

        def batch(cursor):
            try:
                if not Project.objects.filter(
                    pk=project.pk,
                    is_active=True,
                    memberships__user=request.user,
                    memberships__user__is_active=True,
                ).exists():
                    return None
                return WorkshopHistorySerializer(
                    conversation.page(project, dataset.pk, cursor)
                ).data
            finally:
                connections.close_all()

        async def stream():
            cursor = after
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                value = await sync_to_async(batch)(cursor)
                if value is None:
                    return
                if value["events"]:
                    cursor = value["cursor"]
                    yield f"id: {cursor}\ndata: {json.dumps(value)}\n\n"
                else:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(0.5)

        response = StreamingHttpResponse(stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    @extend_schema(
        request=WorkshopMessageRequestSerializer, responses={201: WorkshopTaskSerializer}
    )
    @action(detail=False, methods=["post"])
    def messages(self, request):
        serializer = WorkshopMessageRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        project = self.project(data.pop("project"))
        try:
            task = conversation.send(
                lifecycle.NativeGrant(project, request.user), SendMessage(**data)
            )
        except DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc
        return Response(WorkshopTaskSerializer(lifecycle.describe(task)).data, status=201)

    @extend_schema(parameters=[WorkshopQuerySerializer], responses=WorkshopListSerializer)
    def list(self, request):
        query = WorkshopQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        project = self.project(query.validated_data["project"])
        dataset = get_object_or_404(Dataset, pk=query.validated_data["dataset"], project=project)
        tasks = WorkshopTask.objects.filter(dataset=dataset).order_by("-created_at")[:20]
        return Response(
            WorkshopListSerializer(
                {
                    "enabled": provisioning.available(project),
                    "tasks": [lifecycle.describe(task) for task in tasks],
                }
            ).data
        )

    @extend_schema(request=WorkshopStartSerializer, responses={201: WorkshopTaskSerializer})
    def create(self, request):
        serializer = WorkshopStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        project = self.project(data.pop("project"))
        context = lifecycle.NativeGrant(project, request.user)
        try:
            task = lifecycle.start(context, StartTask(**data))
        except DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc
        return Response(WorkshopTaskSerializer(lifecycle.describe(task)).data, status=201)

    @extend_schema(responses=WorkshopTaskSerializer)
    def retrieve(self, request, pk=None):
        task = self.resolve(WorkshopTask, pk)
        return Response(WorkshopTaskSerializer(lifecycle.describe(task)).data)

    @extend_schema(request=WorkshopControlSerializer, responses=WorkshopTaskSerializer)
    @action(detail=True, methods=["post"])
    def control(self, request, pk=None):
        task = self.resolve(WorkshopTask, pk)
        serializer = WorkshopControlSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            task = lifecycle.control(task.project, task.pk, serializer.validated_data["command"])
        except DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc
        return Response(WorkshopTaskSerializer(lifecycle.describe(task)).data)
