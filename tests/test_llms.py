"""Tests for overmind.core.llms.call_llm."""

from unittest.mock import MagicMock, patch

import pytest

from overmind.core.llms import call_llm


def _make_completion_response(content: str = "Hello") -> MagicMock:
    """Build a minimal litellm-style response mock."""
    message = MagicMock()
    message.content = content
    message.tool_calls = None

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    response.usage.prompt_tokens = 10
    response.usage.completion_tokens = 5
    response._response_ms = 120.0
    response._hidden_params = {"response_cost": 0.001}
    return response


@pytest.fixture()
def mock_completion():
    with patch("overmind.core.llms.completion") as m:
        m.return_value = _make_completion_response()
        yield m


@pytest.mark.parametrize(
    "model",
    ["claude-opus-4-6", "claude-sonnet-4-5", "claude-haiku-4-5"],
    ids=["claude-opus", "claude-sonnet", "claude-haiku"],
)
def test_anthropic_sets_cache_control(mock_completion, model):
    call_llm("hello", model=model)

    positional_kwargs = mock_completion.call_args.kwargs

    assert positional_kwargs.get("cache_control") == {"type": "ephemeral"}, (
        f"Expected cache_control to be set for Anthropic model {model}"
    )


@pytest.mark.parametrize(
    "model",
    ["gpt-4.1", "gemini-2.5-flash"],
    ids=["openai", "gemini"],
)
def test_non_anthropic_does_not_set_cache_control(mock_completion, model):
    call_llm("hello", model=model)

    positional_kwargs = mock_completion.call_args.kwargs

    assert "cache_control" not in positional_kwargs, (
        f"cache_control should NOT be set for non-Anthropic model {model}"
    )
