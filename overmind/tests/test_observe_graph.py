"""The execution graph ``observe`` records: generator steps, expectations,
tool-call linkage, per-trace step order, cross-boundary propagation and the
payload budget. Everything is asserted through the exporter."""

from __future__ import annotations

import asyncio
import functools
import json
import threading

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import overmind.tracing as tracing
from overmind import attrs
from overmind.evals import Expectation
from overmind.payloads import MAX_PAYLOAD_BYTES
from overmind.tracing import (
    SpanType,
    capability,
    carrier,
    continue_trace,
    entry_point,
    observe,
    start_span,
    tool,
    tool_call,
)


@pytest.fixture
def exporter(monkeypatch):
    provider = TracerProvider()
    inmem = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(inmem))
    tracing._ensure_pipeline_processors(provider)
    monkeypatch.setattr(tracing, "_tracer", provider.get_tracer("overmind", "test"))
    return inmem


def _by_name(exporter):
    return {span.name: span for span in exporter.get_finished_spans()}


def _parent_id(span):
    return span.parent.span_id if span.parent else None


def _expectation_events(span):
    return [json.loads(e.attributes[attrs.EVAL_PAYLOAD]) for e in span.events if e.name == attrs.EVAL_EXPECTATION_EVENT]


# --- generators -----------------------------------------------------------


def test_generator_span_is_current_only_while_the_body_runs(exporter):
    @observe("stream")
    def stream():
        with start_span("inside"):
            pass
        yield 1
        yield 2

    for _ in stream():
        with start_span("between"):
            pass

    spans = _by_name(exporter)
    assert _parent_id(spans["inside"]) == spans["stream"].context.span_id
    assert _parent_id(spans["between"]) is None
    assert spans["stream"].attributes[attrs.STATUS] == "success"
    assert spans["stream"].attributes[attrs.STREAM_ITEMS] == 2
    assert spans["stream"].end_time > spans["between"].start_time


def test_generator_closed_early_is_aborted_not_failed(exporter):
    @observe("stream")
    def stream():
        yield "a"
        yield "b"

    gen = stream()
    next(gen)
    gen.close()

    span = _by_name(exporter)["stream"]
    assert span.attributes[attrs.STATUS] == "aborted"
    assert span.status.status_code is otel_trace.StatusCode.UNSET
    assert span.attributes[attrs.STREAM_ITEMS] == 1
    assert json.loads(span.attributes["outputs"]) == {"items": 1}


def test_generator_failure_on_resume_is_failed(exporter):
    @observe("stream")
    def stream():
        yield 1
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        list(stream())

    span = _by_name(exporter)["stream"]
    assert span.attributes[attrs.STATUS] == "failed"
    assert span.attributes[attrs.ERROR_TYPE] == "ValueError"


def test_generator_return_value_send_and_throw_pass_through(exporter):
    seen = []

    @observe("inner")
    def inner():
        try:
            received = yield "first"
        except KeyError:
            received = "recovered"
        seen.append(received)
        yield "second"
        return "done"  # noqa: B901 — the value `yield from` must receive

    def outer():
        result = yield from inner()
        yield result

    gen = outer()
    assert next(gen) == "first"
    assert gen.throw(KeyError("x")) == "second"
    assert next(gen) == "done"
    assert seen == ["recovered"]

    span = _by_name(exporter)["inner"]
    assert span.attributes[attrs.STATUS] == "success"
    assert json.loads(span.attributes["outputs"]) == "done"


def test_async_generator_parentage_and_cancellation(exporter):
    @observe("astream")
    async def astream():
        with start_span("inside"):
            pass
        yield 1
        await asyncio.sleep(10)
        yield 2

    async def consume():
        task = asyncio.current_task()
        async for _item in astream():
            with start_span("between"):
                pass
            task.cancel()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(consume())

    spans = _by_name(exporter)
    assert _parent_id(spans["inside"]) == spans["astream"].context.span_id
    assert _parent_id(spans["between"]) is None
    assert spans["astream"].attributes[attrs.STATUS] == "cancelled"


