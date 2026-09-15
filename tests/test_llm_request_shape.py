"""The request body we hand OpenRouter: provider preferences, the fallback chain,
the retry deadline and cache accounting."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from overbae.core import llms


class _Answer(BaseModel):
    answer: str


def _response(content="hi", model="openai/gpt-5.6-luna", cached=0):
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=2,
        prompt_tokens_details={"cached_tokens": cached},
        model_extra={"cache_discount": -0.5} if cached else {},
    )
    message = SimpleNamespace(content=content, tool_calls=None, model_extra={})
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage, model=model)


@pytest.fixture
def captured(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    llms._provider_client.cache_clear()
    seen = {}

    def _fake(client, completion_kwargs, request_kwargs, retry_deadline=None):
        seen["body"] = completion_kwargs
        seen["deadline"] = retry_deadline
        return _response()

    with patch.object(llms, "_do_openai_completion", _fake):
        yield seen


def test_schema_calls_require_a_provider_that_honours_the_schema(captured):
    llms.call_llm("q", model="gpt-5.6-luna", response_format=_Answer)
    assert captured["body"]["extra_body"]["provider"] == {"require_parameters": True}


def test_plain_calls_do_not_constrain_the_provider_pool(captured):
    llms.call_llm("q", model="gpt-5.6-luna")
    assert "provider" not in captured["body"]["extra_body"]


def test_tool_calls_always_require_parameters(captured):
    llms.call_llm_tools([{"role": "user", "content": "q"}], [{"type": "function"}])
    assert captured["body"]["extra_body"]["provider"] == {"require_parameters": True}


def test_fallback_chain_leads_with_the_selected_model(captured):
    llms.call_llm(
        "q",
        model="gpt-5.6-luna",
        fallback_models=["gpt-5.6-luna", "claude-sonnet-5", "gemini-3.8-flash"],
    )
    assert captured["body"]["extra_body"]["models"] == [
        "openai/gpt-5.6-luna",
        "anthropic/claude-sonnet-5",
        "google/gemini-3.8-flash",
    ]


def test_no_chain_is_sent_when_the_caller_passes_none(captured):
    llms.call_llm("q", model="gpt-5.6-luna")
    assert "models" not in captured["body"]["extra_body"]


def test_a_single_model_chain_is_not_sent(captured):
    llms.call_llm("q", model="gpt-5.6-luna", fallback_models=["gpt-5.6-luna"])
    assert "models" not in captured["body"]["extra_body"]


def test_interactive_callers_get_the_short_deadline(captured):
    llms.call_llm("q", model="gpt-5.6-luna", retry_deadline=llms.RETRY_DEADLINE_INTERACTIVE)
    assert captured["deadline"] == llms.RETRY_DEADLINE_INTERACTIVE
    assert llms.RETRY_DEADLINE_INTERACTIVE < llms.RETRY_DEADLINE_BACKGROUND


def test_background_is_the_default_deadline(captured):
    llms.call_llm("q", model="gpt-5.6-luna")
    assert captured["deadline"] == llms.RETRY_DEADLINE_BACKGROUND


def test_cache_reads_and_the_serving_model_reach_the_stats():
    _, stats = llms._extract_llm_response(_response(cached=7232))
    assert stats["cached_tokens"] == 7232
    assert stats["cache_discount"] == -0.5
    assert stats["served_model"] == "openai/gpt-5.6-luna"


def test_a_429_waits_the_time_the_provider_asked_for():
    exc = Exception()
    exc.response = SimpleNamespace(headers={"retry-after": "12"})
    assert llms._retry_after_seconds(exc) == 12.0


def test_a_missing_or_unparsable_retry_after_falls_back_to_backoff():
    assert llms._retry_after_seconds(Exception()) is None
    exc = Exception()
    exc.response = SimpleNamespace(headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert llms._retry_after_seconds(exc) is None
