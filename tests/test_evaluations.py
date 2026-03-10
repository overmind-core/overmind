"""
Unit tests for the evaluations module — focused on the reason feature
introduced alongside correctness scoring.

Tests cover:
- _evaluate_correctness_with_llm returning (score, reason) pairs
- REASON_SCORE_THRESHOLD boundary: score == 0.5 must NOT carry a reason
- _store_span_score storing / clearing reasons on re-score
- _store_span_error clearing a stale reason and score
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overmind.tasks.evaluations import (
    REASON_SCORE_THRESHOLD,
    CorrectnessResult,
    _evaluate_correctness_with_llm,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_INPUT = {"role": "user", "content": "What is 2+2?"}
_SIMPLE_OUTPUT = {"role": "assistant", "content": "4"}
_CRITERIA = "- Rule 1: The answer must be mathematically correct."


def _fake_call_llm(correctness: float, reason: str = "") -> MagicMock:
    """Return a mock for call_llm that yields the given correctness + reason."""
    import json

    payload = {"correctness": correctness}
    if reason:
        payload["reason"] = reason
    mock = MagicMock(return_value=(json.dumps(payload), None))
    return mock


# ---------------------------------------------------------------------------
# _evaluate_correctness_with_llm — return-value shape
# ---------------------------------------------------------------------------


class TestEvaluateCorrectnessReturnShape:
    """_evaluate_correctness_with_llm must always return tuple[float, str | None]."""

    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_returns_tuple(self, mock_call_llm, _mock_resolve):
        mock_call_llm.side_effect = _fake_call_llm(0.9).side_effect
        mock_call_llm.return_value = _fake_call_llm(0.9).return_value
        result = _evaluate_correctness_with_llm(
            _SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA
        )
        assert isinstance(result, tuple)
        assert len(result) == 2

    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_high_score_no_reason(self, mock_call_llm, _mock_resolve):
        """Scores >= REASON_SCORE_THRESHOLD must return reason=None."""
        mock_call_llm.return_value = _fake_call_llm(
            0.9, reason="some reason"
        ).return_value
        score, reason = _evaluate_correctness_with_llm(
            _SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA
        )
        assert score == pytest.approx(0.9)
        assert reason is None

    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_low_score_with_reason(self, mock_call_llm, _mock_resolve):
        """Scores < REASON_SCORE_THRESHOLD must surface the reason string."""
        mock_call_llm.return_value = _fake_call_llm(
            0.2, reason="The answer was incorrect."
        ).return_value
        score, reason = _evaluate_correctness_with_llm(
            _SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA
        )
        assert score == pytest.approx(0.2)
        assert reason == "The answer was incorrect."

    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_low_score_without_reason_field(self, mock_call_llm, _mock_resolve):
        """Low score with no reason in the payload → reason=None (not an error)."""
        import json

        mock_call_llm.return_value = (json.dumps({"correctness": 0.1}), None)
        score, reason = _evaluate_correctness_with_llm(
            _SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA
        )
        assert score == pytest.approx(0.1)
        assert reason is None

    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_low_score_empty_reason_normalised_to_none(
        self, mock_call_llm, _mock_resolve
    ):
        """An empty-string reason is normalised to None."""
        mock_call_llm.return_value = _fake_call_llm(0.3, reason="   ").return_value
        _, reason = _evaluate_correctness_with_llm(
            _SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA
        )
        assert reason is None


# ---------------------------------------------------------------------------
# REASON_SCORE_THRESHOLD boundary
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


# ---------------------------------------------------------------------------
# ValueError propagation
# ---------------------------------------------------------------------------


class TestEvaluateCorrectnessErrors:
    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_missing_correctness_key_raises(self, mock_call_llm, _mock_resolve):
        import json

        mock_call_llm.return_value = (json.dumps({"something": "else"}), None)
        with pytest.raises(ValueError, match="missing correctness"):
            _evaluate_correctness_with_llm(_SIMPLE_INPUT, _SIMPLE_OUTPUT, _CRITERIA)

    @patch("overmind.tasks.evaluations.resolve_model", return_value="gpt-test")
    @patch("overmind.tasks.evaluations.call_llm")
    def test_non_numeric_correctness_raises(self, mock_call_llm, _mock_resolve):
        import json

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


# ---------------------------------------------------------------------------
# _store_span_score — reason storage and clearing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestStoreSpanScore:
    """_store_span_score must persist reasons and clear stale ones on re-score."""

    async def _make_span(self, initial_feedback: dict) -> SimpleNamespace:
        span = SimpleNamespace(span_id="s1", feedback_score=dict(initial_feedback))
        return span

    async def _run_store(self, span, correctness, reason=None):
        """Run _store_span_score against an in-memory span without a real DB."""
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


# ---------------------------------------------------------------------------
# _store_span_error — clears score and reason
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# CorrectnessResult Pydantic model
# ---------------------------------------------------------------------------


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
