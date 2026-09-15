import pytest

from overbae.core.model_resolver import (
    MODEL_PRIORITY,
    OPENROUTER_MODEL_SLUGS,
    TaskType,
    model_chain,
    openrouter_configured,
    resolve_model,
)


def _clear_provider_keys(monkeypatch):
    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(env, raising=False)


def test_availability_needs_the_openrouter_key(monkeypatch):
    _clear_provider_keys(monkeypatch)
    assert openrouter_configured() is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-direct")
    assert openrouter_configured() is False
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert openrouter_configured() is True


def test_resolve_model_returns_the_head_of_the_chain(monkeypatch):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert resolve_model(TaskType.JUDGE_SCORING) == "gpt-5.6-luna"
    assert resolve_model(TaskType.DEFAULT) == "gpt-5.6-luna"


def test_resolve_model_raises_without_a_key(monkeypatch):
    _clear_provider_keys(monkeypatch)
    with pytest.raises(RuntimeError, match="No LLM API key"):
        resolve_model(TaskType.JUDGE_SCORING)


def test_unknown_task_falls_back_to_the_default_chain():
    assert model_chain("not-a-task") == model_chain(TaskType.DEFAULT)


def test_every_chain_is_routable():
    for chain in MODEL_PRIORITY.values():
        assert all(model in OPENROUTER_MODEL_SLUGS for model in chain)


def test_every_chain_is_ordered_gpt_then_claude_then_gemini():
    rank = {"gpt": 0, "claude": 1, "gemini": 2}

    for task, chain in MODEL_PRIORITY.items():
        ranks = [rank[model.split("-")[0]] for model in chain]
        assert ranks == sorted(ranks), f"{task} is out of vendor order: {chain}"
        assert len(set(ranks)) == len(ranks), f"{task} repeats a vendor: {chain}"


def test_judge_chain_spans_three_families():
    from overbae.core.llms import LLM_PROVIDER_BY_MODEL

    families = [LLM_PROVIDER_BY_MODEL[m] for m in model_chain(TaskType.JUDGE_SCORING)]
    assert families == ["openai", "anthropic", "gemini"]
