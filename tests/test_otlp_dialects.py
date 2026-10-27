"""Every exporter dialect lands as the same native span through the public OTLP endpoint."""

import json
import uuid

import pytest
from factories import (
    api_key_client,
    ingest_spans,
    make_capability,
    make_member,
    make_project,
    make_user,
)

from overbae.models import Span

pytestmark = pytest.mark.django_db


def _ingested(name: str, attributes: dict) -> Span:
    project = make_project()
    span_id = uuid.uuid4().hex[:16]
    ingest_spans(project, [{"name": name, "attributes": attributes, "span_id": span_id}])
    return Span.objects.get(project=project, span_id=span_id)


@pytest.mark.parametrize(
    ("name", "attrs", "expected"),
    [
        ("llm.chat", {}, Span.SpanType.LLM_CALL),
        ("tool_call.search", {}, Span.SpanType.TOOL_CALL),
        ("function call", {}, Span.SpanType.LLM_CALL),
        ("assistant", {"gen_ai.operation.name": "execute_tool"}, Span.SpanType.TOOL_CALL),
        ("assistant", {"overmind.span.type": "tool_call"}, Span.SpanType.TOOL_CALL),
        ("assistant", {"overmind.span.type": "llm_call"}, Span.SpanType.LLM_CALL),
        ("assistant", {"overmind.span_type": "tool_call"}, Span.SpanType.TOOL_CALL),
        ("assistant", {"type": "tool_call"}, Span.SpanType.TOOL_CALL),
        ("SearchAPIRetriever", {"openinference.span.kind": "RETRIEVER"}, "retrieval"),
        ("duckduckgo_search", {"openinference.span.kind": "TOOL"}, Span.SpanType.TOOL_CALL),
        ("ChatOpenAI", {"openinference.span.kind": "LLM"}, Span.SpanType.LLM_CALL),
        ("AgentExecutor", {"openinference.span.kind": "CHAIN"}, "workflow"),
        (
            "assistant",
            {"overmind.span.type": "retrieval", "openinference.span.kind": "LLM"},
            "retrieval",
        ),
    ],
)
def test_each_dialect_lands_as_its_native_span_type(name, attrs, expected):
    assert _ingested(name, attrs).span_type == expected


@pytest.mark.parametrize(
    ("name", "attrs", "operation"),
    [
        ("fallback-name", {"gen_ai.operation.name": "execute_tool"}, "execute_tool"),
        ("fallback-name", {"operation": "custom.op"}, "custom.op"),
        ("chat.completion", {}, "chat.completion"),
    ],
)
def test_the_operation_prefers_gen_ai_then_operation_then_the_span_name(name, attrs, operation):
    assert _ingested(name, attrs).operation == operation


@pytest.mark.parametrize(
    ("attrs", "cost"),
    [
        ({"genai.model": "gpt-5-mini"}, "derived"),
        ({"genai.model": "gpt-5-mini", "genai.cost": 9.99}, 9.99),
        ({"genai.model": "totally-made-up-model-xyz"}, None),
    ],
    ids=["derived from tokens", "client cost wins", "unknown model"],
)
def test_a_capability_accumulates_derived_or_reported_cost(attrs, cost):
    project = make_project()
    capability = make_capability(project)
    ingest_spans(
        project,
        [
            {
                "name": "chat",
                "attributes": {
                    "overmind.capability.id": str(capability.id),
                    "genai.prompt_tokens": 1000,
                    "genai.completion_tokens": 500,
                    **attrs,
                },
            }
        ],
    )
    capability.refresh_from_db()
    stats = capability.usage_stats
    assert stats["prompt_tokens"] == 1000
    if cost == "derived":
        assert type(stats["cost_usd"]) is float and stats["cost_usd"] > 0
    elif cost is None:
        assert not stats.get("cost_usd")
    else:
        assert stats["cost_usd"] == cost


def test_an_otlp_json_export_lands_like_protobuf():
    project = make_project()
    owner = make_user()
    make_member(owner, project)
    trace_id, root_id, child_id = uuid.uuid4().hex, uuid.uuid4().hex[:16], uuid.uuid4().hex[:16]
    # OTLP/JSON: hex ids, integer enums, 64-bit integers as strings, unknown fields.
    export = {
        "resourceSpans": [
            {
                "extension": {"spanId": "opaque"},
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "web"}}]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": root_id,
                                "parentSpanId": "",
                                "name": "agent",
                                "kind": 2,
                                "startTimeUnixNano": "1000",
                                "endTimeUnixNano": "9000",
                            },
                            {
                                "traceId": trace_id,
                                "spanId": child_id,
                                "parentSpanId": root_id,
                                "name": "chat",
                                "startTimeUnixNano": "2000",
                                "endTimeUnixNano": "8000",
                                "attributes": [
                                    {"key": "gen_ai.request.model", "value": {"stringValue": "m"}},
                                    {
                                        "key": "gen_ai.usage.input_tokens",
                                        "value": {"intValue": "12"},
                                    },
                                ],
                            },
                        ]
                    }
                ],
            }
        ]
    }

    response = api_key_client(owner, project).post(
        "/api/v1/traces", data=json.dumps(export), content_type="application/json"
    )

    assert response.status_code == 200, response.content
    root = Span.objects.get(project=project, span_id=root_id)
    child = Span.objects.get(project=project, span_id=child_id)
    assert (root.trace_id, root.parent_span_id, root.service_name) == (trace_id, None, "web")
    assert (child.parent_span_id, child.duration_ns) == (root_id, 6000)
    assert child.attributes["gen_ai.usage.input_tokens"] == 12


@pytest.mark.parametrize(
    ("attributes", "input_attribute", "output_attribute"),
    [
        (
            {"gen_ai.input.messages": "[]", "gen_ai.output.messages": "[]"},
            "gen_ai.input.messages",
            "gen_ai.output.messages",
        ),
        (
            {"gen_ai.tool.call.arguments": '{"q": "w"}', "gen_ai.tool.call.result": "Result"},
            "gen_ai.tool.call.arguments",
            "gen_ai.tool.call.result",
        ),
        (
            {"gen_ai.tool.call.arguments": '{"q": "w"}', "anthropic.tool_result.content": "[]"},
            "gen_ai.tool.call.arguments",
            "anthropic.tool_result.content",
        ),
        (
            {"gen_ai.prompt": '{"messages": []}', "gen_ai.completion": '{"completion": "A"}'},
            "gen_ai.prompt",
            "gen_ai.completion",
        ),
        ({"user_prompt": "Who won?"}, "user_prompt", None),
        (
            {"tool_input": '{"query": "w"}', "response.model_output": "Team A."},
            "tool_input",
            "response.model_output",
        ),
        (
            {"overmind.input.data": "q", "gen_ai.input.messages": "[]"},
            "overmind.input.data",
            None,
        ),
    ],
    ids=[
        "genai messages (Claude API)",
        "genai tool span",
        "Claude API tool span",
        "single-attribute prompt (OpenRouter)",
        "Claude Code prompt",
        "Claude Code detailed",
        "native payload wins",
    ],
)
def test_a_span_names_the_attributes_holding_its_input_and_output(
    attributes, input_attribute, output_attribute
):
    project = make_project()
    viewer = make_user()
    make_member(viewer, project)
    trace_id = uuid.uuid4().hex
    ingest_spans(project, [{"name": "span", "trace_id": trace_id, "attributes": attributes}])

    (span,) = api_key_client(viewer, project).get(f"/api/traces/{trace_id}/").json()["spans"]

    assert (span["input_attribute"], span["output_attribute"]) == (
        input_attribute,
        output_attribute,
    )
