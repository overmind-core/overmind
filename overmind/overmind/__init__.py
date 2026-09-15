"""
Overmind Python Client

Overmind: autonomous agent optimisation through structured experimentation.
Overmind: automatic observability for LLM applications.

"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "0.1.69"

from .client import Client, ModelDeleted, OvermindInferenceError

if TYPE_CHECKING:
    from opentelemetry.overmind.prompt import PromptString

    from .evals import checkpoint, end_conversation, eval_context, expect, intent
    from .lifecycle import RunHandle, run
    from .tracing import (
        SpanType,
        capability,
        capture_exception,
        deliver,
        entry_point,
        force_flush_traces,
        init,
        normalize_messages,
        observe,
        retrieval,
        set_conversation_id,
        set_tag,
        set_user,
        set_workflow_name,
        start_span,
        task,
        tool,
        workflow,
    )

__all__ = [
    "Client",
    "ModelDeleted",
    "OvermindInferenceError",
    "PromptString",
    "RunHandle",
    "SpanType",
    "capability",
    "capture_exception",
    "checkpoint",
    "deliver",
    "end_conversation",
    "entry_point",
    "eval_context",
    "expect",
    "force_flush_traces",
    "init",
    "intent",
    "normalize_messages",
    "observe",
    "retrieval",
    "run",
    "set_conversation_id",
    "set_tag",
    "set_user",
    "set_workflow_name",
    "start_span",
    "task",
    "tool",
    "workflow",
]

_EVALS = frozenset({"checkpoint", "end_conversation", "eval_context", "expect", "intent"})
_LIFECYCLE = frozenset({"RunHandle", "run"})
_TRACING = frozenset({
    "SpanType",
    "capability",
    "capture_exception",
    "deliver",
    "entry_point",
    "force_flush_traces",
    "init",
    "normalize_messages",
    "observe",
    "retrieval",
    "set_conversation_id",
    "set_tag",
    "set_user",
    "set_workflow_name",
    "start_span",
    "task",
    "tool",
    "workflow",
})


def __getattr__(name: str) -> Any:
    # Tracing names import on access so `pip install overmind` (CLI) does not load OpenTelemetry.
    try:
        if name == "PromptString":
            from opentelemetry.overmind.prompt import PromptString

            return PromptString
        if name in _LIFECYCLE:
            from . import lifecycle

            return getattr(lifecycle, name)
        if name in _EVALS:
            from . import evals

            return getattr(evals, name)
        if name in _TRACING:
            from . import tracing

            return getattr(tracing, name)
    except ImportError as exc:
        raise ImportError('OpenTelemetry is not installed. Install with: pip install "overmind[tracing]"') from exc
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