def test_streamed_llm_messages_fold_into_one_assistant_message(exporter):
    def chunk(content=None, tool_calls=None):
        return {"choices": [{"delta": {"content": content, "tool_calls": tool_calls}}]}

    @observe("llm", type=SpanType.LLM, capture="messages")
    def complete(messages):
        yield chunk("Let me ")
        yield chunk("look.")
        yield chunk(tool_calls=[{"index": 0, "id": "call_1", "function": {"name": "search", "arguments": '{"q":'}}])
        yield chunk(tool_calls=[{"index": 0, "function": {"arguments": ' "refunds"}'}}])

    list(complete([{"role": "user", "content": "hi"}]))

    span = _by_name(exporter)["llm"]
    assert json.loads(span.attributes["inputs"]) == {"messages": [{"role": "user", "content": "hi"}]}
    assert json.loads(span.attributes["outputs"]) == {
        "messages": [
            {
                "role": "assistant",
                "content": "Let me look.",
                "tool_calls": [{"id": "call_1", "name": "search", "arguments": {"q": "refunds"}}],
            }
        ]
    }
    assert span.attributes[attrs.STREAM_ITEMS] == 4


# --- expectations ---------------------------------------------------------


def test_expectations_are_emitted_per_call_with_scope_defaults(exporter):
    @entry_point("run", expectations=[Expectation("contains", "refund")])
    def run():
        step()

    @observe(
        "step",
        expectations=[
            Expectation("regex", r"\d+"),
            Expectation("schema", {"type": "object"}, scope="conversation", gate=True),
        ],
    )
    def step():
        pass

    run()
    run()

    spans = exporter.get_finished_spans()
    runs = [s for s in spans if s.name == "run"]
    steps = [s for s in spans if s.name == "step"]
    assert len(runs) == len(steps) == 2
    (run_expectation,) = _expectation_events(runs[0])
    assert run_expectation == {
        "id": run_expectation["id"],
        "kind": "contains",
        "spec": "refund",
        "scope": "trace",
        "gate": False,
    }
    regex, schema = _expectation_events(steps[1])
    assert (regex["scope"], regex["gate"]) == ("span", False)
    assert (schema["scope"], schema["gate"], schema["spec"]) == ("conversation", True, {"type": "object"})
    assert _expectation_events(runs[0])[0]["id"] == _expectation_events(runs[1])[0]["id"]


def test_expectations_are_validated_at_decoration_time():
    with pytest.raises(ValueError, match="kind"):
        Expectation("includes", "x")
    with pytest.raises(TypeError, match="Expectation instances"):
        observe(expectations=[("contains", "x")])
    with pytest.raises(ValueError, match="unique"):
        observe(expectations=[Expectation("contains", "x"), Expectation("contains", "x")])
    with pytest.raises(ValueError, match="at most 64"):
        observe(expectations=[Expectation("contains", str(i)) for i in range(65)])


def test_expectations_without_init_warn_once_and_call_through(monkeypatch, caplog):
    monkeypatch.setattr(tracing, "_initialized", False)
    monkeypatch.setattr(tracing, "_warned_expectations", set())

    @observe(expectations=[Expectation("contains", "x")])
    def op():
        return 1

    with caplog.at_level("WARNING", logger="overmind"):
        assert op() == 1
        assert op() == 1
    assert sum("not emitted" in r.message for r in caplog.records) == 1


# --- argument binding -----------------------------------------------------


def test_input_key_and_ignore(exporter):
    @observe("op", input_key="question", ignore=("session",))
    def op(question, session, **extra):
        return len(question)

    op("why", session=object(), temperature=0.1)
    assert json.loads(_by_name(exporter)["op"].attributes["inputs"]) == "why"

    with pytest.raises(ValueError, match="not a parameter"):
        observe(input_key="missing")(lambda question: question)
    with pytest.raises(ValueError, match="also ignored"):
        observe(input_key="question", ignore=["question"])
    with pytest.raises(ValueError, match="exclusive"):
        observe(input_key="question", format_input=lambda bound: bound)


def test_var_keyword_arguments_flatten_and_self_is_dropped(exporter):
    class Agent:
        @observe("act")
        def act(self, action, **params):
            return action

    Agent().act("navigate", url="https://ex.io")
    assert json.loads(_by_name(exporter)["act"].attributes["inputs"]) == {"action": "navigate", "url": "https://ex.io"}


def test_partial_callable_instance_and_misordered_staticmethod(exporter):
    def base(a, b):
        return a + b

    add_two = observe("partial")(functools.partial(base, 2))
    assert add_two(3) == 5

    class Ranker:
        def __call__(self, query, limit=5):
            return [query] * limit

    rank = observe("instance")(Ranker())
    assert rank("q", limit=1) == ["q"]

    class Util:
        @observe("static")
        @staticmethod
        def shout(text):
            return text.upper()

    assert Util.shout("hi") == "HI"
    assert Util().shout("hi") == "HI"

    spans = _by_name(exporter)
    assert json.loads(spans["partial"].attributes["inputs"]) == {"b": 3}
    assert json.loads(spans["instance"].attributes["inputs"]) == {"query": "q", "limit": 1}
    assert json.loads(spans["static"].attributes["inputs"]) == {"text": "hi"}
    assert spans["static"].attributes[attrs.CODE_FUNCTION_NAME].endswith("Util.shout")
    assert spans["static"].attributes[attrs.CODE_FILE_PATH] == __file__
    assert isinstance(spans["static"].attributes[attrs.CODE_LINE_NUMBER], int)


