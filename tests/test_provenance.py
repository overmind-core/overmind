"""Tests for overmind.optimize.provenance — source tags + confidence."""

from __future__ import annotations

import pytest

from overmind.optimize.provenance import (
    Confidence,
    SourceTag,
    TraceSource,
    aggregate_confidence,
    summarize_sources,
)


class TestTraceSource:
    def test_is_str_enum(self):
        assert TraceSource.REAL_SUBPROCESS == "real_subprocess"
        assert TraceSource.LLM_REAL.value == "llm_real"

    def test_all_values_unique(self):
        values = [m.value for m in TraceSource]
        assert len(values) == len(set(values))


class TestSourceTag:
    def test_to_from_dict(self):
        tag = SourceTag(source=TraceSource.LLM_REAL, reason="real model")
        d = tag.to_dict()
        assert d == {"source": "llm_real", "reason": "real model"}

        round_trip = SourceTag.from_dict(d)
        assert round_trip == tag

    def test_from_dict_handles_none(self):
        assert SourceTag.from_dict(None) is None
        assert SourceTag.from_dict({}) is None

    def test_from_dict_rejects_unknown_source(self):
        assert SourceTag.from_dict({"source": "nonsense", "reason": ""}) is None

    def test_is_hashable(self):
        t1 = SourceTag(source=TraceSource.CASSETTE, reason="r")
        t2 = SourceTag(source=TraceSource.CASSETTE, reason="r")
        assert {t1, t2} == {t1}


class TestAggregateConfidence:
    def test_empty_tags_defaults_to_real(self):
        conf = aggregate_confidence([])
        assert conf.score == 1.0
        assert conf.summary == {"real_subprocess": 1}
        assert "real" in conf.reason.lower()

    def test_all_real_llm(self):
        tags = [SourceTag(source=TraceSource.LLM_REAL) for _ in range(3)]
        conf = aggregate_confidence(tags)
        assert conf.score == pytest.approx(1.0)
        assert conf.summary == {"llm_real": 3}

    def test_mixed_sources(self):
        tags = [
            SourceTag(source=TraceSource.LLM_REAL),
            SourceTag(source=TraceSource.SIMULATED),
            SourceTag(source=TraceSource.CASSETTE),
        ]
        conf = aggregate_confidence(tags)
        # (1.0 + 0.45 + 0.85) / 3 ≈ 0.767
        assert 0.7 < conf.score < 0.85
        assert conf.summary == {"llm_real": 1, "simulated": 1, "cassette": 1}

    def test_all_simulated_is_low_confidence(self):
        tags = [SourceTag(source=TraceSource.SIMULATED) for _ in range(5)]
        conf = aggregate_confidence(tags)
        assert conf.score < 0.5
        assert conf.level == "low"

    def test_static_only_is_very_low(self):
        tags = [SourceTag(source=TraceSource.STATIC_ONLY) for _ in range(2)]
        conf = aggregate_confidence(tags)
        assert conf.score < 0.3

    def test_level_buckets(self):
        assert Confidence(score=0.9).level == "high"
        assert Confidence(score=0.7).level == "medium"
        assert Confidence(score=0.3).level == "low"


class TestSummarizeSources:
    def test_empty(self):
        assert summarize_sources([]) == {}
        assert summarize_sources(None) == {}

    def test_counts_all_kinds(self):
        tags = [
            SourceTag(source=TraceSource.LLM_REAL),
            SourceTag(source=TraceSource.LLM_REAL),
            SourceTag(source=TraceSource.CASSETTE),
        ]
        assert summarize_sources(tags) == {"llm_real": 2, "cassette": 1}
