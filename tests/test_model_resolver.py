"""Tests for the LLM model routing / resolver logic."""

import pytest
from overmind.core.model_resolver import (
    TaskType,
    resolve_model,
    resolve_model_and_provider,
    get_available_backtest_models,
)


@pytest.fixture(autouse=True)
def _clear_api_keys(monkeypatch):
    """Start each test with no API keys set."""
    monkeypatch.setattr("overmind.config.settings.openai_api_key", "")
    monkeypatch.setattr("overmind.config.settings.anthropic_api_key", "")
    monkeypatch.setattr("overmind.config.settings.gemini_api_key", "")


@pytest.mark.parametrize(
    "key_attr,key_value,task_type,expected_model",
    [
        ("openai_api_key", "sk-test", TaskType.JUDGE_SCORING, "gpt-5-mini"),
        (
            "anthropic_api_key",
            "sk-ant-test",
            TaskType.PROMPT_TUNING,
            "claude-sonnet-4-6",
        ),
    ],
    ids=["openai-only", "anthropic-only"],
)
def test_resolve_single_provider(
    monkeypatch, key_attr, key_value, task_type, expected_model
):
    monkeypatch.setattr(f"overmind.config.settings.{key_attr}", key_value)
    assert resolve_model(task_type) == expected_model


def test_resolve_priority_order_all_keys(monkeypatch):
    """With all keys set, the first provider in priority wins."""
    monkeypatch.setattr("overmind.config.settings.openai_api_key", "sk-test")
    monkeypatch.setattr("overmind.config.settings.anthropic_api_key", "sk-ant")
    monkeypatch.setattr("overmind.config.settings.gemini_api_key", "gk-test")

    assert resolve_model(TaskType.JUDGE_SCORING) == "gpt-5-mini"
    assert resolve_model(TaskType.PROMPT_TUNING) == "claude-sonnet-4-6"


def test_resolve_no_keys_raises():
    with pytest.raises(RuntimeError, match="No LLM API key configured"):
        resolve_model(TaskType.DEFAULT)


def test_resolve_model_and_provider_returns_tuple(monkeypatch):
    monkeypatch.setattr("overmind.config.settings.anthropic_api_key", "sk-ant-test")
    model, provider = resolve_model_and_provider(TaskType.PROMPT_TUNING)
    assert model == "claude-sonnet-4-6"
    assert provider == "anthropic"


def test_resolve_model_and_provider_openai(monkeypatch):
    monkeypatch.setattr("overmind.config.settings.openai_api_key", "sk-test")
    model, provider = resolve_model_and_provider(TaskType.JUDGE_SCORING)
    assert model == "gpt-5-mini"
    assert provider == "openai"


def test_resolve_model_and_provider_gemini_fallback(monkeypatch):
    monkeypatch.setattr("overmind.config.settings.gemini_api_key", "gk-test")
    model, provider = resolve_model_and_provider(TaskType.PROMPT_TUNING)
    assert model == "gemini-3-pro-preview"
    assert provider == "gemini"


def test_resolve_model_backward_compat(monkeypatch):
    """resolve_model still returns just the model name string."""
    monkeypatch.setattr("overmind.config.settings.anthropic_api_key", "sk-ant-test")
    assert resolve_model(TaskType.PROMPT_TUNING) == "claude-sonnet-4-6"


def test_resolve_model_and_provider_no_keys_raises():
    with pytest.raises(RuntimeError, match="No LLM API key configured"):
        resolve_model_and_provider(TaskType.DEFAULT)


def test_backtest_models_filtered(monkeypatch):
    monkeypatch.setattr("overmind.config.settings.openai_api_key", "sk-test")
    models = get_available_backtest_models()
    assert "gpt-5-mini" in models
    assert "claude-sonnet-4-6" not in models

    monkeypatch.setattr("overmind.config.settings.anthropic_api_key", "sk-ant")
    models = get_available_backtest_models()
    assert "claude-sonnet-4-6" in models
