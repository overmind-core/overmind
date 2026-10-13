from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from overbae.api.filters import SignalEventFilter
from overbae.api.signal_serializers import (
    SignalCatalogEntrySerializer,
    SignalEventSeenResponseSerializer,
    SignalEventSeenSerializer,
    SignalEventSerializer,
    SignalSubscriptionSerializer,
)
from overbae.api.views import _user_project_ids
from overbae.innermind.signals import signal_factory
from overbae.models import Project, SignalEvent, SignalSubscription


@extend_schema_view(
    list=extend_schema(summary="List signal subscriptions"),
    retrieve=extend_schema(summary="Get a signal subscription"),
    create=extend_schema(summary="Subscribe to a signal"),
    partial_update=extend_schema(summary="Enable, disable or configure a subscription"),
    destroy=extend_schema(summary="Unsubscribe from a signal"),
)
class SignalSubscriptionViewSet(viewsets.ModelViewSet):
    serializer_class = SignalSubscriptionSerializer
    filterset_fields = ["project", "capability", "signal_type", "enabled"]
    lookup_field = "id"
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SignalSubscription.objects.none()
        return SignalSubscription.objects.filter(
            project_id__in=_user_project_ids(self.request.user, self.request.auth)
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @extend_schema(
        summary="List the available signals and their parameters",
        responses=SignalCatalogEntrySerializer(many=True),
    )
    @action(detail=False, methods=["get"], pagination_class=None, filter_backends=[])
    def catalog(self, request):
        return Response(SignalCatalogEntrySerializer(signal_factory.catalog(), many=True).data)


@extend_schema_view(list=extend_schema(summary="List signal events, newest first"))
class SignalEventViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = SignalEventSerializer
    filterset_class = SignalEventFilter

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SignalEvent.objects.none()
        return SignalEvent.objects.filter(
            project_id__in=_user_project_ids(self.request.user, self.request.auth)
        ).select_related("capability")

    @extend_schema(
        summary="Mark signal events seen",
        description="Marks the listed events, or every unseen event in the project when `ids` is omitted.",
        request=SignalEventSeenSerializer,
        responses=SignalEventSeenResponseSerializer,
    )
    @action(detail=False, methods=["post"], url_path="mark-seen")
    def mark_seen(self, request):
        serializer = SignalEventSeenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project_id = serializer.validated_data["project"]
        allowed = _user_project_ids(request.user, request.auth)
        if not Project.objects.filter(id=project_id, id__in=allowed).exists():
            raise PermissionDenied("You are not a member of this project.")
        events = SignalEvent.objects.filter(project_id=project_id, seen_at__isnull=True)
        if "ids" in serializer.validated_data:
            events = events.filter(id__in=serializer.validated_data["ids"])
        updated = events.update(seen_at=timezone.now())
        return Response(SignalEventSeenResponseSerializer({"updated": updated}).data)
