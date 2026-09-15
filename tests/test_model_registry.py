import pytest

from overbae.core.model_registry import (
    CATALOG,
    LLM_PROVIDER_BY_MODEL,
    OPENROUTER_MODEL_SLUGS,
    ROLE_CHAINS,
    WORKSHOP_ENGINES,
    WORKSHOP_KEY_ENVS,
    TaskType,
    default_backtest_models,
    default_judge_model,
    model_chain,
    openrouter_configured,
    pricing_slug,
    resolve_model,
    resolve_openrouter_slug,
    workshop_engine,
)


@pytest.fixture
def no_keys(monkeypatch):
    for env in WORKSHOP_KEY_ENVS:
        monkeypatch.delenv(env, raising=False)


def test_availability_needs_the_openrouter_key(no_keys, monkeypatch):
    assert openrouter_configured() is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-direct")
    assert openrouter_configured() is False
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert openrouter_configured() is True


def test_resolve_model_returns_the_head_of_the_chain(no_keys, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert resolve_model(TaskType.JUDGE_SCORING) == "gpt-5.6-luna"
    assert resolve_model(TaskType.DEFAULT) == "gpt-5.6-luna"
    assert resolve_model(TaskType.WORKSHOP) == "gpt-5.6-terra"


def test_resolve_model_raises_without_a_key(no_keys):
    with pytest.raises(RuntimeError, match="No LLM API key"):
        resolve_model(TaskType.JUDGE_SCORING)


def test_unknown_task_falls_back_to_the_default_chain():
    assert model_chain("not-a-task") == model_chain(TaskType.DEFAULT)


def test_every_chain_is_routable_and_ordered_gpt_then_claude_then_gemini():
    rank = {"gpt": 0, "claude": 1, "gemini": 2}
    for role, chain in ROLE_CHAINS.items():
        assert all(model in OPENROUTER_MODEL_SLUGS for model in chain)
        ranks = [rank[model.split("-")[0]] for model in chain]
        assert ranks == sorted(ranks), f"{role} is out of vendor order: {chain}"
        assert len(set(ranks)) == len(ranks), f"{role} repeats a vendor: {chain}"


def test_judge_chain_spans_three_families():
    families = [LLM_PROVIDER_BY_MODEL[m] for m in model_chain(TaskType.JUDGE_SCORING)]
    assert families == ["openai", "anthropic", "gemini"]


def test_defaults_come_from_the_chains():
    assert default_judge_model() == model_chain(TaskType.JUDGE_SCORING)[0]
    assert default_backtest_models() == ["openai/gpt-5.6-luna", "anthropic/claude-sonnet-5"]


def test_catalog_names_are_unique_and_slugs_are_vendor_qualified():
    names = [m.name for m in CATALOG]
    assert len(names) == len(set(names))
    assert all("/" in m.slug for m in CATALOG if m.slug)


def test_pricing_slug_covers_catalog_slugs_and_stand_ins():
    assert pricing_slug("claude-sonnet-5") == "anthropic/claude-sonnet-5"
    assert pricing_slug("openai/gpt-5.6-terra") == "openai/gpt-5.6-terra"
    assert pricing_slug("composer-2.5") == "moonshotai/kimi-k2.5"
    assert pricing_slug("nope") is None


def test_resolve_openrouter_slug_qualifies_bare_vendor_names():
    assert resolve_openrouter_slug("claude-sonnet-5") == "anthropic/claude-sonnet-5"
    assert resolve_openrouter_slug("gpt-4o-mini") == "openai/gpt-4o-mini"
    assert resolve_openrouter_slug("acme/custom") == "acme/custom"
    assert resolve_openrouter_slug("mystery") == "mystery"


def test_workshop_ladder_is_cursor_then_openrouter_then_direct_keys(no_keys, monkeypatch):
    assert [e.provider.name for e in WORKSHOP_ENGINES] == [
        "cursor",
        "openrouter",
        "openai",
        "anthropic",
        "gemini",
    ]
    assert workshop_engine() is None
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert workshop_engine().model == "gemini-3.1-pro-preview"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert workshop_engine().model == "claude-sonnet-5"
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    assert workshop_engine().model == "gpt-5.6-terra"
    monkeypatch.setenv("OPENROUTER_API_KEY", "r")
    engine = workshop_engine()
    assert engine.provider.name == "openrouter" and engine.models == ROLE_CHAINS["agent"]
    monkeypatch.setenv("CURSOR_API_KEY", "c")
    assert workshop_engine().model == "composer-2.5"


def test_direct_engines_run_their_own_vendor_from_the_agent_chain():
    by_provider = {e.provider.name: e.model for e in WORKSHOP_ENGINES}
    assert LLM_PROVIDER_BY_MODEL[by_provider["openai"]] == "openai"
    assert LLM_PROVIDER_BY_MODEL[by_provider["anthropic"]] == "anthropic"
    assert LLM_PROVIDER_BY_MODEL[by_provider["gemini"]] == "gemini"
    assert all(m in ROLE_CHAINS["agent"] for p, m in by_provider.items() if p != "cursor")
