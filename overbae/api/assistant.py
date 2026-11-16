import mimetypes

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_field
from redis.exceptions import RedisError
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from overbae.api.scoping import project_ids_for
from overbae.api.workbench import ProjectPipelineViewSet
from overbae.core.errors import InputValidationError
from overbae.models import AssistantEvent, AssistantInput, AssistantThread, Dataset
from overbae.services.assistant import log, threads, tools, turn
from overbae.services.datasets import events

# What the Console shows of a call's arguments; a package's files can be much longer.
SHOWN_ARGUMENTS = 4000


class InteractionSerializer(serializers.Serializer):
    """The answer to one Question or Choices of a result the assistant showed."""

    call = serializers.CharField(max_length=32)
    id = serializers.CharField(max_length=80)
    values = serializers.DictField()


class AssistantInputSerializer(serializers.ModelSerializer):
    interaction = InteractionSerializer(allow_null=True)

    class Meta:
        model = AssistantInput
        fields = [
            "id",
            "created_by",
            "text",
            "cells",
            "interaction",
            "urgent",
            "position",
            "revision",
        ]
        read_only_fields = fields


class ParticipantSerializer(serializers.Serializer):
    user = serializers.IntegerField()
    email = serializers.EmailField()
    me = serializers.BooleanField()


class AssistantThreadSerializer(serializers.ModelSerializer):
    queue = serializers.SerializerMethodField()
    participants = serializers.SerializerMethodField()
    draft = serializers.SerializerMethodField()

    class Meta:
        model = AssistantThread
        fields = [
            "id",
            "project",
            "dataset",
            "state",
            "stop_requested",
            "queue",
            "participants",
            "draft",
        ]
        read_only_fields = fields

    @extend_schema_field(AssistantInputSerializer(many=True))
    def get_queue(self, thread):
        queued = thread.inputs.filter(state=AssistantInput.State.QUEUED)
        return AssistantInputSerializer(queued, many=True).data

    @extend_schema_field(ParticipantSerializer(many=True))
    def get_participants(self, thread):
        """Who wrote in the conversation, sent or queued: members share one conversation
        about a dataset, and a reader must tell a teammate's message from their own."""
        senders = set(
            thread.events.filter(kind=AssistantEvent.Kind.USER).values_list(
                "payload__user", flat=True
            )
        )
        senders |= set(
            thread.inputs.filter(state=AssistantInput.State.QUEUED).values_list(
                "created_by_id", flat=True
            )
        )
        viewer = self.context["request"].user.pk
        found = get_user_model().objects.filter(pk__in=senders).order_by("pk")
        return [
            {"user": pk, "email": email, "me": pk == viewer}
            for pk, email in found.values_list("pk", "email")
        ]

    def get_draft(self, thread) -> str:
        """The reply written so far, for a page that opens in the middle of it."""
        client = events.client()
        if client is None or thread.state != AssistantThread.State.RUNNING:
            return ""
        try:
            return client.get(log.draft_key(thread.pk)) or ""
        except RedisError:
            # The reply so far is a convenience: the saved reply arrives without it.
            return ""


class AssistantEventSerializer(serializers.ModelSerializer):
    """An event as the Console shows it. What the model reads of a tool's result stays in
    Postgres: only a Python cell's output, an error and a result document leave it."""

    payload = serializers.SerializerMethodField()

    class Meta:
        model = AssistantEvent
        fields = ["sequence", "kind", "payload", "presentation", "created_at"]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_payload(self, event):
        payload = event.payload
        if event.kind == AssistantEvent.Kind.USER:
            keep = ("text", "cells", "interaction", "user")
            return {key: payload.get(key) for key in keep}
        if event.kind == AssistantEvent.Kind.ASSISTANT:
            return {
                "text": payload["text"],
                "calls": [
                    {**call, "raw": call["raw"][:SHOWN_ARGUMENTS]} for call in payload["calls"]
                ],
            }
        if event.kind == AssistantEvent.Kind.TOOL_RESULT:
            content = payload["content"] if isinstance(payload["content"], dict) else {}
            error = content.get("error")
            if isinstance(error, dict):
                error = error.get("message")
            shown = {"output": content.get("output"), "error": error, "note": content.get("note")}
            return {
                "call": payload["call"],
                "name": payload["name"],
                "ok": payload["ok"],
                "content": {key: value for key, value in shown.items() if value},
            }
        if event.kind == AssistantEvent.Kind.SUMMARY:
            return {}
        return payload


