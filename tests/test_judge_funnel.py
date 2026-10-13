import pytest
from pydantic import BaseModel

from overbae.services.judging import funnel
from overbae.services.judging.funnel import ResolvedJudge, parse_structured, sanitize_raw


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


def test_judge_preserves_oversized_evidence(fake_llm):
    prompt = "RUBRIC " + ("evidence " * 50000) + " RETURN JSON"
    fake_llm.on(lambda r: True, '{"explanation":"ok","score":7}')
    funnel.invoke_judge(prompt, response_format=_Verdict, judge=_judge())
    assert prompt in fake_llm.requests[-1].text
