"""
OTLP/HTTP trace ingest, protobuf or JSON (optionally gzipped). Each exported span becomes
a Span row for the scoring queues.

Spans arrive in any order and in any batch, so ingest only stores them: it never waits
for a parent or a trace root. Re-exporting a span refreshes what OpenTelemetry sent and
keeps what the queues wrote (eval_attributes, in_use, processing_pipeline).
"""

import base64
import hmac
import json
import os
import zlib
from datetime import UTC, datetime

from django.apps import apps
from google.protobuf import json_format
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.trace.v1.trace_pb2 import Status
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ParseError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

# OTel marks "no parent" with eight zero bytes.
EMPTY_PARENT_SPAN_ID = b"\x00" * 8
UPSERT_FIELDS = [
    "parent_id",
    "trace_id",
    "kind",
    "name",
    "start",
    "status",
    "conversation_id",
    "session_id",
    "attributes",
]


class HasTracesKey(BasePermission):
    def has_permission(self, request, view) -> bool:
        key = os.environ.get("INNERMIND_TRACES_KEY", "")
        authorization = request.headers.get("Authorization", "")
        return bool(key) and hmac.compare_digest(authorization, f"Bearer {key}")


class IngestResultSerializer(serializers.Serializer):
    spans_ingested = serializers.IntegerField()


def any_value(value):
    match value.WhichOneof("value"):
        case "string_value":
            return value.string_value
        case "int_value":
            return value.int_value
        case "double_value":
            return value.double_value
        case "bool_value":
            return value.bool_value
        case "array_value":
            return [any_value(item) for item in value.array_value.values]
        case "kvlist_value":
            return {kv.key: any_value(kv.value) for kv in value.kvlist_value.values}
        case "bytes_value":
            return value.bytes_value.hex()
    return None


def span_fields(proto) -> dict:
    attributes = {kv.key: any_value(kv.value) for kv in proto.attributes}
    failed = proto.status.code == Status.STATUS_CODE_ERROR
    if failed and proto.status.message:
        attributes.setdefault("overmind.error", proto.status.message)
    parent = proto.parent_span_id
    start = datetime.fromtimestamp(proto.start_time_unix_nano / 1e9, UTC)
    return {
        "span_id": proto.span_id.hex(),
        "parent_id": parent.hex() if parent and parent != EMPTY_PARENT_SPAN_ID else None,
        "trace_id": proto.trace_id.hex(),
        "kind": str(attributes.get("overmind.kind") or "span")[:32],
        "name": proto.name[:255],
        # Fixed precision keeps "start" sortable as text.
        "start": start.isoformat(timespec="microseconds"),
        "status": "error" if failed else "ok",
        "conversation_id": str(attributes.get("overmind.conversation_id", ""))[:64],
        "session_id": str(attributes.get("overmind.session_id", ""))[:64],
        "attributes": attributes,
    }


def parse_protobuf(body: bytes) -> trace_service_pb2.ExportTraceServiceRequest:
    export = trace_service_pb2.ExportTraceServiceRequest()
    try:
        export.ParseFromString(body)
    except DecodeError as exc:
        raise ParseError(f"Invalid OTLP protobuf: {exc}") from exc
    return export


def parse_json(body: bytes) -> trace_service_pb2.ExportTraceServiceRequest:
    try:
        payload = json.loads(body)
        # OTLP/JSON writes ids as hex; the protobuf JSON mapping expects base64.
        for resource_spans in payload.get("resourceSpans", []):
            for scope_spans in resource_spans.get("scopeSpans", []):
                for span in scope_spans.get("spans", []):
                    for item in (span, *span.get("links", [])):
                        for field in ("traceId", "spanId", "parentSpanId"):
                            if item.get(field):
                                item[field] = base64.b64encode(bytes.fromhex(item[field])).decode()
        return json_format.ParseDict(
            payload, trace_service_pb2.ExportTraceServiceRequest(), ignore_unknown_fields=True
        )
    except (ValueError, AttributeError, TypeError, json_format.ParseError) as exc:
        raise ParseError(f"Invalid OTLP JSON: {exc}") from exc


@api_view(["POST"])
@permission_classes([HasTracesKey])
def otlp_traces(request):
    body = request.body
    if "gzip" in request.headers.get("Content-Encoding", ""):
        try:
            body = zlib.decompress(body, 16 + zlib.MAX_WBITS)
        except zlib.error as exc:
            raise ParseError(f"Invalid gzip body: {exc}") from exc

    is_json = request.content_type.startswith("application/json")
    export = parse_json(body) if is_json else parse_protobuf(body)

    span_model = apps.get_model("innermind", "Span")
    spans = [
        span_model(**span_fields(proto))
        for resource_spans in export.resource_spans
        for scope_spans in resource_spans.scope_spans
        for proto in scope_spans.spans
    ]
    if spans:
        span_model.objects.bulk_create(
            spans, update_conflicts=True, unique_fields=["span_id"], update_fields=UPSERT_FIELDS
        )
    return Response(IngestResultSerializer({"spans_ingested": len(spans)}).data)