class OpenThreadSerializer(serializers.Serializer):
    dataset = serializers.UUIDField()


class ThreadQuerySerializer(serializers.Serializer):
    dataset = serializers.UUIDField()


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
    """The conversation about a dataset. Changes are announced on the dataset's event
    stream; a page reads them here."""

    def _thread(self, pk) -> AssistantThread:
        return turn.settle(self.resolve(AssistantThread.objects.select_related("dataset"), pk))

    def _show(self, thread):
        return AssistantThreadSerializer(thread, context={"request": self.request}).data

    @extend_schema(
        parameters=[ThreadQuerySerializer], responses=AssistantThreadSerializer(many=True)
    )
    def list(self, request):
        """The dataset's conversation, if it has one."""
        query = ThreadQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        found = AssistantThread.objects.filter(
            project__is_active=True,
            project__memberships__user=request.user,
            project_id__in=project_ids_for(request.user, request.auth),
            dataset_id=query.validated_data["dataset"],
        )
        return Response([self._show(turn.settle(thread)) for thread in found])

    @extend_schema(request=OpenThreadSerializer, responses=AssistantThreadSerializer)
    def create(self, request):
        """Open the dataset's conversation."""
        body = OpenThreadSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        dataset = get_object_or_404(
            Dataset,
            pk=body.validated_data["dataset"],
            project__is_active=True,
            project__memberships__user=request.user,
            project_id__in=project_ids_for(request.user, request.auth),
        )
        return Response(self._show(threads.open_thread(dataset)))

    @extend_schema(responses=AssistantThreadSerializer)
    def retrieve(self, request, pk=None):
        return Response(self._show(self._thread(pk)))

    @extend_schema(
        parameters=[EventsQuerySerializer], responses=AssistantEventSerializer(many=True)
    )
    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        """The events after a cursor, as the Console shows them."""
        thread = self._thread(pk)
        query = EventsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        found = thread.events.filter(sequence__gt=query.validated_data["after"])
        return Response(AssistantEventSerializer(found, many=True).data)

    @extend_schema(request=SendMessageSerializer, responses={202: AssistantInputSerializer})
    @action(detail=True, methods=["post"])
    def messages(self, request, pk=None):
        thread = self._thread(pk)
        body = SendMessageSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        with transaction.atomic():
            try:
                message = threads.send(thread, request.user, **body.validated_data)
            except InputValidationError as exc:
                raise ValidationError(exc.detail) from None
            turn.wake(thread)
        return Response(AssistantInputSerializer(message).data, status=202)

    @extend_schema(request=ChangeMessageSerializer, responses=AssistantInputSerializer)
    @action(detail=True, methods=["post"])
    def queue(self, request, pk=None):
        """Edit, remove, move or send now a message that is still queued."""
        thread = self._thread(pk)
        body = ChangeMessageSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = dict(body.validated_data)
        try:
            message = threads.change(
                values.pop("message"), thread, request.user, action=values.pop("change"), **values
            )
        except InputValidationError as exc:
            raise ValidationError(exc.detail) from None
        return Response(AssistantInputSerializer(message).data)

    @extend_schema(request=None, responses={202: AssistantThreadSerializer})
    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        thread = self._thread(pk)
        turn.stop(thread)
        thread.refresh_from_db()
        return Response(self._show(thread), status=202)

    @extend_schema(responses={(200, "application/octet-stream"): OpenApiTypes.BINARY})
    @action(
        detail=True, methods=["get"], url_path=r"images/(?P<name>[0-9a-f]{64}\.(?:png|jpg|webp))"
    )
    def images(self, request, pk=None, name=None):
        """An image the assistant presented in this conversation."""
        path = tools.image_path(self.resolve(AssistantThread, pk), name)
        if not path.is_file():
            raise NotFound
        return FileResponse(path.open("rb"), content_type=mimetypes.guess_type(name)[0])
