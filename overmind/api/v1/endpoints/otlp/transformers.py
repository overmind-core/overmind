import logging
import json
import zlib
from typing import Any
from collections.abc import Callable
from fastapi import Request
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from overmind.models.traces import ConversationModel, SpanModel, TraceModel
from datetime import datetime, timezone
from overmind.tasks.agentic_span_processor import (
    detect_agentic_span,
    _get_tools_from_metadata_attributes,
)
from overmind.utils import calculate_llm_usage_cost, _safe_int

logger = logging.getLogger(__name__)


def _extract_tool_calls_from_span_attributes(
    span_attributes: dict, prefix: str
) -> list:
    """Extract tool_calls for a single message/completion from span attributes."""
    tool_calls = []
    j = 0
    while True:
        tc_prefix = f"{prefix}.tool_calls.{j}"
        tc_name_key = f"{tc_prefix}.name"
        if tc_name_key not in span_attributes:
            break
        tool_calls.append(
            {
                "id": span_attributes.pop(f"{tc_prefix}.id", ""),
                "function": {
                    "name": span_attributes.pop(tc_name_key),
                    "arguments": span_attributes.pop(f"{tc_prefix}.arguments", ""),
                },
                "type": "function",
            }
        )
        j += 1
    return tool_calls


def get_backend_or_langchain_custom_attributes(span_attributes: dict):
    if "gen_ai.prompt.0.role" in span_attributes:
        combined_input = []
        i = 0
        while True:
            key_prefix = f"gen_ai.prompt.{i}"
            role_key = f"{key_prefix}.role"
            if role_key not in span_attributes:
                break
            entry = {
                "role": span_attributes.pop(role_key),
                "content": span_attributes.pop(f"{key_prefix}.content", None),
            }
            tool_call_id = span_attributes.pop(f"{key_prefix}.tool_call_id", None)
            if tool_call_id:
                entry["tool_call_id"] = tool_call_id
            tool_calls = _extract_tool_calls_from_span_attributes(
                span_attributes, key_prefix
            )
            if tool_calls:
                entry["tool_calls"] = tool_calls
            combined_input.append(entry)
            i += 1

        combined_output = []
        i = 0
        while True:
            key_prefix = f"gen_ai.completion.{i}"
            role_key = f"{key_prefix}.role"
            if role_key not in span_attributes:
                break
            entry = {
                "role": span_attributes.pop(role_key),
                "content": span_attributes.pop(f"{key_prefix}.content", None),
            }
            tool_calls = _extract_tool_calls_from_span_attributes(
                span_attributes, key_prefix
            )
            if tool_calls:
                entry["tool_calls"] = tool_calls
            combined_output.append(entry)
            i += 1

        custom_attributes = {
            "Inputs": json.dumps(combined_input),
            "Outputs": json.dumps(combined_output),
            "PolicyOutcome": span_attributes.pop("policy_outcome", ""),
        }
        return custom_attributes

    return {
        "Inputs": span_attributes.pop("inputs", ""),
        "Outputs": span_attributes.pop("outputs", ""),
        "PolicyOutcome": span_attributes.pop("policy_outcome", ""),
    }


def transform_spans(
    export_request: trace_service_pb2.ExportTraceServiceRequest,
    project_id: str | None,
    business_id: str | None,
    user_id: str | None,
    custom_attributes_extractor: Callable,
):
    """
    Transforms OTLP trace data into a list of dictionaries
    that can be inserted into PostgreSQL.
    """
    spans_to_insert = []

    # The request contains a list of ResourceSpans
    for resource_span in export_request.resource_spans:
        resource_attributes = {
            attr.key: str(
                attr.value.string_value
                or attr.value.int_value
                or attr.value.double_value
                or attr.value.bool_value
            )
            for attr in resource_span.resource.attributes
        }

        # Each ResourceSpan contains a list of ScopeSpans
        for scope_span in resource_span.scope_spans:
            scope = scope_span.scope

            # Each ScopeSpan contains a list of Spans
            for span in scope_span.spans:
                # Flatten attributes into a simple string-string map
                span_attributes = {
                    attr.key: str(
                        attr.value.string_value
                        or attr.value.int_value
                        or attr.value.double_value
                        or attr.value.bool_value
                    )
                    for attr in span.attributes
                }

                span_project_id = (
                    project_id or span_attributes.pop("project_id", None) or None
                )
                span_business_id = (
                    business_id or span_attributes.pop("business_id", None) or None
                )
                span_user_id = user_id or span_attributes.pop("user_id", None) or None

                # Serialize events and links to a simple string array
                events = [f"{evt.time_unix_nano} - {evt.name}" for evt in span.events]
                links = [
                    f"{link.trace_id.hex()}-{link.span_id.hex()}" for link in span.links
                ]

                # Create the flattened dictionary for insertion
                flat_span = {
                    "ProjectId": span_project_id,
                    "BusinessId": span_business_id,
                    "UserId": span_user_id,
                    "TraceId": span.trace_id.hex(),
                    "SpanId": span.span_id.hex(),
                    "ParentSpanId": span.parent_span_id.hex()
                    if span.parent_span_id
                    else None,
                    "TraceState": str(span.trace_state),
                    "Name": span.name,
                    "Kind": span.kind,
                    "StartTimeUnixNano": span.start_time_unix_nano,
                    "EndTimeUnixNano": span.end_time_unix_nano,
                    "DurationNano": span.end_time_unix_nano - span.start_time_unix_nano,
                    "StatusCode": span.status.code,
                    "StatusMessage": span.status.message,
                    "ResourceAttributes": resource_attributes,
                    "ScopeName": scope.name,
                    "ScopeVersion": scope.version,
                    "Events": events,
                    "Links": links,
                    **custom_attributes_extractor(span_attributes),
                    "SpanAttributes": span_attributes,
                }
                spans_to_insert.append(flat_span)

    return spans_to_insert


