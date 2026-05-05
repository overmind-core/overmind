"""Tracing helpers for Overmind.

The overmind ``@observe`` decorator automatically serializes a
function's positional arguments and return value into ``inputs`` /
``outputs`` span attributes.  That is convenient for general-purpose
observability but unsafe for Overmind, where many traced functions
receive or return potentially sensitive content (the user's agent
source, policy markdown, generated datapoints, etc.).

``traced`` is a thin replacement that creates a span with the same name
and ``SpanType`` but does **not** capture inputs or outputs.  Use it
instead of ``@observe`` on any function whose arguments or return value
might contain customer code, prompts, datasets, credentials, or other
sensitive material.  Use ``set_tag`` inside the function for the
specific scalar / categorical metadata you do want to surface in traces.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from typing import TypeVar

from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace

from overmind import SpanType, start_span

F = TypeVar("F", bound=Callable)


@contextmanager
def start_child_span(
    name: str,
    *,
    span_type: SpanType = SpanType.FUNCTION,
):
    """Start a span as an explicit child of the current OTel span.

    ``overmind.start_span`` already starts spans in the active context, but we
    attach the current span explicitly so parent/child trees remain stable even
    across nested wrappers and mixed instrumentation stacks.
    """
    current = otel_trace.get_current_span()
    token = None
    try:
        if current is not None and current.get_span_context().is_valid:
            token = otel_context.attach(otel_trace.set_span_in_context(current))
        with start_span(name, span_type=span_type) as span:
            yield span
    finally:
        if token is not None:
            otel_context.detach(token)


def force_flush_traces(timeout_millis: int = 1000) -> None:
    """Best-effort exporter flush for near real-time trace visibility."""
    provider = otel_trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=timeout_millis)


def traced(
    span_name: str | None = None,
    type: SpanType = SpanType.FUNCTION,
) -> Callable[[F], F]:
    """Decorator that opens an OTel child span without capturing args/return.

    The span is wired into the current trace context, so calls made from
    within the wrapped function are nested correctly.  Unlike
    ``overmind.observe`` we deliberately skip serializing function
    inputs and outputs as span attributes.  Use ``set_tag`` inside the
    function for the specific scalar / categorical metadata you do want
    to surface in traces.
    """

    def decorator(func: F) -> F:
        name = span_name or func.__name__

        @wraps(func)
        def wrapper(*args, **kwargs):
            with start_child_span(name, span_type=type):
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = ["force_flush_traces", "start_child_span", "traced"]
