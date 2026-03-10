"""Tests for the span evaluation logic."""

import pytest
import json
from unittest.mock import MagicMock


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
