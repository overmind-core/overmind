"""One reader for what a span asked, answered and was expected to do.

The Overmind decorators write ``overmind.*`` attributes; OpenLLMetry, OpenInference
and the OTel GenAI conventions are read through the shared transcript parsers, so
spans from any instrumentation are scored the same way.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from typing import Any

from overbae.models import Span
from overbae.models.traces import is_tool_operation
from overbae.services.transcripts import chatml
from overbae.services.transcripts.envelope import extract_envelope
from overbae.services.transcripts.span_evidence import unwrap_payload

ARG_PREFIX = "overmind.arg."
INPUT_KEYS = ("overmind.input", "input", *chatml.INPUT_KEYS, "input.value")
OUTPUT_KEYS = ("overmind.output", "output", *chatml.OUTPUT_KEYS, "output.value")
EXPECTED_KEYS = ("overmind.expected", "expected_output", "expected")
INTENT_KEYS = ("overmind.intent", "intent")
TOOL_NAME_KEYS = ("tool.name", "gen_ai.tool.name", "overmind.tool.name")
MODEL_KEYS = (*chatml.MODEL_KEYS, "genai.response.model", "llm.model_name")
OTEL_ERROR = 2


def fingerprint(span: Span) -> str:
    """Identifies the evidence a score was computed from."""
    payload = json.dumps(
        [span.name, span.span_type, span.parent_span_id, span.status_code, span.attributes],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def scored_unchanged(spans: list[Span]) -> set[str]:
    """Ids of spans that already hold a final score computed from the same evidence. A
    score stored without a fingerprint predates the check and is kept."""
    stored = dict(
        Span.objects.filter(
            span_id__in=[span.span_id for span in spans], eval_attributes__has_key="score"
        ).values_list("span_id", "eval_attributes__fingerprint")
    )
    return {
        span.span_id
        for span in spans
        if span.span_id in stored and stored[span.span_id] in (None, fingerprint(span))
    }


def as_text(value: Any) -> str:
    if value is None or (isinstance(value, (dict, list)) and not value):
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, default=str)


def first_text(attributes: dict, *keys: str) -> str:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return ""


def _messages(span: Span) -> tuple[list[dict] | None, list[dict] | None]:
    attributes = span.attributes or {}
    prompt, completion = chatml.parse_genai_semconv_messages(attributes)
    if prompt is None and completion is None:
        prompt, completion = chatml.parse_indexed_genai_messages(attributes)
    return prompt, completion


def read_input(span: Span) -> str:
    attributes = span.attributes or {}
    value = chatml.pick(attributes, INPUT_KEYS)
    if value is not None:
        return as_text(unwrap_payload(value))
    args = {
        key.removeprefix(ARG_PREFIX): value
        for key, value in attributes.items()
        if key.startswith(ARG_PREFIX)
    }
    if len(args) == 1:
        (value,) = args.values()
        if isinstance(value, str):
            return value.strip()
    if args:
        return json.dumps(args, ensure_ascii=False, default=str)
    prompt, _ = _messages(span)
    return chatml.messages_text(prompt) if prompt else ""


def read_output(span: Span) -> str:
    attributes = span.attributes or {}
    value = chatml.pick(attributes, OUTPUT_KEYS)
    if value is not None:
        return as_text(unwrap_payload(value))
    _, completion = _messages(span)
    if not completion:
        return ""
    if len(completion) == 1 and not completion[0].get("tool_calls"):
        return str(completion[0].get("content") or "").strip()
    return json.dumps(completion, ensure_ascii=False, default=str)


def read_expected(span: Span) -> str:
    return as_text(chatml.pick(span.attributes or {}, EXPECTED_KEYS))


def declared_constraints(span: Span) -> list[str]:
    """``overmind.expect("constraint", ...)`` events: natural-language expectations."""
    return [
        expectation["spec"].strip()
        for expectation in extract_envelope([span])["expectations"]
        if expectation["kind"] == "constraint" and isinstance(expectation["spec"], str)
    ]


def declared_intent(span: Span) -> str:
    intent = first_text(span.attributes or {}, *INTENT_KEYS)
    if intent:
        return intent
    event = extract_envelope([span])["intent"]
    return event["text"] if event else ""


def read_expectations(attributes: dict) -> list[str]:
    value = attributes.get("overmind.expectations", attributes.get("expectations"))
    # OTLP array attributes can arrive as a JSON-encoded string.
    if isinstance(value, str) and value.lstrip().startswith("["):
        with suppress(json.JSONDecodeError):
            value = json.loads(value)
    if isinstance(value, str):
        value = [value]
    if isinstance(value, (list, tuple)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    expect = first_text(attributes, "overmind.expect", "expect")
    return [expect] if expect else []


def read_reference(attributes: dict) -> list[str]:
    value = attributes.get("overmind.reference_trajectory", attributes.get("reference_trajectory"))
    if isinstance(value, str):
        with suppress(json.JSONDecodeError):
            value = json.loads(value)
    if not isinstance(value, (list, tuple)):
        return []
    names = [call.get("name", "") if isinstance(call, dict) else str(call) for call in value]
    return [name for name in names if name]


def tool_name(span: Span) -> str:
    return first_text(span.attributes or {}, *TOOL_NAME_KEYS) or span.name


def model_name(span: Span) -> str:
    return first_text(span.usage or span.attributes or {}, *MODEL_KEYS)


def is_tool(span: Span) -> bool:
    return is_tool_operation(span.span_type)


def is_llm(span: Span) -> bool:
    return span.span_type == Span.SpanType.LLM_CALL


def status(span: Span) -> str:
    return "error" if span.status_code == OTEL_ERROR else "ok"


def number(values: dict | None, *keys: str) -> float | None:
    for key in keys:
        value = chatml.to_number((values or {}).get(key))
        if value is not None:
            return value
    return None


def tokens(span: Span) -> float | None:
    return number(span.usage, *chatml.TOTAL_TOKEN_KEYS, "gen_ai.usage.total_tokens")


def cost(span: Span) -> float | None:
    return number(span.usage, *chatml.COST_KEYS, "overmind.cost")


def seconds(span: Span) -> float:
    return span.duration_ns / 1e9 if span.duration_ns else 0.0


def requested_tools(output: str) -> list[str]:
    try:
        message = json.loads(output)
    except json.JSONDecodeError:
        return []
    if isinstance(message, dict) and message.get("choices"):
        message = message["choices"][0].get("message", {})
    messages = message if isinstance(message, list) else [message]
    return [
        (call.get("function") or call).get("name", "")
        for message in messages
        if isinstance(message, dict)
        for call in message.get("tool_calls") or []
    ]


def read_intent(span: Span, trace: dict[str, Span], fallback: str = "") -> str:
    """The nearest declared intent up the arrived ancestry, else the root's input if it
    has arrived, else ``fallback`` (the capability description)."""
    node = span
    while True:
        intent = declared_intent(node)
        if intent:
            return intent
        if node.is_root:
            return read_input(node) or fallback
        parent = trace.get(node.parent_span_id)
        if parent is None:
            return fallback
        node = parent
