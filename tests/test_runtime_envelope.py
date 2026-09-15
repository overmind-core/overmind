from __future__ import annotations

import json
from types import SimpleNamespace

from overbae.api import overmind_attrs as oc_attrs
from overbae.services.eval import envelope, normalizer
from overbae.services.eval.evaluators import base
from tests.factories import expectation


def _event(name, payload, *, version=1, ts=1, raw=None):
    return {
        "time_unix_nano": ts,
        "name": name,
        "attributes": {
            oc_attrs.EVAL_SCHEMA_VERSION: version,
            oc_attrs.EVAL_PAYLOAD: raw if raw is not None else json.dumps(payload),
        },
    }


def _span(span_id="s1", events=(), attributes=None, span_type="llm_call", start=1):
    return SimpleNamespace(
        span_id=span_id,
        trace_id="t1",
        parent_span_id=None,
        name="llm",
        status_code=0,
        span_type=span_type,
        start_time_ns=start,
        events=list(events),
        attributes=attributes or {},
    )


def test_extract_valid_envelope():
    span = _span(
        events=[
            _event(oc_attrs.EVAL_EVENT_EXPECTATION, expectation(), ts=1),
            _event(oc_attrs.EVAL_EVENT_CONTEXT, {"facts": {"user_tier": "premium"}}, ts=2),
            _event(oc_attrs.EVAL_EVENT_CHECKPOINT, {"name": "payment_confirmed"}, ts=3),
            _event(oc_attrs.EVAL_EVENT_CONVERSATION_END, {}, ts=4),
        ]
    )
    out = envelope.extract_envelope([span])
    assert out["expectations"] == [
        {
            "id": "currency",
            "kind": "contains",
            "spec": "USD",
            "scope": "trace",
            "gate": True,
            "span_id": "s1",
        }
    ]
    assert out["context"] == {"user_tier": "premium"}
    assert out["checkpoints"] == [{"name": "payment_confirmed", "span_id": "s1"}]
    assert out["conversation_end"] is True
    assert out["errors"] == []


def test_scope_defaults_to_trace_and_gate_to_false():
    payload = {"id": "x", "kind": "regex", "spec": "\\d+"}
    out = envelope.extract_envelope(
        [_span(events=[_event(oc_attrs.EVAL_EVENT_EXPECTATION, payload)])]
    )
    assert out["expectations"][0]["scope"] == "trace"
    assert out["expectations"][0]["gate"] is False


def test_malformed_entries_collect_errors_never_raise():
    span = _span(
        events=[
            _event(oc_attrs.EVAL_EVENT_EXPECTATION, None, raw="{not json"),
            _event(oc_attrs.EVAL_EVENT_EXPECTATION, expectation(), version=99),
            _event(oc_attrs.EVAL_EVENT_EXPECTATION, {"kind": "contains", "spec": "x"}),
            _event(oc_attrs.EVAL_EVENT_EXPECTATION, expectation(kind="mystery")),
            _event(oc_attrs.EVAL_EVENT_EXPECTATION, expectation(scope="galaxy")),
            _event(oc_attrs.EVAL_EVENT_CONTEXT, {"facts": "not-a-dict"}),
            _event(oc_attrs.EVAL_EVENT_CHECKPOINT, {}),
            {"time_unix_nano": 9, "name": oc_attrs.EVAL_EVENT_EXPECTATION},
        ]
    )
    out = envelope.extract_envelope([span])
    assert out["expectations"] == []
    assert out["context"] == {}
    assert out["checkpoints"] == []
    assert len(out["errors"]) == 8
    reasons = " | ".join(e["reason"] for e in out["errors"])
    assert "schema_version" in reasons
    assert "not valid JSON" in reasons
    assert "missing id" in reasons
    assert "kind" in reasons
    assert "scope" in reasons


