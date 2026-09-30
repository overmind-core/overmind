from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.authentication import GuestJWTAuthentication
from overbae.auth import ClerkAuthentication
from overbae.services.mcp.oauth import consent_request, decide_consent


class ConsentQuerySerializer(serializers.Serializer):
    request = serializers.CharField(min_length=64, max_length=64)


class ConsentDecisionSerializer(ConsentQuerySerializer):
    approve = serializers.BooleanField()


class ConsentDetailsSerializer(serializers.Serializer):
    client_name = serializers.CharField()
    redirect_uri = serializers.CharField()
    scopes = serializers.ListField(child=serializers.CharField())


class ConsentRedirectSerializer(serializers.Serializer):
    redirect_url = serializers.URLField()


class MCPConsentView(APIView):
    authentication_classes = [ClerkAuthentication, GuestJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["mcp-oauth"], parameters=[ConsentQuerySerializer], responses=ConsentDetailsSerializer
    )
    def get(self, request):
        query = ConsentQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        grant = consent_request(query.validated_data["request"], request.user)
        return Response(
            ConsentDetailsSerializer(
                {
                    "client_name": grant.client.metadata.get("client_name") or "MCP client",
                    "redirect_uri": grant.parameters["redirect_uri"],
                    "scopes": grant.scopes,
                }
            ).data,
            headers={"Cache-Control": "no-store"},
        )

    @extend_schema(
        tags=["mcp-oauth"], request=ConsentDecisionSerializer, responses=ConsentRedirectSerializer
    )
    def post(self, request):
        serializer = ConsentDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        redirect = decide_consent(
            serializer.validated_data["request"], request.user, serializer.validated_data["approve"]
        )
        return Response(
            ConsentRedirectSerializer({"redirect_url": redirect}).data,
            headers={"Cache-Control": "no-store"},
        )
