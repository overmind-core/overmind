"""Model library for the marketing site. Deliberately unauthenticated and
unscoped — architecture, tiers and benchmark scores are not sensitive."""

from __future__ import annotations

from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.serializers import PublicModelListResponseSerializer, PublicModelSerializer
from overbae.services.model_library import (
    get_public_model,
    public_library_filters,
    public_model_catalog,
)


class PublicModelLibraryListView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(
        summary="List the public model library",
        description=(
            "Every enabled model on the Overmind platform, deduped across backends and "
            "sorted by tier then size. Powers the marketing site's /library page — no "
            "authentication required."
        ),
        responses=PublicModelListResponseSerializer,
    )
    def get(self, request):
        serializer = PublicModelListResponseSerializer(
            {
                "models": public_model_catalog(),
                "filters": public_library_filters(),
            }
        )
        return Response(serializer.data)


class PublicModelLibraryDetailView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(
        summary="Get a public model library entry",
        description="A single model's catalog entry by slug — 404 if unknown or disabled.",
        responses=PublicModelSerializer,
    )
    def get(self, request, slug: str):
        model = get_public_model(slug)
        if model is None:
            raise Http404("Unknown model.")
        return Response(PublicModelSerializer(model).data)
