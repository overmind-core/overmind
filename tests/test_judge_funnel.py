import time

import pytest
from pydantic import BaseModel

from overbae.services.eval import funnel
from overbae.services.eval.funnel import (
    JudgeExecutor,
    JudgeTask,
    ResolvedJudge,
    geometric_median,
    judge_contract_identifier,
    normalize_unit_interval,
    parse_structured,
    sanitize_raw,
)


class _Verdict(BaseModel):
    explanation: str
    score: int


def _judge(family: str = "unknown") -> ResolvedJudge:
    return ResolvedJudge(model_name="test/model-x", model_spec=None, family=family)


def test_sanitize_strips_nul():
    assert sanitize_raw('{"a": "b\x00c\\u0000d"}') == '{"a": "bcd"}'


def test_parse_structured_repairs_fenced_json():
    raw = 'Sure! ```json\n{"explanation": "ok", "score": 7}\n```'
    parsed = parse_structured(raw, _Verdict)
    assert parsed is not None and parsed.score == 7


def test_parse_structured_returns_none_on_garbage():
    assert parse_structured("not json at all", _Verdict) is None


def test_normalize_unit_interval():
    assert normalize_unit_interval(5, 0, 10) == 0.5
    assert normalize_unit_interval(-3, 0, 10) == 0.0
    assert normalize_unit_interval(42, 0, 10) == 1.0
    assert normalize_unit_interval(1, 1, 1) == 0.0


def test_geometric_median_is_1d_median():
    assert geometric_median([0.0, 0.4, 1.0]) == 0.4


def test_contract_identifier_uses_resolved_model_and_family():
    assert judge_contract_identifier(_judge("openai"), "abc123") == "openai:test/model-x:abc123"


def test_executor_retries_then_succeeds():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("boom")
        return "ok"

    results = JudgeExecutor(max_workers=2, max_retries=2).run(
        [JudgeTask(key="a", fn=flaky), JudgeTask(key="b", fn=lambda: "fine")]
    )
    assert results["a"].ok and results["a"].value == "ok"
    assert results["b"].value == "fine"


def test_executor_surfaces_persistent_error():
    def broken():
        raise RuntimeError("always")

    results = JudgeExecutor(max_workers=1, max_retries=1).run([JudgeTask(key="x", fn=broken)])
    assert not results["x"].ok
    assert isinstance(results["x"].error, RuntimeError)


def test_executor_defers_past_deadline():
    results = JudgeExecutor(max_workers=1, max_retries=0).run(
        [
            JudgeTask(key="slow", fn=lambda: time.sleep(0.5) or "done"),
            JudgeTask(key="starved", fn=lambda: "never-admitted"),
        ],
        timeout_s=0.15,
    )
    assert results["starved"].deferred


@pytest.mark.parametrize(
    ("message", "limited"),
    [("HTTP 429 too many requests", True), ("rate limit exceeded", True), ("boom", False)],
)
def test_rate_limit_detection(message, limited):
    assert funnel.is_rate_limited(RuntimeError(message)) is limited


@pytest.mark.parametrize(
    ("message", "permanent"),
    [
        ("Error code: 400 - maximum context length is 400000 tokens", True),
        ("context_length_exceeded", True),
        ("the prompt is too long for this model", True),
        ("HTTP 429 too many requests", False),
        ("connection reset by peer", False),
    ],
)
def test_permanent_error_detection(message, permanent):
    assert funnel.is_permanent_error(RuntimeError(message)) is permanent


def test_executor_does_not_retry_permanent_errors():
    attempts = {"n": 0}

    def overflow():
        attempts["n"] += 1
        raise RuntimeError("maximum context length is 400000 tokens")

    results = JudgeExecutor(max_workers=1, max_retries=2).run([JudgeTask(key="x", fn=overflow)])
    assert not results["x"].ok
    assert attempts["n"] == 1


def test_judge_preserves_oversized_evidence(monkeypatch):
    from unittest.mock import Mock

    prompt = "RUBRIC " + ("evidence " * 50000) + " RETURN JSON"
    completion = Mock(return_value=('{"explanation":"ok","score":7}', {}))
    monkeypatch.setattr(funnel, "call_llm", completion)
    funnel.invoke_judge(prompt, response_format=_Verdict, judge=_judge(), use_cache=False)
    assert completion.call_args.args[0] == prompt
