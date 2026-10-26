"""Internal analytics reads for Overwatch's shared Overlord runtime.

POST carries private query arguments in the body, never in access-log URLs.
It performs no product mutations and is deliberately absent from public OpenAPI/MCP.
"""

import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from drf_spectacular.utils import extend_schema
from jsonschema import ValidationError, validate
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.services import overwatch_tools, user_insights

logger = logging.getLogger(__name__)
TOOL_CACHE_SECONDS = 3600
# product_usage keeps its own PostHog cache and still reads product rows live.
CACHED_TOOLS = frozenset(
    {"activation_funnel", "billing_summary", "customer_activity", "customers_at_step"}
)
DAYS = {"type": "integer", "minimum": 1, "maximum": 365}
SCHEMAS = {
    name: {
        "type": "object",
        "properties": {"days": DAYS},
        "required": ["days"],
        "additionalProperties": False,
    }
    for name in ("activation_funnel", "product_usage", "billing_summary")
}
SCHEMAS["customer_activity"] = {
    "type": "object",
    "properties": {"days": DAYS, "email": {"type": "string", "minLength": 3, "maxLength": 254}},
    "required": ["days", "email"],
    "additionalProperties": False,
}
SCHEMAS["customers_at_step"] = {
    "type": "object",
    "properties": {
        "days": DAYS,
        "step": {"enum": list(user_insights.ACTIVATION_STEPS)},
        "limit": {"type": "integer", "minimum": 1, "maximum": 25},
    },
    "required": ["days", "step"],
    "additionalProperties": False,
}


class HasOverwatchServiceToken(BasePermission):
    def has_permission(self, request, view):
        expected = settings.OVERWATCH_SERVICE_TOKEN
        scheme, _, value = request.headers.get("Authorization", "").partition(" ")
        return (
            bool(expected)
            and expected != settings.SALES_DASHBOARD_TOKEN
            and scheme == "Bearer"
            and hmac.compare_digest(value.encode(), expected.encode())
        )


class QuerySerializer(serializers.Serializer):
    tool = serializers.ChoiceField(choices=tuple(SCHEMAS))
    arguments = serializers.JSONField()

    def validate(self, attrs):
        if set(self.initial_data) != {"tool", "arguments"}:
            raise serializers.ValidationError("Invalid query fields.")
        try:
            validate(attrs["arguments"], SCHEMAS[attrs["tool"]])
            if attrs["tool"] == "customer_activity":
                serializers.EmailField().run_validation(attrs["arguments"]["email"])
        except (ValidationError, serializers.ValidationError):
            raise serializers.ValidationError("Invalid query arguments.") from None
        return attrs


def _tool_cache_key(tool: str, arguments: dict) -> str:
    # One snapshot per tool and arguments. Hash the query so a customer email is
    # not a Redis key, and never include the bearer token.
    normalized = dict(arguments)
    if tool == "customer_activity":
        normalized["email"] = normalized["email"].casefold()
    elif tool == "customers_at_step":
        # The handler treats a missing limit as 25; the snapshot must too.
        normalized["limit"] = normalized.get("limit", 25)
    fingerprint = hashlib.sha256(
        json.dumps({"tool": tool, "arguments": normalized}, sort_keys=True).encode()
    ).hexdigest()
    return f"overwatch:agent:query:{fingerprint}"


class QueryResultSerializer(serializers.Serializer):
    content = serializers.CharField()
    private = serializers.BooleanField()
    source = serializers.CharField()
    as_of = serializers.DateTimeField()
    window_days = serializers.IntegerField()
    window_start = serializers.DateTimeField()


class OverwatchQueryView(APIView):
    authentication_classes = []
    permission_classes = [HasOverwatchServiceToken]
    http_method_names = ["post", "options"]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response

    @extend_schema(exclude=True)
    @sensitive_variables()
    def post(self, request):
        if len(request.body) > 4096:
            return Response({"error": "invalid_query"}, status=400)
        query = QuerySerializer(data=request.data)
        if not query.is_valid():
            return Response({"error": "invalid_query"}, status=400)
        tool, args = query.validated_data["tool"], dict(query.validated_data["arguments"])
        try:
            minute = int(timezone.now().timestamp()) // 60
            rate_key = f"overwatch:service:rate:{minute}"
            cache.add(rate_key, 0, timeout=120)
            if cache.incr(rate_key) > 120:
                return Response({"error": "query_limit"}, status=429)
            result_key = _tool_cache_key(tool, args) if tool in CACHED_TOOLS else None
            if result_key is not None:
                cached = cache.get(result_key)
                if cached is not None:
                    return Response(cached)
            # Dedicated top-level transaction: PostgreSQL itself rejects writes,
            # including accidental future ORM writes and data-modifying CTEs.
            with transaction.atomic():
                if connection.vendor != "postgresql":
                    raise RuntimeError("unsupported_analytics_database")
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                content, private = overwatch_tools.query(tool, args)
            result = QueryResultSerializer(
                {
                    "content": content,
                    "private": private,
                    "source": "Overmind product/billing records"
                    + (" and PostHog aggregates" if tool == "product_usage" else ""),
                    "as_of": timezone.now(),
                    "window_days": args["days"],
                    "window_start": user_insights.window_start(args["days"]),
                }
            )
            payload = dict(result.data)
            if result_key is not None:
                cache.set(result_key, payload, TOOL_CACHE_SECONDS)
            return Response(payload)
        except Exception:
            logger.warning("overwatch_service_query_failed")
            return Response({"error": "analytics_unavailable"}, status=503)
