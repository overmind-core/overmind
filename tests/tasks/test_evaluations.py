"""Tests for the span evaluation logic."""

import pytest
import json
from unittest.mock import MagicMock


def test_evaluate_correctness_returns_score(monkeypatch):
    """The core LLM judge call should parse a correctness score from the response."""
    from overmind.tasks.evaluations import _evaluate_correctness_with_llm

    fake_response = json.dumps({"correctness": 0.85})

    monkeypatch.setattr("overmind.config.settings.openai_api_key", "sk-test")

    mock_call = MagicMock(
        return_value=(
            fake_response,
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "response_ms": 200,
                "response_cost": 0.005,
            },
        )
    )
    monkeypatch.setattr("overmind.tasks.evaluations.call_llm", mock_call)

    result = _evaluate_correctness_with_llm(
        input_data={"messages": [{"role": "user", "content": "What is 2+2?"}]},
        output_data={"content": "4"},
        criteria_text="Must give correct mathematical answers.",
        agent_description="A math tutor",
    )

    assert result is not None
    assert isinstance(result, float)
    assert 0.0 <= result <= 1.0
    mock_call.assert_called_once()


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
