import pytest
from pydantic import BaseModel

from overbae.core.llms import (
    EmbeddingUnavailableError,
    ModelSpec,
    call_llm,
    call_llm_tools,
    get_embedding,
)
from overbae.core.model_registry import CATALOG, MODELS_BY_NAME, reasoning_of


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


def test_call_llm_passes_reasoning_effort_when_supported(fake_llm):
    call_llm("hello", model="gpt-5-mini", reasoning_effort="low")
    assert fake_llm.requests[-1].body["reasoning"] == {"effort": "low"}


def test_call_llm_uses_the_default_model_and_reports_usage(fake_llm):
    fake_llm.on(lambda r: True, {"content": "ok", "usage": {"cost": 0.001}})
    content, stats = call_llm("hello")
    assert content == "ok"
    assert stats["prompt_tokens"] > 0
    assert stats["response_cost"] == 0.001
    assert fake_llm.requests[-1].model.startswith("openai/")


@pytest.mark.parametrize("cost", [None, 0.0, 0.001])
@pytest.mark.parametrize("with_tools", [False, True])
def test_usage_distinguishes_missing_cost_from_reported_zero(fake_llm, cost, with_tools):
    fake_llm.on(lambda r: True, {"content": "ok", "usage": {} if cost is None else {"cost": cost}})
    if with_tools:
        _, _, stats = call_llm_tools(
            [{"role": "user", "content": "hello"}], [], model="gpt-5-mini", retry_deadline=0
        )
    else:
        _, stats = call_llm("hello", model="gpt-5-mini")
    assert stats["response_cost"] == cost


class _Status(BaseModel):
    status: str


def test_call_llm_sends_the_response_schema(fake_llm):
    content, _ = call_llm("hello", system_prompt="system", model="gpt-5", response_format=_Status)
    request = fake_llm.requests[-1]
    assert request.model == "openai/gpt-5"
    assert request.system == "system"
    assert request.schema_name == "_Status"
    assert _Status.model_validate_json(content)


@pytest.fixture
def only_openrouter(monkeypatch):
    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "TOGETHER_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")


@pytest.mark.parametrize(
    ("arguments", "slug"),
    [
        ({"model": "gpt-5-mini"}, "openai/gpt-5-mini"),
        ({"model": "claude-sonnet-4-6"}, "anthropic/claude-sonnet-4.6"),
        (
            {"model_spec": ModelSpec(provider="anthropic", model_id="claude-haiku-4-5")},
            "anthropic/claude-haiku-4.5",
        ),
    ],
)
def test_catalog_models_route_through_openrouter(fake_llm, only_openrouter, arguments, slug):
    call_llm("hello", **arguments)
    request = fake_llm.requests[-1]
    assert request.url.startswith("https://openrouter.ai/")
    assert request.model == slug
    assert "cache_control" not in request.body


@pytest.mark.parametrize(
    ("spec", "env", "url"),
    [
        (
            ModelSpec(provider="together", model_id="ft-model"),
            "TOGETHER_API_KEY",
            "https://api.together.xyz/v1/chat/completions",
        ),
        (
            ModelSpec(
                provider="custom",
                model_id="ft-model",
                base_url="https://models.example.test/v1",
                api_key_env="CUSTOM_MODEL_KEY",
            ),
            "CUSTOM_MODEL_KEY",
            "https://models.example.test/v1/chat/completions",
        ),
    ],
    ids=["together", "custom"],
)
def test_openai_compatible_specs_call_their_own_endpoint(fake_llm, monkeypatch, spec, env, url):
    monkeypatch.setenv(env, "provider-key")
    call_llm("hello", model_spec=spec)
    request = fake_llm.requests[-1]
    assert request.url == url
    assert request.model == "ft-model"


def test_get_embedding_fails_fast_without_openai_key(only_openrouter):
    with pytest.raises(EmbeddingUnavailableError):
        get_embedding("hello")


@pytest.mark.parametrize(("status", "message"), [(429, "rate limited"), (500, "server exploded")])
def test_a_transient_provider_error_is_retried(fake_llm, slept, status, message):
    calls = []
    fake_llm.fail(lambda r: calls.append(r) is None and len(calls) == 1, status, message)
    fake_llm.on(lambda r: True, "recovered")
    content, _ = call_llm("hello", model="gpt-5-mini")
    assert content == "recovered"
    assert len(calls) == 2


def _attempts_until_refused(fake_llm, status: int, message: str) -> int:
    calls = []
    fake_llm.fail(lambda r: calls.append(r) is None, status, message)
    with pytest.raises(RuntimeError, match=message):
        call_llm("hello", model="gpt-5-mini", retry_deadline=0)
    return len(calls)


def test_a_bad_request_is_not_retried(fake_llm, slept):
    assert _attempts_until_refused(fake_llm, 400, "bad request") == 1


@pytest.mark.parametrize("message", ["Missing credentials", "error code: authentication_error"])
def test_a_credential_error_fails_on_the_first_attempt(fake_llm, slept, message):
    assert _attempts_until_refused(fake_llm, 500, message) == 1


def test_every_catalog_row_names_a_vendor_and_a_tier():
    assert "gemini-3.1-pro-preview" in MODELS_BY_NAME
    for m in CATALOG:
        assert m.vendor and m.tier and m.slug
