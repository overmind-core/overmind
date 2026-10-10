"""Every exporter dialect lands as the same native span through the public OTLP endpoint."""

import uuid

import pytest
from factories import ingest_spans, make_capability, make_project

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
