"""Runtime eval declarations — the ``overmind.eval.*`` envelope.

Each public function emits a span event on the current span; the Overmind
platform parses these server-side, so event names and payload shapes are a
pinned wire contract (v1) — see ``docs/tracing-attributes.md`` §6.  All
functions no-op (with a debug log) when there is no recording span.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, replace
from typing import Any

from opentelemetry import trace

from overmind import attrs, payloads

logger = logging.getLogger(__name__)

EXPECT_KINDS = frozenset({"contains", "regex", "schema", "constraint", "checkpoints"})
EXPECT_SCOPES = frozenset({"span", "trace", "conversation"})
# The server keeps the first 64 expectations per unit and drops the rest.
MAX_EXPECTATIONS = 64


def _emit(event_name: str, payload: dict[str, Any], span: trace.Span | None = None) -> None:
    span = span if span is not None else trace.get_current_span()
    if not span.is_recording():
        logger.debug("%s ignored: no recording span", event_name)
        return
    span.add_event(
        event_name,
        {attrs.EVAL_SCHEMA_VERSION: 1, attrs.EVAL_PAYLOAD: payloads.serialize(payload)},
    )


@dataclass(frozen=True)
class Expectation:
    """One declared expectation, checked server-side when the trace is scored.

    ``kind`` is ``contains`` / ``regex`` / ``schema`` / ``constraint`` /
    ``checkpoints``; ``spec`` is what to check (a string, or an object such as
    a JSON schema or the ordered checkpoint names). ``scope`` is ``span`` /
    ``trace`` / ``conversation``; ``None`` lets the declaring site choose.
    ``gate=True`` makes a miss cap the score. ``id`` defaults to a hash of
    kind and spec, stable across runs so the platform can aggregate."""

    kind: str
    spec: Any
    id: str | None = None
    scope: str | None = None
    gate: bool = False

    def __post_init__(self) -> None:
        if self.kind not in EXPECT_KINDS:
            raise ValueError(f"expectation kind must be one of {sorted(EXPECT_KINDS)}, got {self.kind!r}")
        if self.scope is not None and self.scope not in EXPECT_SCOPES:
            raise ValueError(f"expectation scope must be one of {sorted(EXPECT_SCOPES)}, got {self.scope!r}")
        spec = payloads.normalize(self.spec)
        object.__setattr__(self, "spec", spec)
        if self.id is None:
            canonical = json.dumps(spec, sort_keys=True, ensure_ascii=False)
            object.__setattr__(self, "id", hashlib.sha256(f"{self.kind}:{canonical}".encode()).hexdigest()[:12])

    def scoped(self, default: str) -> Expectation:
        return self if self.scope is not None else replace(self, scope=default)

    def emit(self, span: trace.Span | None = None) -> None:
        """Add the expectation event to *span* (default: the current span)."""
        scope = self.scope or "trace"
        _emit(
            attrs.EVAL_EXPECTATION_EVENT,
            {"id": self.id, "kind": self.kind, "spec": self.spec, "scope": scope, "gate": bool(self.gate)},
            span,
        )


def expect(
    kind: str,
    spec: Any,
    *,
    id: str | None = None,
    scope: str = "trace",
    gate: bool = False,
) -> None:
    """Declare a runtime expectation on the current span; see :class:`Expectation`."""
    Expectation(kind, spec, id=id, scope=scope, gate=gate).emit()


def eval_context(**facts: Any) -> None:
    """Attach runtime facts for the judge; values coerced like :func:`set_tag`."""
    _emit(attrs.EVAL_CONTEXT_EVENT, {"facts": {key: payloads.to_attribute(value) for key, value in facts.items()}})


def intent(text: str, *, source: str = "declared") -> None:
    """Declare what the user asked for in this run; the platform grounds judge
    scoring in it. Undeclared runs fall back server-side to the first user
    message."""
    _emit(attrs.EVAL_INTENT_EVENT, {"text": str(text), "source": str(source)})


def checkpoint(name: str) -> None:
    """Mark a named trajectory milestone / turn boundary."""
    _emit(attrs.EVAL_CHECKPOINT_EVENT, {"name": name})


def end_conversation() -> None:
    """Signal the conversation is complete; triggers conversation-scope scoring."""
    _emit(attrs.EVAL_CONVERSATION_END_EVENT, {})


__all__ = ["Expectation", "checkpoint", "end_conversation", "eval_context", "expect", "intent"]
