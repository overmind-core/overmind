import json
import logging
import mimetypes

import redis.asyncio as aredis
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connections, transaction
from django.http import FileResponse, StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from overbae.api.datasets import ServerSentEventRenderer
from overbae.api.scoping import project_ids_for
from overbae.api.workbench import ProjectPipelineViewSet
from overbae.core.errors import InputValidationError
from overbae.models import AssistantEvent, AssistantInput, AssistantThread
from overbae.services.assistant import log, loop, threads, tools
from overbae.tasks.assistant import advance

logger = logging.getLogger(__name__)
PING_SECONDS = 15


class InteractionSerializer(serializers.Serializer):
    """The answer to one Question or Choices of a result the assistant showed."""

    call = serializers.CharField(max_length=32)
    id = serializers.CharField(max_length=80)
    values = serializers.DictField()


class AssistantInputSerializer(serializers.ModelSerializer):
    interaction = InteractionSerializer(allow_null=True)

    class Meta:
        model = AssistantInput
        fields = ["id", "text", "cells", "interaction", "urgent", "position", "revision"]
        read_only_fields = fields


class SubjectSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(sorted(threads.SUBJECTS))
    record = serializers.UUIDField()


class AssistantThreadSerializer(serializers.ModelSerializer):
    queue = serializers.SerializerMethodField()
    waiting_for = serializers.SerializerMethodField()

    class Meta:
        model = AssistantThread
        fields = [
            "id",
            "project",
            "subject_kind",
            "subject_id",
            "state",
            "stop_requested",
            "waiting_for",
            "queue",
        ]
        read_only_fields = fields

    @extend_schema_field(AssistantInputSerializer(many=True))
    def get_queue(self, thread):
        queued = thread.inputs.filter(state=AssistantInput.State.QUEUED)
        return AssistantInputSerializer(queued, many=True).data

    def get_waiting_for(self, thread) -> str | None:
        return thread.waiting_on["name"] if thread.waiting_on else None


class AssistantEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssistantEvent
        fields = ["sequence", "kind", "payload", "presentation", "created_at"]
        read_only_fields = fields


class OpenThreadSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    subject = SubjectSerializer(required=False, allow_null=True, default=None)


