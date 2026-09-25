from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import openai
import pytest
from pydantic import BaseModel
from tenacity import RetryCallState

from overbae.core import llms as _llms_module
from overbae.core.llms import _should_retry_llm_call, call_llm
from overbae.core.model_registry import CATALOG, MODELS_BY_NAME, reasoning_of


def _make_completion_response(content: str = "Hello") -> MagicMock:
    message = MagicMock()
    message.content = content
    message.tool_calls = None
    message.model_extra = {}
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    response.usage.prompt_tokens = 10
    response.usage.completion_tokens = 5
    response.usage.model_extra = {"cost": 0.001}
    response._response_ms = 120.0
    return response


@pytest.fixture()
def mock_openrouter_completion():
    client = MagicMock()
    create = client.chat.completions.create
    create.return_value = _make_completion_response()
    with (
        patch("overbae.core.llms._openrouter_client", return_value=client),
        patch("overbae.core.llms._provider_client", return_value=client),
    ):
        yield create


def test_reasoning_metadata_reads_from_the_registry():
    assert reasoning_of("gpt-5-mini").adaptive is True
    assert reasoning_of("gpt-5-mini").levels == ("low", "medium", "high")
    assert reasoning_of("claude-opus-4-6").levels == ("low", "medium", "high", "max")
    assert reasoning_of("claude-opus-4-5") == reasoning_of("claude-haiku-4-5")
    assert reasoning_of("claude-opus-4-5").budgets == (8000,)
    assert reasoning_of("gemini-2.5-flash").adaptive is False
    assert reasoning_of("gemini-2.5-pro").required is True
    assert reasoning_of("gpt-4.1").adaptive is None
    assert reasoning_of("gemini-2.5-flash-lite").adaptive is None
    assert reasoning_of("gpt-5-mini-2026-01-01") == reasoning_of("gpt-5-mini")


def test_call_llm_passes_reasoning_effort_when_supported(mock_openrouter_completion):
    call_llm("hello", model="gpt-5-mini", reasoning_effort="low")
    assert mock_openrouter_completion.call_args.kwargs["extra_body"]["reasoning"] == {
        "effort": "low"
    }


def test_call_llm_uses_default_model():
    response = _make_completion_response("ok")
    client = MagicMock()
    client.chat.completions.create.return_value = response
    with (
        patch("overbae.core.llms._openrouter_client", return_value=client),
        patch("overbae.core.llms._get_default_model", return_value="gpt-5-mini"),
    ):
        content, stats = _llms_module.call_llm("hello")
    assert content == "ok"
    assert stats["prompt_tokens"] == 10
    assert stats["response_cost"] == 0.001
    assert client.chat.completions.create.call_args.kwargs["model"] == "openai/gpt-5-mini"


@pytest.mark.parametrize("cost", [None, 0.0, 0.001])
@pytest.mark.parametrize("with_tools", [False, True])
def test_usage_distinguishes_missing_cost_from_reported_zero(
    mock_openrouter_completion, cost, with_tools
):
    mock_openrouter_completion.return_value.usage = SimpleNamespace(
        prompt_tokens=10, completion_tokens=5, model_extra={} if cost is None else {"cost": cost}
    )
    if with_tools:
        _, _, stats = _llms_module.call_llm_tools(
            [{"role": "user", "content": "hello"}], [], model="gpt-5-mini", retry_deadline=0
        )
    else:
        _, stats = call_llm("hello", model="gpt-5-mini")
    assert stats["response_cost"] == cost


class _DummyResponseFormat(BaseModel):
    status: str


def test_call_llm_uses_model_and_response_format(mock_openrouter_completion):
    content, _ = _llms_module.call_llm(
        "hello",
        system_prompt="system",
        model="gpt-5",
        response_format=_DummyResponseFormat,
    )
    kwargs = mock_openrouter_completion.call_args.kwargs
    assert content == "Hello"
    assert kwargs["model"] == "openai/gpt-5"
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["name"] == "_DummyResponseFormat"


def _clear_provider_keys(monkeypatch):
    for env in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "TOGETHER_API_KEY",
        "CUSTOM_MODEL_KEY",
    ):
        monkeypatch.delenv(env, raising=False)