def test_expectation_cap():
    events = [
        _event(oc_attrs.EVAL_EVENT_EXPECTATION, expectation(exp_id=f"e{i}"), ts=i)
        for i in range(envelope.MAX_EXPECTATIONS + 3)
    ]
    out = envelope.extract_envelope([_span(events=events)])
    assert len(out["expectations"]) == envelope.MAX_EXPECTATIONS
    assert len(out["errors"]) == 3
    assert all("cap" in e["reason"] for e in out["errors"])


def test_payload_size_cap():
    big = expectation(spec="x" * (envelope.MAX_PAYLOAD_BYTES + 1))
    out = envelope.extract_envelope([_span(events=[_event(oc_attrs.EVAL_EVENT_EXPECTATION, big)])])
    assert out["expectations"] == []
    assert "exceeds" in out["errors"][0]["reason"]


def test_dedupe_by_id_first_wins():
    events = [
        _event(oc_attrs.EVAL_EVENT_EXPECTATION, expectation(spec="USD"), ts=1),
        _event(oc_attrs.EVAL_EVENT_EXPECTATION, expectation(spec="EUR"), ts=2),
    ]
    out = envelope.extract_envelope([_span(events=events)])
    assert len(out["expectations"]) == 1
    assert out["expectations"][0]["spec"] == "USD"
    assert out["errors"] == []


def test_events_ordered_across_spans_and_later_context_wins():
    a = _span(
        span_id="a",
        events=[
            _event(oc_attrs.EVAL_EVENT_CHECKPOINT, {"name": "second"}, ts=20),
            _event(oc_attrs.EVAL_EVENT_CONTEXT, {"facts": {"k": "old"}}, ts=5),
        ],
    )
    b = _span(
        span_id="b",
        events=[
            _event(oc_attrs.EVAL_EVENT_CHECKPOINT, {"name": "first"}, ts=10),
            _event(oc_attrs.EVAL_EVENT_CONTEXT, {"facts": {"k": "new"}}, ts=15),
        ],
    )
    out = envelope.extract_envelope([a, b])
    assert [c["name"] for c in out["checkpoints"]] == ["first", "second"]
    assert out["context"] == {"k": "new"}


def test_intent_event_parses_and_later_declaration_wins():
    span = _span(
        events=[
            _event(oc_attrs.EVAL_EVENT_INTENT, {"text": "first", "source": "declared"}, ts=1),
            _event(oc_attrs.EVAL_EVENT_INTENT, {"text": "refined"}, ts=2),
        ]
    )
    out = envelope.extract_envelope([span])
    assert out["intent"] == {"text": "refined", "source": "declared"}
    assert out["errors"] == []


def test_intent_missing_text_collects_error():
    out = envelope.extract_envelope(
        [_span(events=[_event(oc_attrs.EVAL_EVENT_INTENT, {"source": "declared"})])]
    )
    assert out["intent"] is None
    assert "intent missing text" in out["errors"][0]["reason"]


def test_reconstruct_spans_attaches_intent_to_runtime():
    span = _chat_span(events=[_event(oc_attrs.EVAL_EVENT_INTENT, {"text": "book a flight"})])
    trajectory = normalizer.reconstruct_spans([span])
    assert trajectory["runtime"]["intent"] == {"text": "book a flight", "source": "declared"}


def test_prompt_records_render_from_template():
    span = _span(
        attributes={
            oc_attrs.PROMPT_TEMPLATE: "You have these tools: {tools}",
            oc_attrs.PROMPT_KWARGS: json.dumps({"tools": "search, book"}),
        }
    )
    records = envelope.prompt_records([span])
    assert records == [
        {
            "span_id": "s1",
            "template": "You have these tools: {tools}",
            "kwargs": {"tools": "search, book"},
            "rendered": "You have these tools: search, book",
        }
    ]


def test_prompt_records_absent_without_prompt_attrs():
    assert envelope.prompt_records([_span(attributes={"genai.model": "gpt"})]) == []


