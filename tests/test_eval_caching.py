from __future__ import annotations

import pytest

from overbae.services.eval import funnel as judging
from overbae.services.eval.cascade import resolve_config
from overbae.services.eval.evaluators.base import JudgeResult


class TestPresets:
    def test_fast_disables_per_step(self):
        cfg = resolve_config({"preset": "fast"})
        assert cfg["long_trace_strategy"] == "final_only"

    def test_default_is_balanced(self):
        cfg = resolve_config({})
        assert cfg["long_trace_strategy"] == "auto"
        assert cfg["judge_salient_only"] is True
        assert cfg["max_steps_judged"] == 12

    def test_explicit_overrides_preset(self):
        cfg = resolve_config({"preset": "fast", "long_trace_strategy": "agentic"})
        assert cfg["long_trace_strategy"] == "agentic"

    def test_thorough_judges_all_steps(self):
        cfg = resolve_config({"preset": "thorough"})
        assert cfg["judge_salient_only"] is False
        assert cfg["max_steps_judged"] == 0


@pytest.mark.django_db
def test_judge_cache_serves_second_call(monkeypatch):
    calls = []

    def fake_call_llm(prompt, **kw):
        calls.append(prompt)
        return (
            '{"items":[],"score":0.7,"reasoning":"ok","label":"","evidence":[]}',
            {"response_cost": 0.01, "prompt_tokens": 5, "completion_tokens": 3},
        )

    monkeypatch.setattr(judging, "call_llm", fake_call_llm)
    judge = judging.ResolvedJudge(model_name="gpt-5-mini", model_spec=None, family="openai")

    o1 = judging.invoke_judge("grade this", response_format=JudgeResult, judge=judge)
    o2 = judging.invoke_judge("grade this", response_format=JudgeResult, judge=judge)

    assert len(calls) == 1, "identical second call must hit the cache, not the LLM"
    assert o1.cached is False
    assert o2.cached is True
    assert o2.stats["response_cost"] == 0.0
    assert o2.parsed.score == 0.7


@pytest.mark.django_db
def test_judge_cache_key_varies_by_prompt(monkeypatch):
    calls = []

    def fake_call_llm(prompt, **kw):
        calls.append(prompt)
        return (
            '{"items":[],"score":0.5,"reasoning":"","label":"","evidence":[]}',
            {"response_cost": 0.01},
        )

    monkeypatch.setattr(judging, "call_llm", fake_call_llm)
    judge = judging.ResolvedJudge(model_name="gpt-5-mini", model_spec=None, family="openai")

    judging.invoke_judge("prompt A", response_format=JudgeResult, judge=judge)
    judging.invoke_judge("prompt B", response_format=JudgeResult, judge=judge)
    assert len(calls) == 2, "different prompts must not share a cache entry"
