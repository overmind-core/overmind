import asyncio
import json
import logging

import redis.asyncio as aredis
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connections
from django.http import StreamingHttpResponse
from drf_spectacular.utils import extend_schema, extend_schema_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response

from overbae.api.scoping import project_ids_for
from overbae.services import activity

logger = logging.getLogger(__name__)
STREAM_SECONDS = 300


class ActivityAssetSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["dataset", "capability", "evaluation", "training_job"])
    id = serializers.UUIDField()
    label = serializers.CharField()


class ActivitySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    project = serializers.UUIDField(allow_null=True)
    kind = serializers.CharField()
    actor_kind = serializers.CharField()
    actor_name = serializers.CharField()
    action = serializers.CharField()
    status = serializers.CharField()
    assets = ActivityAssetSerializer(many=True)
    task_kind = serializers.CharField(allow_blank=True)
    task_id = serializers.CharField(allow_blank=True)
    sequence_key = serializers.CharField(allow_blank=True)
    seen = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


@extend_schema_serializer(many=False)
class ActivityFeedSerializer(serializers.Serializer):
    results = ActivitySerializer(many=True)
    count = serializers.IntegerField()
    unseen_count = serializers.IntegerField()
    observed_at = serializers.DateTimeField()
    next_offset = serializers.IntegerField(allow_null=True)


class ActivityQuerySerializer(serializers.Serializer):
    project = serializers.UUIDField(required=False)
    offset = serializers.IntegerField(min_value=0, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=25)
    path = serializers.ChoiceField(choices=["agent", "data"], default="agent")


class ActivitySeenSerializer(serializers.Serializer):
    project = serializers.UUIDField(required=False)
    ids = serializers.ListField(child=serializers.UUIDField(), max_length=100)
    through = serializers.DateTimeField()


class ActivitySeenResultSerializer(serializers.Serializer):
    updated = serializers.IntegerField()


class OnboardingMilestoneSerializer(serializers.Serializer):
    actor_kind = serializers.CharField(allow_null=True)
    actor_name = serializers.CharField(allow_null=True)
    kind = serializers.CharField()
    label = serializers.CharField()
    completed_at = serializers.DateTimeField(allow_null=True)
    activity_id = serializers.UUIDField(allow_null=True)
    assets = ActivityAssetSerializer(many=True)


class ActivityEventRenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "sse"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class ActivityViewSet(viewsets.GenericViewSet):
    serializer_class = ActivitySerializer
    filter_backends = []
    pagination_class = None

    def scoped(self, project=None):
        allowed = project_ids_for(self.request.user, self.request.auth)
        if project and str(project) not in {str(pk) for pk in allowed}:
            raise PermissionDenied("You are not a member of this project.")
        scope = getattr(self.request.auth, "scope", {})
        return activity.visible(
            self.request.user,
            allowed,
            project,
            include_account=not isinstance(scope, dict) or scope.get("scope") != "project",
        )

    def get_renderers(self):
        if self.action == "events":
            return [JSONRenderer(), ActivityEventRenderer()]
        return super().get_renderers()

    @extend_schema(
        parameters=[ActivityQuerySerializer], responses=ActivityFeedSerializer(many=False)
    )
    @action(detail=False, methods=["get"])
    def events(self, request):
        query = ActivityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        project = query.validated_data.get("project")
        self.scoped(project)

        def snapshot():
            try:
                scope = self.scoped(project)
                result = activity.feed(scope, limit=100)
                revision = list(scope.values_list("id", "updated_at", "seen")[:100])
                return ActivityFeedSerializer(result).data, revision
            finally:
                connections.close_all()

        channels = [activity.channel(user_id=request.user.pk)]
        if project:
            channels.append(activity.channel(project))

        async def stream():
            await sync_to_async(connections.close_all)()
            loop = asyncio.get_running_loop()
            # The stream ends so the client reconnects and membership is checked again.
            deadline = loop.time() + STREAM_SECONDS
            client = aredis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
            pubsub = client.pubsub()
            previous = None
            try:
                await pubsub.subscribe(*channels)
                stale = True
                while loop.time() < deadline:
                    if stale:
                        data, revision = await sync_to_async(snapshot, thread_sensitive=True)()
                        signature = (revision, data["count"], data["unseen_count"])
                        if signature != previous:
                            yield f"data: {json.dumps(data)}\n\n"
                            previous = signature
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                    stale = message is not None
                    if not stale:
                        yield ": ping\n\n"
                        continue
                    await asyncio.sleep(0.5)
                    while await pubsub.get_message(ignore_subscribe_messages=True, timeout=0):
                        pass
            finally:
                try:
                    await pubsub.unsubscribe()
                    await pubsub.aclose()
                    await client.aclose()
                except Exception:  # noqa: BLE001
                    logger.debug("activity stream teardown failed", exc_info=True)

        response = StreamingHttpResponse(stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    @extend_schema(
        parameters=[ActivityQuerySerializer], responses=ActivityFeedSerializer(many=False)
    )
    def list(self, request):
        query = ActivityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        result = activity.feed(
            self.scoped(values.get("project")).order_by("-created_at", "-id"),
            offset=values["offset"],
            limit=values["limit"],
        )
        return Response(ActivityFeedSerializer(result).data)

    @extend_schema(
        parameters=[ActivityQuerySerializer], responses=OnboardingMilestoneSerializer(many=True)
    )
    @action(detail=False, methods=["get"])
    def onboarding(self, request):
        query = ActivityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        return Response(
            OnboardingMilestoneSerializer(
                activity.milestones(self.scoped(values.get("project")), values["path"]), many=True
            ).data
        )

    @extend_schema(request=ActivitySeenSerializer, responses=ActivitySeenResultSerializer)
    @action(detail=False, methods=["post"], url_path="mark-seen")
    def mark_seen(self, request):
        data = ActivitySeenSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        updated = activity.mark_seen(
            self.scoped(values.get("project")), request.user, values["ids"], values["through"]
        )
        return Response(ActivitySeenResultSerializer({"updated": updated}).data)