def test_call_llm_routes_catalog_models_through_openrouter(mock_openrouter_completion, monkeypatch):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    call_llm("hello", model="gpt-5-mini")
    assert mock_openrouter_completion.call_args.kwargs["model"] == "openai/gpt-5-mini"


def test_call_llm_routes_claude_with_dotted_openrouter_slug(
    mock_openrouter_completion, monkeypatch
):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    call_llm("hello", model="claude-sonnet-4-6")
    kwargs = mock_openrouter_completion.call_args.kwargs
    assert kwargs["model"] == "anthropic/claude-sonnet-4.6"
    assert "cache_control" not in kwargs


def test_call_llm_model_spec_uses_openrouter_for_catalog_provider(
    mock_openrouter_completion, monkeypatch
):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    spec = _llms_module.ModelSpec(provider="anthropic", model_id="claude-haiku-4-5")
    call_llm("hello", model_spec=spec)
    assert mock_openrouter_completion.call_args.kwargs["model"] == "anthropic/claude-haiku-4.5"


def test_call_llm_together_model_spec_uses_openai_compatible_client(monkeypatch):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("TOGETHER_API_KEY", "together-key")
    client = MagicMock()
    create = client.chat.completions.create
    create.return_value = _make_completion_response()
    with patch("overbae.core.llms._openai_compatible_client", return_value=client) as factory:
        spec = _llms_module.ModelSpec(provider="together", model_id="ft-model")
        call_llm("hello", model_spec=spec)
    factory.assert_called_once_with("https://api.together.xyz/v1", "together-key")
    assert create.call_args.kwargs["model"] == "ft-model"


def test_call_llm_custom_model_spec_uses_openai_compatible_client(monkeypatch):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("CUSTOM_MODEL_KEY", "custom-key")
    client = MagicMock()
    create = client.chat.completions.create
    create.return_value = _make_completion_response()
    with patch("overbae.core.llms._openai_compatible_client", return_value=client) as factory:
        spec = _llms_module.ModelSpec(
            provider="custom",
            model_id="custom-model",
            base_url="https://models.example.test/v1",
            api_key_env="CUSTOM_MODEL_KEY",
        )
        call_llm("hello", model_spec=spec)
    # Custom base URLs split into (base, query_items) so routing params ride on
    # default_query instead of being baked into the URL.
    factory.assert_called_once_with("https://models.example.test/v1", "custom-key", (), ())
    assert create.call_args.kwargs["model"] == "custom-model"


def test_get_embedding_fails_fast_without_openai_key(monkeypatch):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    with pytest.raises(_llms_module.EmbeddingUnavailableError):
        _llms_module.get_embedding("hello")


def _make_openai_exc(cls, msg: str, status_code: int | None = None):
    exc = cls.__new__(cls)
    Exception.__init__(exc, msg)
    if status_code is not None:
        exc.status_code = status_code
    return exc


def _make_retry_state(exc=None) -> RetryCallState:
    outcome = MagicMock()
    outcome.exception.return_value = exc
    state = MagicMock(spec=RetryCallState)
    state.outcome = outcome
    return state


def test_retry_predicate_returns_true_for_retryable_errors():
    exc = _make_openai_exc(openai.RateLimitError, "rate limited", status_code=429)
    assert _should_retry_llm_call(_make_retry_state(exc=exc)) is True


def test_retry_predicate_returns_false_for_non_retryable_errors():
    exc = _make_openai_exc(openai.BadRequestError, "bad request", status_code=400)
    assert _should_retry_llm_call(_make_retry_state(exc=exc)) is False


def test_retry_predicate_retries_generic_internal_server_errors():
    exc = _make_openai_exc(openai.InternalServerError, "server exploded", status_code=500)
    assert _should_retry_llm_call(_make_retry_state(exc=exc)) is True


def test_retry_predicate_fails_fast_on_credential_errors():
    missing = _make_openai_exc(openai.InternalServerError, "Missing credentials", status_code=500)
    assert _should_retry_llm_call(_make_retry_state(exc=missing)) is False
    auth = _make_openai_exc(openai.InternalServerError, "error code: authentication_error", 500)
    assert _should_retry_llm_call(_make_retry_state(exc=auth)) is False


def test_every_catalog_row_names_a_vendor_and_a_tier():
    assert "gemini-3.1-pro-preview" in MODELS_BY_NAME
    for m in CATALOG:
        assert m.vendor and m.tier and (m.slug or m.priced_as)