async def create_trace(
    request: Request,
    project_id: str | None,
    business_id: str | None,
    user_id: str | None,
    db: AsyncSession,
):
    """
    Endpoint to receive OTLP traces over HTTP/protobuf.
    """

    body = await request.body()

    # The OTel SDK might send data with gzip compression
    content_encoding = request.headers.get("content-encoding", "")
    if "gzip" in content_encoding:
        body = zlib.decompress(body, 16 + zlib.MAX_WBITS)

    trace_request = trace_service_pb2.ExportTraceServiceRequest()
    trace_request.ParseFromString(body)

    # Transform the data for our database schema
    spans_data = transform_spans(
        export_request=trace_request,
        project_id=project_id,
        business_id=business_id,
        custom_attributes_extractor=get_backend_or_langchain_custom_attributes,
        user_id=user_id,
    )

    if not spans_data:
        logger.info("Received an empty trace request. No data to insert.")
        return {"message": "Empty request, nothing to process."}

    trace_models, span_models, conversation = tranform_spans_for_postgres(
        spans_data=spans_data
    )

    if conversation:
        stmt = select(ConversationModel).where(
            ConversationModel.conversation_id == conversation.conversation_id
        )
        result = await db.execute(stmt)
        if not result.scalar_one_or_none():
            db.add(conversation)
            await db.flush()
        logger.debug("Successfully inserted conversation")

    db.add_all(trace_models)
    await db.flush()
    logger.debug("Successfully inserted trace")

    if span_models:
        db.add_all(span_models)
    await db.flush()
    await db.commit()
    logger.info(f"Successfully inserted {len(spans_data)} spans")

    return {"message": f"Successfully processed {len(spans_data)} spans."}


def _detect_response_type(output_data: Any) -> str | None:
    """
    Determine the response_type of an LLM span from its output.

    Returns "tool_calls" if the model responded with tool calls,
    "text" if it gave a plain text answer, or None if undetermined.
    """
    if not output_data:
        return None

    if isinstance(output_data, str):
        try:
            output_data = json.loads(output_data)
        except (json.JSONDecodeError, ValueError):
            return "text"

    if isinstance(output_data, dict):
        if output_data.get("tool_calls") or output_data.get("function_call"):
            return "tool_calls"
        if output_data.get("content") is not None:
            return "text"
        return None

    if isinstance(output_data, list):
        for msg in output_data:
            if isinstance(msg, dict):
                if msg.get("tool_calls") or msg.get("function_call"):
                    return "tool_calls"
                if msg.get("role") == "assistant" and msg.get("content") is not None:
                    return "text"

    return None


def _extract_tool_call_metadata(output_data: Any) -> dict[str, Any]:
    """
    Extract tool call count and unique tool names from output data.

    Returns a dict with tool_calls_count (int) and tools_called (list[str]).
    """
    if not output_data:
        return {"tool_calls_count": 0, "tools_called": []}

    if isinstance(output_data, str):
        try:
            output_data = json.loads(output_data)
        except (json.JSONDecodeError, ValueError):
            return {"tool_calls_count": 0, "tools_called": []}

    raw_tool_calls: list[Any] = []
    if isinstance(output_data, dict):
        if output_data.get("tool_calls"):
            raw_tool_calls = output_data["tool_calls"]
        elif output_data.get("function_call"):
            # Legacy OpenAI single-function format
            raw_tool_calls = [output_data["function_call"]]
    elif isinstance(output_data, list):
        for msg in output_data:
            if not isinstance(msg, dict):
                continue
            if msg.get("tool_calls"):
                raw_tool_calls = msg["tool_calls"]
                break
            if msg.get("function_call"):
                raw_tool_calls = [msg["function_call"]]
                break

    tools_called: list[str] = []
    for tc in raw_tool_calls:
        if not isinstance(tc, dict):
            continue
        name = ""
        if isinstance(tc.get("function"), dict):
            name = tc["function"].get("name", "")
        elif tc.get("name"):
            name = tc["name"]
        if name and name not in tools_called:
            tools_called.append(name)

    return {
        "tool_calls_count": len(raw_tool_calls),
        "tools_called": tools_called,
    }


