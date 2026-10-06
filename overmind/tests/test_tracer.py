"""The observe decorator on plain and async callables, through the exporter."""

from __future__ import annotations

import asyncio
import json

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import overmind.tracing as tracing
from overmind import attrs
from overmind.tracing import observe


@pytest.fixture
def exporter(monkeypatch):
    provider = TracerProvider()
    inmem = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(inmem))
    monkeypatch.setattr(tracing, "_tracer", provider.get_tracer("overmind", "test"))
    return inmem


def _only_span(exporter):
    (span,) = exporter.get_finished_spans()
    return span


def test_observe_async(exporter):
    @observe(span_name="async_operation")
    async def async_add(a: int, b: int):
        await asyncio.sleep(0.01)
        return a + b

    assert asyncio.run(async_add(10, 20)) == 30
    span = _only_span(exporter)
    assert span.name == "async_operation"
    assert span.status.status_code == StatusCode.OK
    assert span.attributes[attrs.STATUS] == "success"
    assert json.loads(span.attributes["outputs"]) == 30


def test_observe_async_with_exception(exporter):
    @observe()
    async def async_fail():
        await asyncio.sleep(0.01)
        raise RuntimeError("Async error")

    with pytest.raises(RuntimeError, match="Async error"):
        asyncio.run(async_fail())

    span = _only_span(exporter)
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes[attrs.STATUS] == "failed"
    assert span.attributes[attrs.ERROR_TYPE] == "RuntimeError"
    assert [event.name for event in span.events] == ["exception"]


def test_observe_async_cancellation_marks_cancelled(exporter):
    """asyncio.CancelledError ends the span cancelled, not failed, and propagates."""

    @observe()
    async def cancelled_op():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled_op())

    span = _only_span(exporter)
    assert span.attributes[attrs.STATUS] == "cancelled"
    assert attrs.ERROR_TYPE not in span.attributes
    assert [event.name for event in span.events] == ["exception"]


def test_observe_preserves_function_metadata():
    @observe()
    def documented_function(param: int) -> int:
        """This is a test function."""
        return param * 2

    assert documented_function.__name__ == "documented_function"
    assert documented_function.__doc__ == "This is a test function."


def test_observe_skips_cls_in_classmethod(exporter):
    class TestClass:
        class_value = 20

        @classmethod
        @observe()
        def class_method(cls, x: int, y: int):
            return cls.class_value + x + y

    assert TestClass.class_method(5, 3) == 28
    assert json.loads(_only_span(exporter).attributes["inputs"]) == {"x": 5, "y": 3}


def test_observe_async_class_method(exporter):
    class AsyncTestClass:
        def __init__(self):
            self.value = 15

        @observe()
        async def async_method(self, x: int):
            await asyncio.sleep(0.01)
            return self.value * x

    assert asyncio.run(AsyncTestClass().async_method(2)) == 30
    span = _only_span(exporter)
    assert span.name.endswith("AsyncTestClass.async_method")
    assert json.loads(span.attributes["inputs"]) == {"x": 2}
