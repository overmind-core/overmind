"""Tests for the span evaluation logic.

Covers:
- _evaluate_correctness_with_llm return shape, threshold boundaries, clamping
- _store_span_score / _store_span_error (reason storage, error clearing)
- CorrectnessResult Pydantic model
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overmind.tasks.evaluations import (
    REASON_SCORE_THRESHOLD,
    CorrectnessResult,
    _evaluate_correctness_with_llm,
)

_SIMPLE_INPUT = {"role": "user", "content": "What is 2+2?"}
_SIMPLE_OUTPUT = {"role": "assistant", "content": "4"}
_CRITERIA = "- Rule 1: The answer must be mathematically correct."


def _fake_call_llm(correctness: float, reason: str = "") -> MagicMock:
    """Return a mock for call_llm that yields the given correctness + reason."""
    payload = {"correctness": correctness}
    if reason:
        payload["reason"] = reason
    mock = MagicMock(return_value=(json.dumps(payload), None))
    return mock


# ---------------------------------------------------------------------------
# Parametrized / monkeypatch-based tests (original)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "llm_response,expected_score,expected_reason",
    [
        (json.dumps({"correctness": 0.85}), 0.85, None),
        (json.dumps({"correctness": 1.0}), 1.0, None),
        (
            json.dumps({"correctness": 0.3, "reason": "Answer was incomplete."}),
            0.3,
            "Answer was incomplete.",
        ),
        (
            json.dumps({"correctness": 0.0, "reason": "Completely wrong."}),
            0.0,
            "Completely wrong.",
        ),
    ],
    ids=[
        "high-score-no-reason",
        "perfect-score-no-reason",
        "low-score-with-reason",
        "zero-score-with-reason",
    ],
)
def test_evaluate_correctness_returns_score_and_reason(
    monkeypatch, llm_response, expected_score, expected_reason
):
    """_evaluate_correctness_with_llm returns (score, reason) tuple; reason only populated below threshold."""
    from overmind.tasks.evaluations import _evaluate_correctness_with_llm

    monkeypatch.setattr("overmind.config.settings.openai_api_key", "sk-test")

    mock_call = MagicMock(
        return_value=(
            llm_response,
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "response_ms": 200,
                "response_cost": 0.005,
            },
        )
    )
    monkeypatch.setattr("overmind.tasks.evaluations.call_llm", mock_call)

    score, reason = _evaluate_correctness_with_llm(
        input_data={"messages": [{"role": "user", "content": "What is 2+2?"}]},
        output_data={"content": "4"},
        criteria_text="Must give correct mathematical answers.",
        agent_description="A math tutor",
    )

    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0
    assert score == expected_score
    assert reason == expected_reason
    mock_call.assert_called_once()


def test_evaluate_correctness_reason_omitted_above_threshold(monkeypatch):
    """reason is None when score >= REASON_SCORE_THRESHOLD even if LLM includes one."""
    from overmind.tasks.evaluations import (
        _evaluate_correctness_with_llm,
        REASON_SCORE_THRESHOLD,
    )

    monkeypatch.setattr("overmind.config.settings.openai_api_key", "sk-test")

    # LLM spuriously returns a reason for a high score — should be ignored
    llm_response = json.dumps(
        {"correctness": REASON_SCORE_THRESHOLD, "reason": "Should be ignored."}
    )
    mock_call = MagicMock(
        return_value=(
            llm_response,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "response_ms": 100,
                "response_cost": 0.001,
            },
        )
    )
    monkeypatch.setattr("overmind.tasks.evaluations.call_llm", mock_call)

    score, reason = _evaluate_correctness_with_llm(
        input_data={"messages": [{"role": "user", "content": "Hello"}]},
        output_data={"content": "Hi"},
        criteria_text="Be friendly.",
        agent_description=None,
    )

    assert score == REASON_SCORE_THRESHOLD
    assert reason is None


def test_evaluate_correctness_handles_llm_error(monkeypatch):
    """When call_llm raises, the function re-raises after retries."""
    from overmind.tasks.evaluations import _evaluate_correctness_with_llm

    monkeypatch.setattr("overmind.config.settings.openai_api_key", "sk-test")

    mock_call = MagicMock(side_effect=ValueError("LLM API down"))
    monkeypatch.setattr("overmind.tasks.evaluations.call_llm", mock_call)

    with pytest.raises((ValueError, Exception)):
        _evaluate_correctness_with_llm(
            input_data={"messages": [{"role": "user", "content": "Hello"}]},
            output_data={"content": "Hi"},
            criteria_text="Must be friendly.",
            agent_description=None,
        )


# ---------------------------------------------------------------------------
# Patch-based unit tests (threshold, _store_span_score, _store_span_error)
# ---------------------------------------------------------------------------


class TestReasonScoreThresholdBoundary:
    """score == REASON_SCORE_THRESHOLD (0.5) must NOT carry a reason."""

    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_exact_threshold_no_reason(self, mock_call_llm, _mock_resolve):
        mock_call_llm.return_value = _fake_call_llm(
            REASON_SCORE_THRESHOLD, reason="Borderline response."
        ).return_value
        score, reason = _evaluate_correctness_with_llm(
            _SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA
        )
        assert score == pytest.approx(REASON_SCORE_THRESHOLD)
        assert reason is None, "score == threshold must NOT include a reason"

    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_just_below_threshold_has_reason(self, mock_call_llm, _mock_resolve):
        just_below = REASON_SCORE_THRESHOLD - 0.01
        mock_call_llm.return_value = _fake_call_llm(
            just_below, reason="Slightly off."
        ).return_value
        score, reason = _evaluate_correctness_with_llm(
            _SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA
        )
        assert score < REASON_SCORE_THRESHOLD
        assert reason == "Slightly off."

    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_just_above_threshold_no_reason(self, mock_call_llm, _mock_resolve):
        just_above = REASON_SCORE_THRESHOLD + 0.01
        mock_call_llm.return_value = _fake_call_llm(
            just_above, reason="Should be ignored."
        ).return_value
        _, reason = _evaluate_correctness_with_llm(
            _SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA
        )
        assert reason is None


class TestEvaluateCorrectnessErrors:
    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_missing_correctness_key_raises(self, mock_call_llm, _mock_resolve):
        mock_call_llm.return_value = (json.dumps({"something": "else"}), None)
        with pytest.raises(ValueError, match="missing correctness"):
            _evaluate_correctness_with_llm(_SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA)

    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_non_numeric_correctness_raises(self, mock_call_llm, _mock_resolve):
        mock_call_llm.return_value = (json.dumps({"correctness": "high"}), None)
        with pytest.raises(ValueError, match="not a number"):
            _evaluate_correctness_with_llm(_SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA)

    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_score_clamped_above_one(self, mock_call_llm, _mock_resolve):
        mock_call_llm.return_value = _fake_call_llm(1.5).return_value
        score, _ = _evaluate_correctness_with_llm(
            _SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA
        )
        assert score == pytest.approx(1.0)

    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_score_clamped_below_zero(self, mock_call_llm, _mock_resolve):
        mock_call_llm.return_value = _fake_call_llm(-0.5).return_value
        score, _ = _evaluate_correctness_with_llm(
            _SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA
        )
        assert score == pytest.approx(0.0)


@pytest.mark.asyncio
class TestStoreSpanScore:
    """_store_span_score must persist reasons and clear stale ones on re-score."""

    async def _make_span(self, initial_feedback: dict) -> SimpleNamespace:
        return SimpleNamespace(span_id="s1", feedback_score=dict(initial_feedback))

    async def _run_store(self, span, correctness, reason=None):
        from overmind.tasks.evaluations import _store_span_score

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = span
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        mock_session_local = MagicMock(return_value=mock_session)

        with patch(
            "overmind.tasks.evaluations.get_session_local",
            return_value=mock_session_local,
        ):
            return await _store_span_score("s1", correctness, reason)

    async def test_stores_reason_for_low_score(self):
        span = await self._make_span({})
        await self._run_store(span, correctness=0.2, reason="Too vague.")
        assert span.feedback_score["correctness"] == pytest.approx(0.2)
        assert span.feedback_score["correctness_reason"] == "Too vague."

    async def test_clears_reason_when_rescored_high(self):
        span = await self._make_span(
            {"correctness": 0.2, "correctness_reason": "old reason"}
        )
        await self._run_store(span, correctness=0.8, reason=None)
        assert span.feedback_score["correctness"] == pytest.approx(0.8)
        assert "correctness_reason" not in span.feedback_score

    async def test_clears_error_on_successful_score(self):
        span = await self._make_span({"correctness_error": "parse failed"})
        await self._run_store(span, correctness=0.7, reason=None)
        assert "correctness_error" not in span.feedback_score
        assert span.feedback_score["correctness"] == pytest.approx(0.7)

    async def test_replaces_stale_reason_with_new_reason(self):
        span = await self._make_span(
            {"correctness": 0.1, "correctness_reason": "stale"}
        )
        await self._run_store(span, correctness=0.3, reason="updated reason")
        assert span.feedback_score["correctness_reason"] == "updated reason"


@pytest.mark.asyncio
class TestStoreSpanError:
    """_store_span_error must remove both correctness and correctness_reason."""

    async def _run_store_error(self, span, error: str):
        from overmind.tasks.evaluations import _store_span_error

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = span
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        mock_session_local = MagicMock(return_value=mock_session)

        with patch(
            "overmind.tasks.evaluations.get_session_local",
            return_value=mock_session_local,
        ):
            return await _store_span_error("s1", error)

    async def test_sets_error_clears_score_and_reason(self):
        span = SimpleNamespace(
            span_id="s1",
            feedback_score={"correctness": 0.3, "correctness_reason": "old reason"},
        )
        await self._run_store_error(span, "JSON parse failed after 3 retries")
        assert (
            span.feedback_score.get("correctness_error")
            == "JSON parse failed after 3 retries"
        )
        assert "correctness" not in span.feedback_score
        assert "correctness_reason" not in span.feedback_score

    async def test_error_overwrites_prior_error(self):
        span = SimpleNamespace(
            span_id="s1",
            feedback_score={"correctness_error": "old error"},
        )
        await self._run_store_error(span, "new error")
        assert span.feedback_score["correctness_error"] == "new error"

    async def test_returns_false_for_missing_span(self):
        from overmind.tasks.evaluations import _store_span_error

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_session_local = MagicMock(return_value=mock_session)

        with patch(
            "overmind.tasks.evaluations.get_session_local",
            return_value=mock_session_local,
        ):
            result = await _store_span_error("missing-id", "some error")
        assert result is False


class TestCorrectnessResultModel:
    def test_reason_defaults_to_empty_string(self):
        r = CorrectnessResult(correctness=0.9)
        assert r.reason == ""

    def test_reason_accepts_string(self):
        r = CorrectnessResult(correctness=0.2, reason="Not enough detail.")
        assert r.reason == "Not enough detail."

    def test_correctness_required(self):
        with pytest.raises(Exception):
            CorrectnessResult()
