"""Tests for overmind.core.llms.call_llm."""

from unittest.mock import MagicMock, patch

import litellm
import pytest
from tenacity import RetryCallState

from overmind.core.llms import (
    _do_litellm_completion,
    _should_retry_llm_call,
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


@pytest.mark.parametrize(
    "model,budget_tokens",
    [
        ("claude-opus-4-5", 8000),  # Anthropic 4.5: only [8000]
        ("gemini-2.5-flash", -1),  # Gemini 2.5 Flash: only [-1] (dynamic)
    ],
)
def test_call_llm_passes_thinking_budget_tokens_for_manual_mode(
    mock_completion, model, budget_tokens: int
):
    call_llm("hello", model=model, thinking_budget_tokens=budget_tokens)

    positional_kwargs = mock_completion.call_args.kwargs
    thinking_param = positional_kwargs.get("thinking")
    assert thinking_param is not None, (
        f"Expected thinking param to be set for {model} with budget_tokens={budget_tokens}"
    )
    assert thinking_param == {"type": "enabled", "budget_tokens": budget_tokens}
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


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

# Minimal litellm exception factories.
# Use __new__ to skip the complex parent constructor (which needs httpx objects),
# then set the `message` attribute that litellm exceptions rely on for __str__.


def _make_litellm_exc(cls, msg: str):
    e = cls.__new__(cls)
    Exception.__init__(e, msg)
    # litellm exceptions use these in __str__; set to None/0 to avoid AttributeError
    e.message = msg
    e.num_retries = None
    e.max_retries = None
    return e


def _rate_limit_err(msg="rate limited") -> litellm.RateLimitError:
    return _make_litellm_exc(litellm.RateLimitError, msg)


def _internal_server_err(msg="internal server error") -> litellm.InternalServerError:
    return _make_litellm_exc(litellm.InternalServerError, msg)


def _service_unavailable_err(
    msg="service unavailable",
) -> litellm.ServiceUnavailableError:
    return _make_litellm_exc(litellm.ServiceUnavailableError, msg)


def _api_connection_err(msg="connection error") -> litellm.APIConnectionError:
    return _make_litellm_exc(litellm.APIConnectionError, msg)


def _bad_request_err(msg="bad request") -> litellm.BadRequestError:
    return _make_litellm_exc(litellm.BadRequestError, msg)


def _make_retry_state(exc=None) -> RetryCallState:
    """Build a minimal RetryCallState mock with a given outcome exception."""
    outcome = MagicMock()
    outcome.exception.return_value = exc
    state = MagicMock(spec=RetryCallState)
    state.outcome = outcome
    return state


@pytest.fixture()
def no_wait_retry():
    """Swap tenacity's sleep on _do_litellm_completion to a no-op so retry tests are instant."""
    original = _do_litellm_completion.retry.sleep
    _do_litellm_completion.retry.sleep = lambda _: None
    yield
    _do_litellm_completion.retry.sleep = original


# -- _should_retry_llm_call predicate --


def test_retry_predicate_returns_false_on_success():
    assert _should_retry_llm_call(_make_retry_state(exc=None)) is False


@pytest.mark.parametrize(
    "exc_factory",
    [
        _rate_limit_err,
        _internal_server_err,
        _service_unavailable_err,
        _api_connection_err,
    ],
    ids=[
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
        "APIConnectionError",
    ],
)
def test_retry_predicate_returns_true_for_retryable_errors(exc_factory):
    assert _should_retry_llm_call(_make_retry_state(exc=exc_factory())) is True


@pytest.mark.parametrize(
    "exc",
    [
        _bad_request_err(),
        ValueError("bad value"),
        RuntimeError("unexpected"),
    ],
    ids=["BadRequestError", "ValueError", "RuntimeError"],
)
def test_retry_predicate_returns_false_for_non_retryable_errors(exc):
    assert _should_retry_llm_call(_make_retry_state(exc=exc)) is False


# -- call_llm retry integration --


def test_call_llm_retries_on_rate_limit_and_succeeds(no_wait_retry):
    success = _make_completion_response("ok")
    with patch(
        "litellm.completion", side_effect=[_rate_limit_err(), success]
    ) as mock_comp:
        content, _ = call_llm("hello", model="gpt-5-mini")

    assert content == "ok"
    assert mock_comp.call_count == 2


def test_call_llm_retries_on_overloaded_internal_server_error_and_succeeds(
    no_wait_retry,
):
    """Covers the Anthropic overloaded_error path (litellm.InternalServerError)."""
    overloaded = _internal_server_err(
        'AnthropicError - {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'
    )
    success = _make_completion_response("recovered")
    with patch("litellm.completion", side_effect=[overloaded, success]) as mock_comp:
        content, _ = call_llm("hello", model="claude-haiku-4-5")

    assert content == "recovered"
    assert mock_comp.call_count == 2


def test_call_llm_does_not_retry_bad_request_error(no_wait_retry):
    """Non-retryable errors should propagate immediately without a second attempt."""
    with patch("litellm.completion", side_effect=_bad_request_err()) as mock_comp:
        with pytest.raises(Exception):
            call_llm("hello", model="gpt-5-mini")

    assert mock_comp.call_count == 1


def test_call_llm_reraises_after_retries_exhausted(no_wait_retry, monkeypatch):
    """When all retry attempts fail the original error is reraised."""
    # Limit to 2 attempts so the test terminates quickly.
    import tenacity

    monkeypatch.setattr(
        _do_litellm_completion.retry,
        "stop",
        tenacity.stop_after_attempt(2),
    )
    err = _rate_limit_err("still rate limited")
    with patch("litellm.completion", side_effect=err):
        # call_llm wraps the re-raised error as "Error calling LLM: <original>"
        with pytest.raises(Exception, match="Error calling LLM.*still rate limited"):
            call_llm("hello", model="gpt-5-mini")