# --- tool-call linkage ----------------------------------------------------


def test_tool_span_links_to_the_llm_span_that_requested_it(exporter):
    @observe("llm", type=SpanType.LLM)
    def plan():
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"id": "call_7", "function": {"name": "search", "arguments": "{}"}}],
                    }
                }
            ]
        }

    @tool("search")
    def search():
        return []

    @entry_point("run")
    def run():
        plan()
        with tool_call("call_7"):
            search()
        search()  # the pending id was consumed by the first tool span

    run()

    spans = exporter.get_finished_spans()
    llm = next(s for s in spans if s.name == "llm")
    linked, bare = [s for s in spans if s.name == "search"]
    assert linked.attributes[attrs.TOOL_CALL_ID] == "call_7"
    (link,) = linked.links
    assert link.context.span_id == llm.context.span_id
    assert link.attributes[attrs.TOOL_CALL_ID] == "call_7"
    assert attrs.TOOL_CALL_ID not in bare.attributes
    assert bare.links == ()


def test_tool_call_with_unknown_id_keeps_the_id_without_a_link(exporter):
    @tool("search")
    def search():
        return []

    with tool_call("call_unknown"):
        search()

    span = _by_name(exporter)["search"]
    assert span.attributes[attrs.TOOL_CALL_ID] == "call_unknown"
    assert span.links == ()
    with pytest.raises(ValueError), tool_call(""):
        pass


# --- step order -----------------------------------------------------------


def test_steps_count_per_trace_in_start_order(exporter):
    @entry_point("run")
    def run():
        with start_span("a"), start_span("a1"):
            pass
        with start_span("b"):
            pass

    run()
    run()

    spans = exporter.get_finished_spans()
    first, second = spans[:4], spans[4:]
    assert {s.name: s.attributes[attrs.STEP] for s in first} == {"run": 1, "a": 2, "a1": 3, "b": 4}
    assert {s.name: s.attributes[attrs.STEP] for s in second} == {"run": 1, "a": 2, "a1": 3, "b": 4}
    assert first[0].context.trace_id != second[0].context.trace_id


# --- propagation ----------------------------------------------------------


def test_carrier_continues_the_trace_across_a_thread(exporter):
    handoff = {}

    def worker(headers):
        with continue_trace(headers), start_span("in-thread"):
            pass

    with capability(id="cap-123"), start_span("parent") as parent:
        handoff["headers"] = carrier()
        thread = threading.Thread(target=worker, args=(handoff["headers"],))
        thread.start()
        thread.join()
        parent_id = parent.get_span_context().span_id

    headers = handoff["headers"]
    assert set(headers) == {"traceparent", "baggage"}
    spans = _by_name(exporter)
    child = spans["in-thread"]
    assert child.context.trace_id == spans["parent"].context.trace_id
    assert _parent_id(child) == parent_id
    assert child.attributes[attrs.CAPABILITY_ID] == "cap-123"
    assert child.attributes[attrs.STEP] == spans["parent"].attributes[attrs.STEP] + 1


def test_carrier_is_empty_without_a_span_and_continue_trace_tolerates_it(exporter):
    assert carrier() == {}
    with continue_trace({}), start_span("root"):
        pass
    with continue_trace({"traceparent": "not-a-traceparent"}), start_span("root2"):
        pass
    spans = _by_name(exporter)
    assert _parent_id(spans["root"]) is None
    assert _parent_id(spans["root2"]) is None


# --- payload budget -------------------------------------------------------


def test_oversized_payloads_are_replaced_by_a_valid_json_marker(exporter):
    @observe("big")
    def big(text):
        return {"echo": text}

    big("word " * (MAX_PAYLOAD_BYTES // 4))

    span = _by_name(exporter)["big"]
    for key, marker in (("inputs", attrs.INPUTS_TRUNCATED), ("outputs", attrs.OUTPUTS_TRUNCATED)):
        payload = json.loads(span.attributes[key])
        assert payload["truncated"] is True
        assert payload["bytes"] > MAX_PAYLOAD_BYTES
        assert len(span.attributes[key].encode()) < MAX_PAYLOAD_BYTES
        assert span.attributes[marker] is True
