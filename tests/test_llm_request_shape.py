"""The request body we hand OpenRouter: provider preferences, the fallback chain,
Retry-After and cache accounting."""

import pytest
from pydantic import BaseModel

from overbae.core.llms import call_llm, call_llm_tools


class _Answer(BaseModel):
    answer: str


@pytest.fixture
def sent(fake_llm):
    return lambda: fake_llm.requests[-1].body


def test_schema_calls_require_a_provider_that_honours_the_schema(sent):
    call_llm("q", model="gpt-5.6-luna", response_format=_Answer)
    assert sent()["provider"] == {"require_parameters": True}


def test_plain_calls_do_not_constrain_the_provider_pool(sent):
    call_llm("q", model="gpt-5.6-luna")
    assert "provider" not in sent()


def test_tool_calls_always_require_parameters(sent):
    call_llm_tools([{"role": "user", "content": "q"}], [])
    assert sent()["provider"] == {"require_parameters": True}


def test_fallback_chain_leads_with_the_selected_model(sent):
    call_llm(
        "q",
        model="gpt-5.6-luna",
        fallback_models=["gpt-5.6-luna", "claude-sonnet-5", "gemini-3.8-flash"],
    )
    assert sent()["models"] == [
        "openai/gpt-5.6-luna",
        "anthropic/claude-sonnet-5",
        "google/gemini-3.8-flash",
    ]


@pytest.mark.parametrize("fallback", [None, ["gpt-5.6-luna"]], ids=["none", "single"])
def test_no_chain_is_sent_without_an_alternative(sent, fallback):
    call_llm("q", model="gpt-5.6-luna", fallback_models=fallback)
    assert "models" not in sent()


def test_cache_reads_and_the_serving_model_reach_the_stats(fake_llm):
    fake_llm.on(
        lambda r: True,
        {
            "content": "hi",
            "usage": {"prompt_tokens_details": {"cached_tokens": 7232}, "cache_discount": -0.5},
        },
    )
    _, stats = call_llm("q", model="gpt-5.6-luna")
    assert stats["cached_tokens"] == 7232
    assert stats["cache_discount"] == -0.5
    assert stats["served_model"] == "openai/gpt-5.6-luna"


@pytest.mark.parametrize(("retry_after", "waits"), [("12", 12.0), ("soon", None)])
def test_a_429_waits_the_time_the_provider_asked_for(fake_llm, slept, retry_after, waits):
    calls = []
    fake_llm.fail(
        lambda r: calls.append(r) is None and len(calls) <= 3,
        429,
        "slow down",
        {"retry-after": retry_after},
    )
    call_llm("q", model="gpt-5.6-luna")
    assert len(calls) == 4
    if waits is None:
        assert slept[-1] != 12.0
    else:
        assert slept[-1] == waits
