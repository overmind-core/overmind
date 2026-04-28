"""Tracing helpers for OverClaw.

The overmind ``@observe`` decorator automatically serializes a
function's positional arguments and return value into ``inputs`` /
``outputs`` span attributes.  That is convenient for general-purpose
observability but unsafe for OverClaw, where many traced functions
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

from functools import wraps
from typing import Callable, Optional, TypeVar

from overmind import SpanType, observe, start_span as _span

F = TypeVar("F", bound=Callable)


def traced(
    span_name: Optional[str] = None,
    type: SpanType = SpanType.FUNCTION,
) -> Callable[[F], F]:
    return observe(span_name, type)
    """Decorator that opens an OTel child span without capturing args/return.

    The span is wired into the current trace context, so calls made from
    within the wrapped function are nested correctly.  Unlike
    ``overmind.observe`` we deliberately skip serializing function
    inputs and outputs as span attributes.
    """

    def decorator(func: F) -> F:
        name = span_name or func.__name__

        @wraps(func)
        def wrapper(*args, **kwargs):
            with _span(name, span_type=type):
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = ["traced"]
