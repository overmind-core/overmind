"""Provenance and confidence primitives for OverClaw's execution pipeline.

Every span produced by an agent run carries a :class:`TraceSource` indicating
whether the data is real (the user's code actually ran), replayed from a
cassette, or simulated.  Downstream, the evaluator aggregates per-span sources
into a per-case :class:`Confidence` used to decide whether to accept a
candidate automatically, suggest it for review, or discard it.

The design principle is that OverClaw can still reason about an agent when it
cannot fully execute it, but it must never pretend simulated signal is the
same as real signal.  Provenance is how we stay honest.

This module deliberately contains no runtime behaviour — just types, helpers,
and a small aggregation function.  It is imported both in-process and inside
the subprocess intercept layer, so it must stay dependency-free (stdlib only).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class TraceSource(str, Enum):
    """Where a trace span's data came from.

    The enum is a ``str`` subclass so values serialise cleanly through JSON /
    OpenTelemetry attribute strings without a custom encoder.

    Ordered from highest fidelity to lowest.
    """

    REAL_SUBPROCESS = "real_subprocess"
    """Span produced by executing the user's code in a normal subprocess with
    live external calls.  Fully trustworthy."""

    LLM_REAL = "llm_real"
    """An LLM call that hit the real model (prompts were computed from the
    user's code; completion is the model's actual response).  Trustworthy."""

    CODE_REAL = "code_real"
    """Deterministic Python (or pure-function tool) executed for real.
    Trustworthy."""

    CASSETTE = "cassette"
    """Replayed from a cassette captured during a prior successful run.
    Trustworthy at capture time; may be stale if the world has changed."""

    SIMULATED = "simulated"
    """Value produced by a tool-response simulator (an LLM hallucinating a
    plausible response for an external tool we could not run).  Low fidelity
    — only for agents that would otherwise be unoptimizable."""

    STATIC_ONLY = "static_only"
    """No execution happened.  Signal derived from reading the code alone.
    Lowest fidelity."""


# Weights used to translate a span's source into a confidence contribution.
# These are defaults; callers can override with their own mapping.
_DEFAULT_SOURCE_WEIGHTS: dict[TraceSource, float] = {
    TraceSource.REAL_SUBPROCESS: 1.00,
    TraceSource.LLM_REAL: 1.00,
    TraceSource.CODE_REAL: 1.00,
    TraceSource.CASSETTE: 0.85,
    TraceSource.SIMULATED: 0.45,
    TraceSource.STATIC_ONLY: 0.20,
}


@dataclass(frozen=True)
class SourceTag:
    """Lightweight, serialisable tag attached to a trace span.

    Carries *where* the span's data came from and a human-readable *reason*
    explaining the source.  Designed to be attached to OpenTelemetry span
    attributes and to ``ParsedTrace`` entries.
    """

    source: TraceSource
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source.value, "reason": self.reason}

    @classmethod
    def from_dict(cls, raw: dict | None) -> SourceTag | None:
        if not raw:
            return None
        source = raw.get("source")
        if not source:
            return None
        try:
            return cls(source=TraceSource(source), reason=raw.get("reason", ""))
        except ValueError:
            return None


@dataclass
class Confidence:
    """Aggregate confidence in a run's signal, 0.0 – 1.0.

    ``score`` is the weighted blend of per-span source fidelity.
    ``summary`` breaks down how many spans came from each source (useful for
    UI display and for the optimizer's acceptance gate).
    ``reason`` is a short, human-readable explanation ("2 real LLM calls, 3
    simulated tool responses, no cassette hits").
    """

    score: float = 1.0
    summary: dict[str, int] = field(default_factory=dict)
    reason: str = "real execution"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def level(self) -> str:
        """Coarse bucket for UI and acceptance logic."""
        if self.score >= 0.85:
            return "high"
        if self.score >= 0.55:
            return "medium"
        return "low"


def aggregate_confidence(
    tags: list[SourceTag] | None,
    *,
    weights: dict[TraceSource, float] | None = None,
) -> Confidence:
    """Collapse a list of per-span tags into a single :class:`Confidence`.

    If *tags* is empty we assume :attr:`TraceSource.REAL_SUBPROCESS` — the
    historical default — so old callers keep their behaviour.
    """
    if not tags:
        return Confidence(
            score=_DEFAULT_SOURCE_WEIGHTS[TraceSource.REAL_SUBPROCESS],
            summary={TraceSource.REAL_SUBPROCESS.value: 1},
            reason="real execution (no explicit provenance)",
        )

    weights = weights or _DEFAULT_SOURCE_WEIGHTS

    total_weight = 0.0
    summary: dict[str, int] = {}
    for tag in tags:
        total_weight += weights.get(tag.source, 0.5)
        summary[tag.source.value] = summary.get(tag.source.value, 0) + 1

    score = total_weight / max(len(tags), 1)

    parts = [f"{count}× {src}" for src, count in summary.items()]
    reason = ", ".join(parts) if parts else "no spans"

    return Confidence(score=round(score, 3), summary=summary, reason=reason)


def summarize_sources(tags: list[SourceTag] | None) -> dict[str, int]:
    """Count occurrences of each :class:`TraceSource` in *tags*."""
    if not tags:
        return {}
    summary: dict[str, int] = {}
    for tag in tags:
        summary[tag.source.value] = summary.get(tag.source.value, 0) + 1
    return summary
