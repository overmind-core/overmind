"""The capability surface: ``/api/capabilities/`` and the project's agent graph
at ``/api/agent/``. DELETE hides a capability; nothing here drops a row."""

from __future__ import annotations

from django.db.models import Count
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.filters import CapabilityFilter
from overbae.api.scoping import project_ids_for
from overbae.api.serializers import (
    CapabilityListSerializer,
    CapabilitySerializer,
    PromptSerializer,
)
from overbae.models import Capability
from overbae.services.capabilities import graph, identity
from overbae.services.capability_prompts import sync_capability_prompts


class EvalSpecResponseSerializer(serializers.Serializer):
    capability_description = serializers.CharField()
    source_path = serializers.CharField()
    input_schema = serializers.JSONField()
    output_fields = serializers.JSONField()
    structure_weight = serializers.FloatField()
    total_points = serializers.FloatField()
    tool_config = serializers.JSONField()
    tool_usage_weight = serializers.FloatField()
    consistency_rules = serializers.JSONField()
    optimizable_elements = serializers.JSONField()
    fixed_elements = serializers.JSONField()


class GraphEdgeSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["invokes", "shares_tool"])
    source = serializers.CharField()
    target = serializers.CharField()
    source_name = serializers.CharField(allow_blank=True, default="")
    target_name = serializers.CharField(allow_blank=True, default="")
    via = serializers.ListField(child=serializers.CharField(), default=list)
    tools = serializers.ListField(child=serializers.CharField(), default=list)
    observed = serializers.IntegerField(default=0)


class AgentGraphSerializer(serializers.Serializer):
    """The project's agent: its current capabilities and the evidence edges between them."""

    project = serializers.CharField()
    capabilities = CapabilityListSerializer(many=True)
    edges = GraphEdgeSerializer(many=True)


def _capabilities_for(request):
    return Capability.objects.filter(
        project_id__in=project_ids_for(request.user, request.auth)
    ).exclude(status=Capability.Status.DELETED)


def _annotated(qs):
    return qs.annotate(trace_count=Count("spans__trace_id", distinct=True))


@extend_schema_view(
    list=extend_schema(
        summary="List capabilities",
        description="Current capabilities by default; pass `status=leftover` for rows the latest scan did not reproduce.",
    ),
    retrieve=extend_schema(summary="Get capability"),
    create=extend_schema(summary="Create capability"),
    partial_update=extend_schema(summary="Update capability"),
    destroy=extend_schema(
        summary="Delete capability",
        description="Hides the capability from lists, scans, and identity lookup. Its traces, datasets, and runs stay.",
    ),
)
class CapabilityViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Capability.objects.all()
    filterset_class = CapabilityFilter
    search_fields = ["name", "slug", "description", "source_path"]
    ordering_fields = ["name", "created_at", "total_points"]
    lookup_field = "id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "list":
            return CapabilityListSerializer
        return CapabilitySerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Capability.objects.none()
        qs = _capabilities_for(self.request)
        if self.action == "list" and "status" not in self.request.query_params:
            qs = qs.filter(status=Capability.Status.CURRENT)
        return _annotated(qs).order_by("-created_at")

    def perform_create(self, serializer):
        # A hand-made capability is runtime-observed: no scan can see it, so no
        # scan may retire it.
        slug = (serializer.validated_data.get("slug") or "").strip()
        if not slug:
            slug = identity.unique_slug(
                serializer.validated_data["project"].id, serializer.validated_data.get("name", "")
            )
        capability = serializer.save(observed=True, slug=slug)
        identity.enqueue_rebind(capability.project_id)

    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        instance.set_status(Capability.Status.DELETED)

    @extend_schema(summary="Get capability eval spec", responses={200: EvalSpecResponseSerializer})
    @action(detail=True, methods=["get"])
    def eval_spec(self, request, id=None):
        capability = self.get_object()
        return Response(
            EvalSpecResponseSerializer(
                {
                    "capability_description": capability.description,
                    "source_path": capability.source_path,
                    "input_schema": capability.input_schema,
                    "output_fields": capability.output_fields,
                    "structure_weight": capability.structure_weight,
                    "total_points": capability.total_points,
                    "tool_config": capability.tool_config,
                    "tool_usage_weight": capability.tool_usage_weight,
                    "consistency_rules": capability.consistency_rules,
                    "optimizable_elements": capability.optimizable_elements,
                    "fixed_elements": capability.fixed_elements,
                }
            ).data
        )

    @extend_schema(
        summary="List the capability's selectable system prompts",
        description=(
            "The capability's selectable prompts with full `system_prompt` text: "
            "versioned `Prompt` snapshots plus the flow-derived prompts from the "
            "capability card, materialized into `Prompt` rows so a selection "
            "persists for an eval run."
        ),
        responses={200: PromptSerializer(many=True)},
    )
    @action(detail=True, methods=["get"])
    def prompts(self, request, id=None):
        prompts = sync_capability_prompts(self.get_object())
        page = self.paginate_queryset(prompts)
        if page is not None:
            return self.get_paginated_response(PromptSerializer(page, many=True).data)
        return Response(PromptSerializer(prompts, many=True).data)


class AgentGraphView(APIView):
    """One request draws the agent page: current capabilities, evidence edges
    weighted by observed runtime transitions, and the latest scan."""

    @extend_schema(
        summary="Get the project's agent graph",
        parameters=[
            OpenApiParameter(
                name="project", location=OpenApiParameter.QUERY, type=str, required=True
            )
        ],
        responses={200: AgentGraphSerializer},
    )
    def get(self, request):
        project_id = str(request.query_params.get("project") or "")
        if project_id not in {str(p) for p in project_ids_for(request.user, request.auth)}:
            return Response({"detail": "Not found."}, status=404)
        current = list(
            _annotated(Capability.objects.filter(project_id=project_id).current()).order_by(
                "-created_at"
            )
        )
        payload = {
            "project": project_id,
            "capabilities": current,
            "edges": graph.weighted_edges(project_id, []),
        }
        return Response(AgentGraphSerializer(payload).data)
