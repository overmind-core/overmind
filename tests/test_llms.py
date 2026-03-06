"""Tests for overmind.core.llms.call_llm."""

from unittest.mock import MagicMock, patch

import pytest

from overmind.core.llms import (
    call_llm,
    get_reasoning_levels,
    get_thinking_budget_tokens,
    is_adaptive_mode,
    is_reasoning_required,
    model_supports_reasoning,
    SUPPORTED_LLM_MODELS,
)


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
    with patch("litellm.completion") as m:
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


def test_all_models_have_reasoning_metadata():
    """Every model has supports_reasoning; reasoning models have adaptive_mode + levels or budget_tokens."""
    for item in SUPPORTED_LLM_MODELS:
        assert "supports_reasoning" in item
        assert isinstance(item["supports_reasoning"], bool)
        if item["supports_reasoning"]:
            assert "adaptive_mode" in item, (
                f"Model {item['model_name']} missing adaptive_mode"
            )
            assert isinstance(item["adaptive_mode"], bool)
            if item["adaptive_mode"]:
                assert "reasoning_levels" in item and item["reasoning_levels"], (
                    f"Adaptive model {item['model_name']} needs reasoning_levels"
                )
            else:
                assert (
                    "thinking_budget_tokens" in item and item["thinking_budget_tokens"]
                ), f"Manual model {item['model_name']} needs thinking_budget_tokens"


def test_model_supports_reasoning():
    assert model_supports_reasoning("gpt-5-mini") is True
    assert model_supports_reasoning("claude-sonnet-4-6") is True
    assert model_supports_reasoning("gemini-3-flash-preview") is True
    assert model_supports_reasoning("gemini-2.5-flash-lite") is False


def test_get_reasoning_levels():
    assert get_reasoning_levels("gpt-5-mini") == ["low", "medium", "high"]
    assert get_reasoning_levels("gemini-3.1-pro-preview") == ["low", "medium", "high"]
    assert get_reasoning_levels("claude-opus-4-6") == [
        "low",
        "medium",
        "high",
        "max",
    ]
    assert (
        get_reasoning_levels("claude-opus-4-5") == []
    )  # manual mode: no reasoning_effort
    assert get_reasoning_levels("gemini-2.5-flash-lite") == []  # no thinking support


def test_get_thinking_budget_tokens():
    assert get_thinking_budget_tokens("claude-opus-4-5") == [8000]
    assert get_thinking_budget_tokens("claude-sonnet-4-5") == [8000]
    assert get_thinking_budget_tokens("claude-haiku-4-5") == [8000]
    assert get_thinking_budget_tokens("gemini-2.5-flash") == [-1]  # dynamic budget
    assert get_thinking_budget_tokens("claude-opus-4-6") == []  # adaptive_mode=True
    assert get_thinking_budget_tokens("gpt-5-mini") == []


def test_is_reasoning_required():
    assert is_reasoning_required("gemini-2.5-pro") is True
    assert is_reasoning_required("gpt-5-mini") is False
    assert is_reasoning_required("gemini-2.5-flash") is False


def test_is_adaptive_mode():
    assert is_adaptive_mode("claude-opus-4-6") is True
    assert is_adaptive_mode("claude-sonnet-4-6") is True
    assert is_adaptive_mode("gpt-5-mini") is True
    assert is_adaptive_mode("gemini-3-flash-preview") is True
    assert is_adaptive_mode("claude-opus-4-5") is False
    assert is_adaptive_mode("claude-haiku-4-5") is False
    assert is_adaptive_mode("gemini-2.5-flash") is False
    assert is_adaptive_mode("gpt-4.1") is None  # supports_reasoning=False


def test_call_llm_passes_reasoning_effort_when_supported(mock_completion):
    call_llm("hello", model="gpt-5-mini", reasoning_effort="low")

    positional_kwargs = mock_completion.call_args.kwargs
    assert positional_kwargs.get("reasoning_effort") == "low"


@pytest.mark.parametrize("model", ["claude-opus-4-5", "gemini-2.5-flash"])
@pytest.mark.parametrize("budget_tokens", [8000, -1])
def test_call_llm_passes_thinking_budget_tokens_for_manual_mode(
    mock_completion, model, budget_tokens: int
):
    call_llm("hello", model=model, thinking_budget_tokens=budget_tokens)

    positional_kwargs = mock_completion.call_args.kwargs
    # Check that thinking is passed with the correct structure
    thinking_param = positional_kwargs.get("thinking")
    if thinking_param is not None:
        assert thinking_param == {
            "type": "enabled",
            "budget_tokens": budget_tokens,
        }
    # Also check via extra_body for providers that use that pattern
    extra_body = positional_kwargs.get("extra_body", {})
    if extra_body and "thinking" in extra_body:
        assert extra_body["thinking"] == {
            "type": "enabled",
            "budget_tokens": budget_tokens,
        }
    assert "reasoning_effort" not in positional_kwargs


def test_call_llm_uses_default_reasoning_effort_when_required(mock_completion):
    call_llm("hello", model="gemini-2.5-pro")

    positional_kwargs = mock_completion.call_args.kwargs
    assert positional_kwargs.get("reasoning_effort") == "medium"


def test_call_llm_includes_reasoning_content_in_stats_when_present(mock_completion):
    mock_completion.return_value = _make_completion_response("Hi")
    msg = mock_completion.return_value.choices[0].message
    msg.reasoning_content = "Let me think about this..."

    content, stats = call_llm("hello", model="gpt-5-mini", reasoning_effort="low")

    assert stats.get("reasoning_content") == "Let me think about this..."
