from types import SimpleNamespace

import pytest

from overbae.api.otlp import (
    _build_span_usage,
    _classify_span_type,
    _operation_from,
    _parent_hex_or_none,
)
from overbae.models import Span


def _proto(name: str = "chat", parent: bytes | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, parent_span_id=parent or b"")


def _span(attributes: dict, span_type=Span.SpanType.LLM_CALL) -> SimpleNamespace:
    return SimpleNamespace(attributes=attributes, span_type=span_type)


class TestParentResolution:
    def test_parent_none_when_empty(self):
        assert _parent_hex_or_none(_proto(parent=b"")) is None

    def test_parent_none_when_zero_bytes(self):
        assert _parent_hex_or_none(_proto(parent=b"\x00" * 8)) is None

    def test_parent_hex_when_present(self):
        parent = bytes.fromhex("0123456789abcdef")
        assert _parent_hex_or_none(_proto(parent=parent)) == "0123456789abcdef"


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
    def test_reads_native_otel_token_attributes(self):
        usage = _build_span_usage(
            _span(
                {
                    "gen_ai.usage.input_tokens": 1000,
                    "gen_ai.usage.output_tokens": 500,
                    "gen_ai.request.model": "gpt-5-mini",
                }
            )
        )
        assert usage["prompt_tokens"] == 1000
        assert usage["completion_tokens"] == 500
        assert usage["total_tokens"] == 1500
        assert usage["models"] == {"gpt-5-mini": 1}

    def test_derives_cost_when_client_omits_it(self):
        usage = _build_span_usage(
            _span(
                {
                    "genai.prompt_tokens": 1000,
                    "genai.completion_tokens": 500,
                    "genai.model": "gpt-5-mini",
                }
            )
        )
        # Must be a plain float — Decimal + float crashed process_span / capability linking.
        assert usage["cost_usd"] > 0
        assert type(usage["cost_usd"]) is float

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

    def test_unknown_model_yields_zero_cost(self):
        usage = _build_span_usage(
            _span(
                {
                    "genai.prompt_tokens": 10,
                    "genai.completion_tokens": 10,
                    "genai.model": "totally-made-up-model-xyz",
                }
            )
        )
        assert usage["cost_usd"] == 0.0
