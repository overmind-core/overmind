"""Tests for the LLM model routing / resolver logic."""

import pytest
from overmind_core.overmind.model_resolver import (
    TaskType,
    resolve_model,
    get_available_backtest_models,
    get_available_providers,
)


@pytest.fixture(autouse=True)
def _clear_api_keys(monkeypatch):
    """Start each test with no API keys set."""
    monkeypatch.setattr("overmind_core.config.settings.openai_api_key", "")
    monkeypatch.setattr("overmind_core.config.settings.anthropic_api_key", "")
    monkeypatch.setattr("overmind_core.config.settings.gemini_api_key", "")


def test_resolve_openai_only(monkeypatch):
    monkeypatch.setattr("overmind_core.config.settings.openai_api_key", "sk-test")
    model = resolve_model(TaskType.JUDGE_SCORING)
    assert model == "gpt-5-mini"


def test_resolve_anthropic_only(monkeypatch):
    monkeypatch.setattr("overmind_core.config.settings.anthropic_api_key", "sk-ant-test")
    model = resolve_model(TaskType.PROMPT_TUNING)
    assert model == "claude-sonnet-4-6"


def test_resolve_priority_order_all_keys(monkeypatch):
    """With all keys set, the first provider in priority wins."""
    monkeypatch.setattr("overmind_core.config.settings.openai_api_key", "sk-test")
    monkeypatch.setattr("overmind_core.config.settings.anthropic_api_key", "sk-ant")
    monkeypatch.setattr("overmind_core.config.settings.gemini_api_key", "gk-test")

    assert resolve_model(TaskType.JUDGE_SCORING) == "gpt-5-mini"
    assert resolve_model(TaskType.PROMPT_TUNING) == "claude-sonnet-4-6"


def test_resolve_no_keys_raises():
    with pytest.raises(RuntimeError, match="No LLM API key configured"):
        resolve_model(TaskType.DEFAULT)


def test_backtest_models_filtered(monkeypatch):
    monkeypatch.setattr("overmind_core.config.settings.openai_api_key", "sk-test")
    models = get_available_backtest_models()
    assert "gpt-5-mini" in models
    assert "claude-sonnet-4-6" not in models

    monkeypatch.setattr("overmind_core.config.settings.anthropic_api_key", "sk-ant")
    models = get_available_backtest_models()
    assert "claude-sonnet-4-6" in models