def test_prompt_records_fallback_to_span_input():
    span = _span(
        attributes={
            oc_attrs.PROMPT_TEMPLATE: "broken {template",
            "overmind.input.data": json.dumps(
                [
                    {"role": "system", "content": "the assembled system prompt"},
                    {"role": "user", "content": "hi"},
                ]
            ),
        }
    )
    records = envelope.prompt_records([span])
    assert records[0]["rendered"] == "the assembled system prompt"


def _chat_span(span_id="root", events=(), extra_attrs=None, start=1):
    attributes = {
        "overmind.input.data": json.dumps([{"role": "user", "content": "how much?"}]),
        "overmind.output.data": json.dumps([{"role": "assistant", "content": "100 USD"}]),
        **(extra_attrs or {}),
    }
    return _span(span_id=span_id, events=events, attributes=attributes, start=start)


def test_reconstruct_spans_without_envelope_is_bit_for_bit_identical():
    plain = normalizer.reconstruct_spans([_chat_span()])
    enriched = normalizer.reconstruct_spans(
        [_chat_span(events=[_event(oc_attrs.EVAL_EVENT_CHECKPOINT, {"name": "cp"})])]
    )
    assert "runtime" not in plain
    runtime = enriched.pop("runtime")
    assert runtime["checkpoints"] == [{"name": "cp", "span_id": "root"}]
    assert enriched == plain


def test_reconstruct_spans_attaches_runtime_and_prompt_records():
    span = _chat_span(
        events=[_event(oc_attrs.EVAL_EVENT_EXPECTATION, expectation())],
        extra_attrs={
            oc_attrs.PROMPT_TEMPLATE: "answer in {currency}",
            oc_attrs.PROMPT_KWARGS: json.dumps({"currency": "USD"}),
        },
    )
    trajectory = normalizer.reconstruct_spans([span])
    runtime = trajectory["runtime"]
    assert runtime["expectations"][0]["id"] == "currency"
    assert runtime["prompt_records"][0]["rendered"] == "answer in USD"
    assert "conversation_end" not in runtime


def test_resolver_runtime_sources():
    unit = base.EvalUnit(
        trajectory={
            "final_output": "100 USD",
            "messages": [],
            "runtime": {
                "expectations": [{"id": "currency", "kind": "contains"}],
                "context": {"user_tier": "premium"},
                "checkpoints": [{"name": "cp", "span_id": "s"}],
                "prompt_records": [
                    {"span_id": "s", "template": "T {x}", "kwargs": {"x": 1}, "rendered": "T 1"}
                ],
            },
        }
    )
    assert base._source_object(unit, "runtime_expectations") == [
        {"id": "currency", "kind": "contains"}
    ]
    assert base._source_object(unit, "runtime_context") == {"user_tier": "premium"}
    assert base._source_object(unit, "runtime_checkpoints") == [{"name": "cp", "span_id": "s"}]
    assert base._source_object(unit, "prompt_template") == "T {x}"
    assert base._source_object(unit, "prompt_kwargs") == {"x": 1}


def test_resolver_runtime_sources_absent_on_plain_trajectory():
    unit = base.EvalUnit(trajectory={"final_output": "x", "messages": []})
    assert base._source_object(unit, "runtime_expectations") == []
    assert base._source_object(unit, "runtime_context") == {}
    assert base._source_object(unit, "runtime_checkpoints") == []
    assert base._source_object(unit, "prompt_template") is None
    assert base._source_object(unit, "prompt_kwargs") is None
    rv = base.resolve_one(unit, "prompt_template", source="prompt_template")
    assert rv.shape == "empty"


def test_resolver_multi_record_prompt_template_returns_ordered_list():
    unit = base.EvalUnit(
        trajectory={
            "runtime": {
                "prompt_records": [
                    {"span_id": "a", "template": "first", "kwargs": {}, "rendered": "first"},
                    {"span_id": "b", "template": "second", "kwargs": {}, "rendered": "second"},
                ]
            }
        }
    )
    assert base._source_object(unit, "prompt_template") == ["first", "second"]
