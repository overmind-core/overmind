from types import SimpleNamespace

import pytest

from overbae.api.otlp import (
    _build_span_usage,
    _classify_span_type,
    _operation_from,
)
from overbae.models import Span


def _proto(name: str = "chat") -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _span(attributes: dict, span_type=Span.SpanType.LLM_CALL) -> SimpleNamespace:
    return SimpleNamespace(attributes=attributes, span_type=span_type)


class TestSpanTypeClassification:
    @pytest.mark.parametrize(
        ("name", "attrs", "expected"),
        [
            # Without an explicit Overmind tag the classifier defaults to LLM_CALL,
            # regardless of the span name or any third-party hints.
            ("llm.chat", {}, Span.SpanType.LLM_CALL),
            ("tool_call.search", {}, Span.SpanType.TOOL_CALL),
            ("function call", {}, Span.SpanType.LLM_CALL),
            ("assistant", {"gen_ai.operation.name": "execute_tool"}, Span.SpanType.TOOL_CALL),
            ("assistant", {"overmind.span.type": "tool_call"}, Span.SpanType.TOOL_CALL),
            ("assistant", {"overmind.span.type": "llm_call"}, Span.SpanType.LLM_CALL),
            ("assistant", {"overmind.span_type": "tool_call"}, Span.SpanType.TOOL_CALL),
            ("assistant", {"overmind.span_type": "llm_call"}, Span.SpanType.LLM_CALL),
            ("assistant", {"type": "tool_call"}, Span.SpanType.TOOL_CALL),
            # openinference kinds (langchain, llama-index instrumentors) map to
            # the native taxonomy instead of falling through to llm_call.
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
    def test_classify(self, name, attrs, expected):
        assert _classify_span_type(name, attrs) == expected, (
            f"expected {expected} for {name} with attrs {attrs}"
        )


class TestOperationResolution:
    def test_operation_prefers_gen_ai_attribute(self):
        proto = _proto(name="fallback-name")
        attrs = {"gen_ai.operation.name": "execute_tool"}
        assert _operation_from(proto, attrs) == "execute_tool"

    def test_operation_falls_back_to_operation_attr(self):
        proto = _proto(name="fallback-name")
        attrs = {"operation": "custom.op"}
        assert _operation_from(proto, attrs) == "custom.op"

    def test_operation_falls_back_to_span_name(self):
        proto = _proto(name="chat.completion")
        assert _operation_from(proto, {}) == "chat.completion"


class TestSpanUsage:

    def test_client_reported_cost_wins(self):
        usage = _build_span_usage(
            _span(
                {
                    "genai.prompt_tokens": 1000,
                    "genai.completion_tokens": 500,
                    "genai.model": "gpt-5-mini",
                    "genai.cost": 9.99,
                }
            )
        )
        assert usage["cost_usd"] == 9.99