class SendMessageSerializer(serializers.Serializer):
    request_key = serializers.CharField(max_length=128)
    text = serializers.CharField(max_length=20000, allow_blank=True)
    cells = serializers.ListField(child=serializers.UUIDField(), max_length=20, default=list)
    interaction = InteractionSerializer(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        if not attrs["text"].strip() and not attrs["interaction"]:
            raise serializers.ValidationError("Write a message.")
        return attrs


class ChangeMessageSerializer(serializers.Serializer):
    message = serializers.UUIDField()
    revision = serializers.IntegerField(min_value=1)
    change = serializers.ChoiceField(["edit", "remove", "move", "send_now"])
    text = serializers.CharField(max_length=20000, required=False)
    before = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if attrs["change"] == "edit" and not attrs.get("text", "").strip():
            raise serializers.ValidationError("Write a message.")
        return attrs


class EventsQuerySerializer(serializers.Serializer):
    after = serializers.IntegerField(min_value=0, default=0)


class AssistantThreadViewSet(ProjectPipelineViewSet):
    def get_renderers(self):
        if getattr(self, "action", None) == "events":
            return [JSONRenderer(), ServerSentEventRenderer()]
        return super().get_renderers()

    @extend_schema(parameters=[SubjectSerializer], responses=AssistantThreadSerializer(many=True))
    def list(self, request):
        """The thread about one record, if the record has one."""
        query = SubjectSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        found = AssistantThread.objects.filter(
            project__is_active=True,
            project__memberships__user=request.user,
            project_id__in=project_ids_for(request.user, request.auth),
            subject_kind=query.validated_data["kind"],
            subject_id=query.validated_data["record"],
        )
        return Response(AssistantThreadSerializer(found, many=True).data)

    @extend_schema(request=OpenThreadSerializer, responses=AssistantThreadSerializer)
    def create(self, request):
        """Open the thread about a record, or a new thread with no subject."""
        body = OpenThreadSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        project = self.project(body.validated_data["project"])
        about = body.validated_data["subject"] or {}
        try:
            thread = threads.open_thread(project, about.get("kind", ""), about.get("record"))
        except InputValidationError as exc:
            raise NotFound from exc
        return Response(AssistantThreadSerializer(thread).data)

    @extend_schema(responses=AssistantThreadSerializer)
    def retrieve(self, request, pk=None):
        return Response(AssistantThreadSerializer(self.resolve(AssistantThread, pk)).data)

    @extend_schema(request=SendMessageSerializer, responses={202: AssistantInputSerializer})
    @action(detail=True, methods=["post"])
    def messages(self, request, pk=None):
        thread = self.resolve(AssistantThread, pk)
        body = SendMessageSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            message = threads.send(thread, request.user, **body.validated_data)
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        transaction.on_commit(lambda: advance.delay(str(thread.pk)))
        return Response(AssistantInputSerializer(message).data, status=202)

    @extend_schema(request=ChangeMessageSerializer, responses=AssistantInputSerializer)
    @action(detail=True, methods=["post"])
    def queue(self, request, pk=None):
        """Edit, remove, move or send now a message that is still queued."""
        thread = self.resolve(AssistantThread, pk)
        body = ChangeMessageSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = dict(body.validated_data)
        try:
            message = threads.change(
                values.pop("message"), thread, action=values.pop("change"), **values
            )
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        transaction.on_commit(lambda: advance.delay(str(thread.pk)))
        return Response(AssistantInputSerializer(message).data)

    @extend_schema(request=None, responses={202: AssistantThreadSerializer})
    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        thread = self.resolve(AssistantThread, pk)
        loop.stop(thread)
        thread.refresh_from_db()
        return Response(AssistantThreadSerializer(thread).data, status=202)

    @extend_schema(responses={(200, "application/octet-stream"): OpenApiTypes.BINARY})
    @action(
        detail=True, methods=["get"], url_path=r"images/(?P<name>[0-9a-f]{64}\.(?:png|jpg|webp))"
    )
    def images(self, request, pk=None, name=None):
        """An image the assistant presented in this thread."""
        path = tools.image_path(self.resolve(AssistantThread, pk), name)
        if not path.is_file():
            raise NotFound
        return FileResponse(path.open("rb"), content_type=mimetypes.guess_type(name)[0])

    @extend_schema(
        summary="Thread stream (SSE): events after a cursor, the thread and its queue, the reply being written",
        parameters=[EventsQuerySerializer],
        responses={(200, "text/event-stream"): str},
    )
    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        thread = self.resolve(AssistantThread, pk)
        query = EventsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        cursor = query.validated_data["after"]

        def read() -> list[str]:
            """Frames for every event after the cursor, then the thread and its queue."""
            nonlocal cursor
            thread.refresh_from_db()
            events = AssistantEventSerializer(
                thread.events.filter(sequence__gt=cursor), many=True
            ).data
            messages = [{"type": "event", "event": event} for event in events]
            messages.append({"type": "thread", "thread": AssistantThreadSerializer(thread).data})
            cursor = events[-1]["sequence"] if events else cursor
            # One held connection per open stream otherwise.
            connections.close_all()
            return [f"data: {json.dumps(message, default=str)}\n\n" for message in messages]

        # Must stay a native async generator: under ASGI a sync generator sends an empty body.
        async def stream():
            if not settings.CELERY_BROKER_URL.startswith(("redis://", "rediss://", "unix://")):
                for frame in await sync_to_async(read)():
                    yield frame
                return
            client = aredis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
            pubsub = client.pubsub()
            try:
                # Subscribe first: a change between the read and the subscription is not lost.
                await pubsub.subscribe(log.channel(thread.pk))
                for frame in await sync_to_async(read)():
                    yield frame
                if draft := await client.get(log.draft_key(thread.pk)):
                    # The reply written so far; what it gains follows on the channel.
                    yield f"data: {json.dumps({'type': 'draft', 'text': draft})}\n\n"
                idle = 0.0
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                    if message is None:
                        idle += 5.0
                        if idle >= PING_SECONDS:
                            idle = 0.0
                            yield ": ping\n\n"
                        continue
                    idle = 0.0
                    if json.loads(message["data"])["type"] == "changed":
                        for frame in await sync_to_async(read)():
                            yield frame
                    else:
                        yield f"data: {message['data']}\n\n"
            finally:
                try:
                    await pubsub.aclose()
                    await client.aclose()
                except Exception:  # noqa: BLE001 - teardown of a closed stream
                    logger.debug("assistant stream teardown failed", exc_info=True)

        response = StreamingHttpResponse(stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