def tranform_spans_for_postgres(
    spans_data: list[dict],
) -> tuple[list[TraceModel], list[SpanModel], ConversationModel | None]:
    """
    Transforms OTLP trace data into SQLAlchemy TraceModel and SpanModel instances
    suitable for insertion into PostgreSQL.
    If conversation_id is provided, it is attached to the TraceModel.
    Returns (trace_model, [span_models]) tuple for a single trace and its spans.
    NOTE: Only one trace per export_request is expected, with many spans along with it
    """
    trace_models: list[TraceModel] = []
    span_models: list[SpanModel] = []
    conversation_model: ConversationModel | None = None

    for span in spans_data:
        # Compose SpanModel (SQLAlchemy)
        span_attributes: dict = span.get("SpanAttributes", {})
        span_inputs = span.get("Inputs", {})
        span_outputs = span.get("Outputs", {})
        span_attributes_for_trace = dict(span_attributes)

        # both for chat and backend spans, chat is not really used, just keeping it in here
        keys = [
            "ToolName",
            "PolicyOutcome",
            "PageTitle",
            "TraceState",
            "Kind",
            "StatusMessage",
            "ScopeName",
            "ScopeVersion",
        ]
        custom_attributes = {
            key: span.pop(key, None) for key in keys if span.get(key, None)
        }

        # Detect if this is an agentic span
        is_agentic = detect_agentic_span(
            input_data=span_inputs,
            output_data=span_outputs,
            metadata={**span_attributes, **custom_attributes},
        )

        # Detect response_type for LLM spans (tool_calls vs text)
        response_type = _detect_response_type(span_outputs)
        if response_type == "tool_calls":
            is_agentic = True  # tool-calling spans are always agentic

        tool_type_metadata: dict[str, Any] = {}
        if response_type:
            tool_type_metadata["response_type"] = response_type
            if response_type == "tool_calls":
                tool_meta = _extract_tool_call_metadata(span_outputs)
                tool_type_metadata["tool_calls_count"] = tool_meta["tool_calls_count"]
                tool_type_metadata["tools_called"] = tool_meta["tools_called"]
                available_tools = _get_tools_from_metadata_attributes(span_attributes)
                if available_tools:
                    tool_type_metadata["available_tools"] = available_tools

        # Compute and store cost at ingest time so it can be read back directly
        cost = calculate_llm_usage_cost(
            str(span_attributes.get("gen_ai.request.model", "") or ""),
            _safe_int(span_attributes.get("gen_ai.usage.input_tokens", 0)),
            _safe_int(span_attributes.get("gen_ai.usage.output_tokens", 0)),
        )

        # Add agentic flag, response_type, and cost to metadata
        metadata_with_flags = {
            **span_attributes,
            **custom_attributes,
            "is_agentic": is_agentic,
            **tool_type_metadata,
            "cost": cost,
        }

        span_model = SpanModel(
            parent_span_id=span.get("ParentSpanId") or None,
            span_id=span.get("SpanId", None),
            trace_id=span.get("TraceId", None),
            start_time_unix_nano=span.get("StartTimeUnixNano", 0),
            end_time_unix_nano=span.get("EndTimeUnixNano", 0),
            operation=span.get("Name", None),
            input_params=span_attributes.pop("client_call_params", {}),
            output_params=span_attributes.pop("client_init_params", {}),
            input=span_inputs,
            output=span_outputs,
            status_code=span.get("StatusCode", 0),
            metadata_attributes=metadata_with_flags,
            feedback_score={},
            created_at=datetime.now(timezone.utc),
        )
        span_models.append(span_model)

        if not span.get("ParentSpanId"):
            # Compose TraceModel (SQLAlchemy)
            # Only create trace if required fields are present
            span_project_id = span.get("ProjectId", None)
            span_user_id = span.get("UserId", None)

            if span_project_id and span_user_id:
                resource_attributes = span.get("ResourceAttributes", {})
                trace_input_params = span_attributes_for_trace.pop(
                    "client_call_params", {}
                )
                trace_output_params = span_attributes_for_trace.pop(
                    "client_init_params", {}
                )
                trace_model = TraceModel(
                    project_id=span_project_id,
                    user_id=span_user_id,
                    trace_id=span.get("TraceId", None),
                    conversation_id=span.get("ConversationId", None),
                    application_name=span.get("Name", None),
                    source=span.get("ScopeName", ""),
                    version=span.get("ScopeVersion", ""),
                    start_time_unix_nano=span.get("StartTimeUnixNano", 0),
                    end_time_unix_nano=span.get("EndTimeUnixNano", 0),
                    status_code=span.get("StatusCode", 0),
                    input_params=trace_input_params,
                    output_params=trace_output_params,
                    input=span_inputs,
                    output=span_outputs,
                    metadata_attributes={
                        **resource_attributes,
                        **custom_attributes,
                        "events": span.get("Events", []),
                        "links": span.get("Links", []),
                    },
                    created_at=datetime.now(timezone.utc),
                    feedback_score={},
                )
                trace_models.append(trace_model)

                if span.get("ConversationId", None):
                    conversation_model = ConversationModel(
                        conversation_id=span.get("ConversationId", None),
                        project_id=span_project_id,
                        user_id=span_user_id,
                        created_at=datetime.now(timezone.utc),
                    )

    return trace_models, span_models, conversation_model
