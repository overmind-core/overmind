"""The profiler must find capability boundaries without relying on the CAPABILITY type."""

from overbae.services.connectors.braintrust.mapping import BRAINTRUST
from overbae.services.connectors.langfuse.mapping import LANGFUSE
from overbae.services.connectors.profiling import profile_capability_candidates as _profile
from overbae.services.connectors.records import ObservationRecord
from tests.test_langfuse_capability_traces import (
    _langchain_style_trace,
    scan_inbox_trace,
    triage_email_trace,
)


def profile_capability_candidates(traces, conventions=LANGFUSE):
    return _profile(traces, conventions)


def _ranked(traces):
    return [c["name"] for c in profile_capability_candidates(traces)]


def test_capabilities_outrank_the_loop_step_they_repeat():
    sample = [scan_inbox_trace(20, invoices=10) for _ in range(5)]
    ranked = _ranked(sample)

    # Capabilities tie on score, so the one owning more model calls leads.
    assert ranked[:3] == ["scan-inbox", "triage-invoices", "plan-payments"]
    # The 20x-per-trace unit of work and its leaf generation rank last.
    assert ranked[-2:] == ["analyze-email", "classify-invoice"]


def test_repetition_within_a_trace_is_the_disqualifier():
    (email,) = (
        c
        for c in profile_capability_candidates([scan_inbox_trace(20)])
        if c["name"] == "analyze-email"
    )
    assert email["max_per_trace"] == 20
    assert any("loop step" in r for r in email["reasons"])
    assert email["score"] < 0


def test_ranks_shapes_when_no_capability_type_is_emitted():
    ranked = _ranked([_langchain_style_trace() for _ in range(4)])
    assert ranked[0] == "workflow"  # the root
    assert ranked[1] == "answer-question"  # owns the model call
    assert ranked[-1] == "llm"  # a model call is never the thing making them


def test_evidence_is_reported_for_the_ui():
    (triage,) = (
        c
        for c in profile_capability_candidates([scan_inbox_trace(3), scan_inbox_trace(3)])
        if c["name"] == "triage-invoices"
    )
    assert triage["type"] == "CAPABILITY"
    assert triage["traces"] == 2
    assert triage["max_per_trace"] == 1
    assert triage["model_calls"] == 3
    assert triage["parent_name"] == "scan-inbox"
    assert triage["is_root"] is False


def test_model_calls_are_counted_through_intermediate_spans():
    """analyze-email sits between the capability and its generation."""
    (scan,) = (
        c
        for c in profile_capability_candidates([scan_inbox_trace(4, invoices=2)])
        if c["name"] == "scan-inbox"
    )
    assert scan["model_calls"] == 5  # four classify-invoice plus rank-invoices
    assert scan["is_root"] is True


def test_ranking_is_deterministic():
    sample = [scan_inbox_trace(6), triage_email_trace()]
    assert _ranked(sample) == _ranked(sample)


def test_empty_sample_is_not_an_error():
    assert profile_capability_candidates([]) == []
    assert profile_capability_candidates([[]]) == []


def _lower_case_trace():
    """Braintrust spells its span types in lower case: ``function``, ``llm``."""
    capability = ObservationRecord(
        id="r",
        trace_id="t",
        parent_observation_id=None,
        type="function",
        name="run_invoice_capability",
        start_time="2026-01-01T00:00:00Z",
        end_time=None,
        is_root_observation=True,
    )
    call = ObservationRecord(
        id="c",
        trace_id="t",
        parent_observation_id="r",
        type="llm",
        name="Chat Completion",
        start_time="2026-01-01T00:00:01Z",
        end_time=None,
    )
    return [capability, call]


def test_a_lower_case_provider_vocabulary_still_separates_capabilities_from_model_calls():
    sample = [_lower_case_trace() for _ in range(3)]
    ranked = profile_capability_candidates(sample, BRAINTRUST)

    capability, call = ranked[0], ranked[-1]
    assert capability["name"] == "run_invoice_capability"
    assert call["name"] == "Chat Completion"
    # Both signals have to fire, or a bare model call ties with the capability above it.
    assert capability["model_calls"] == 1
    assert any("model calls" in r for r in capability["reasons"])
    assert any("is a model call" in r for r in call["reasons"])
    assert capability["score"] > call["score"]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
