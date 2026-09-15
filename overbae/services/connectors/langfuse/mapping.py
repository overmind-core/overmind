"""Langfuse's vocabulary, read by the shared span mapping."""

from __future__ import annotations

from overbae.services.connectors.mapping import SourceConventions
from overbae.services.connectors.schema import CONNECTOR_SOURCE_LANGFUSE

# Langfuse observation type -> Overmind span_type. Trace roots override to entry_point.
_SPAN_TYPES = {
    "GENERATION": "llm_call",
    "EMBEDDING": "llm_call",
    "TOOL": "tool_call",
    "CAPABILITY": "llm_call",  # overridden to entry_point when it roots a trace
    "CHAIN": "workflow",
    "RETRIEVER": "retrieval",
    "EVALUATOR": "workflow",
    "GUARDRAIL": "workflow",
    "SPAN": "llm_call",
    "EVENT": "llm_call",
}

LANGFUSE = SourceConventions(
    source=CONNECTOR_SOURCE_LANGFUSE,
    span_types=_SPAN_TYPES,
    capability_type="CAPABILITY",
    model_call_types=frozenset({"GENERATION", "EMBEDDING"}),
    # SDK-injected noise plus a v2/v3-era tag convention that is inert in SDK v4.
    metadata_skip=frozenset({"scope", "resourceAttributes", "langfuse_tags"}),
)
