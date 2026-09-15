from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import StrEnum


class Vendor(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    XAI = "x-ai"
    DEEPSEEK = "deepseek"
    MOONSHOT = "moonshotai"
    META = "meta-llama"
    QWEN = "qwen"
    CURSOR = "cursor"


class Tier(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    FLAGSHIP = "flagship"
    LEGACY = "legacy"
    OPEN = "open"


@dataclass(frozen=True)
class Reasoning:
    adaptive: bool | None = None
    levels: tuple[str, ...] = ()
    budgets: tuple[int, ...] = ()
    required: bool = False


NO_REASONING = Reasoning()
EFFORT = Reasoning(adaptive=True, levels=("low", "medium", "high"))
EFFORT_MAX = Reasoning(adaptive=True, levels=("low", "medium", "high", "max"))
EFFORT_HIGH = Reasoning(adaptive=True, levels=("medium", "high"))
BUDGET_8K = Reasoning(adaptive=False, budgets=(8000,))


@dataclass(frozen=True)
class Model:
    name: str
    vendor: Vendor
    slug: str
    tier: Tier
    description: str = ""
    reasoning: Reasoning = NO_REASONING
    priced_as: str = ""
    library: bool = False

    @property
    def supports_reasoning(self) -> bool:
        return self.reasoning.adaptive is not None


CATALOG: tuple[Model, ...] = (
    Model(
        "gpt-5.6-sol",
        Vendor.OPENAI,
        "openai/gpt-5.6-sol",
        Tier.FLAGSHIP,
        "OpenAI's flagship for complex professional and agentic work.",
        EFFORT,
        library=True,
    ),
    Model(
        "gpt-5.6-terra",
        Vendor.OPENAI,
        "openai/gpt-5.6-terra",
        Tier.BALANCED,
        "OpenAI's balanced tier for long agentic tasks.",
        EFFORT,
        library=True,
    ),
    Model(
        "gpt-5.6-luna",
        Vendor.OPENAI,
        "openai/gpt-5.6-luna",
        Tier.FAST,
        "OpenAI's fast tier: near-frontier quality at a fraction of the price.",
        EFFORT,
    ),
    Model("gpt-5.5", Vendor.OPENAI, "openai/gpt-5.5", Tier.LEGACY, reasoning=EFFORT),
    Model("gpt-5.5-pro", Vendor.OPENAI, "openai/gpt-5.5-pro", Tier.LEGACY, reasoning=EFFORT_HIGH),
    Model("gpt-5.4", Vendor.OPENAI, "openai/gpt-5.4", Tier.LEGACY, reasoning=EFFORT),
    Model("gpt-5.4-pro", Vendor.OPENAI, "openai/gpt-5.4-pro", Tier.LEGACY, reasoning=EFFORT_HIGH),
    Model("gpt-5.4-mini", Vendor.OPENAI, "openai/gpt-5.4-mini", Tier.LEGACY, reasoning=EFFORT),
    Model("gpt-5.4-nano", Vendor.OPENAI, "openai/gpt-5.4-nano", Tier.LEGACY, reasoning=EFFORT),
    Model("gpt-5.2", Vendor.OPENAI, "openai/gpt-5.2", Tier.LEGACY, reasoning=EFFORT),
    Model("gpt-5.2-pro", Vendor.OPENAI, "openai/gpt-5.2-pro", Tier.LEGACY, reasoning=EFFORT_HIGH),
    Model("gpt-5", Vendor.OPENAI, "openai/gpt-5", Tier.LEGACY, reasoning=EFFORT),
    Model("gpt-5-mini", Vendor.OPENAI, "openai/gpt-5-mini", Tier.LEGACY, reasoning=EFFORT),
    Model("gpt-5-nano", Vendor.OPENAI, "openai/gpt-5-nano", Tier.LEGACY, reasoning=EFFORT),
    Model("gpt-4.1", Vendor.OPENAI, "openai/gpt-4.1", Tier.LEGACY),
    Model(
        "claude-fable-5",
        Vendor.ANTHROPIC,
        "anthropic/claude-fable-5",
        Tier.FLAGSHIP,
        "Anthropic's most capable model for long-horizon agentic work.",
        EFFORT_MAX,
        library=True,
    ),
    Model(
        "claude-opus-5",
        Vendor.ANTHROPIC,
        "anthropic/claude-opus-5",
        Tier.FLAGSHIP,
        "Anthropic's premium model for the hardest reasoning.",
        EFFORT_MAX,
        library=True,
    ),
    Model(
        "claude-sonnet-5",
        Vendor.ANTHROPIC,
        "anthropic/claude-sonnet-5",
        Tier.BALANCED,
        "Anthropic's best combination of speed and intelligence.",
        EFFORT,
        library=True,
    ),
    Model(
        "claude-haiku-4-5",
        Vendor.ANTHROPIC,
        "anthropic/claude-haiku-4.5",
        Tier.FAST,
        "Anthropic's fastest model.",
        BUDGET_8K,
    ),
    Model(
        "claude-opus-4-6",
        Vendor.ANTHROPIC,
        "anthropic/claude-opus-4.6",
        Tier.LEGACY,
        reasoning=EFFORT_MAX,
    ),
    Model(
        "claude-sonnet-4-6",
        Vendor.ANTHROPIC,
        "anthropic/claude-sonnet-4.6",
        Tier.LEGACY,
        reasoning=EFFORT,
    ),
    Model(
        "claude-opus-4-5",
        Vendor.ANTHROPIC,
        "anthropic/claude-opus-4.5",
        Tier.LEGACY,
        reasoning=BUDGET_8K,
    ),
    Model(
        "claude-sonnet-4-5",
        Vendor.ANTHROPIC,
        "anthropic/claude-sonnet-4.5",
        Tier.LEGACY,
        reasoning=BUDGET_8K,
    ),
    Model(
        "gemini-3.1-pro-preview",
        Vendor.GEMINI,
        "google/gemini-3.1-pro-preview",
        Tier.BALANCED,
        "Google's most advanced reasoning model.",
        EFFORT,
        library=True,
    ),
    Model(
        "gemini-3.8-flash",
        Vendor.GEMINI,
        "google/gemini-3.8-flash",
        Tier.FAST,
        "Google's fast Gemini 3 tier.",
        EFFORT,
    ),
    Model(
        "gemini-3.6-flash", Vendor.GEMINI, "google/gemini-3.6-flash", Tier.LEGACY, reasoning=EFFORT
    ),
    Model(
        "gemini-3.5-flash", Vendor.GEMINI, "google/gemini-3.5-flash", Tier.LEGACY, reasoning=EFFORT
    ),
    Model(
        "gemini-3.5-flash-lite",
        Vendor.GEMINI,
        "google/gemini-3.5-flash-lite",
        Tier.LEGACY,
        reasoning=EFFORT,
    ),
    Model(
        "gemini-3.1-flash-lite-preview",
        Vendor.GEMINI,
        "google/gemini-3.1-flash-lite-preview",
        Tier.LEGACY,
        reasoning=EFFORT,
    ),
    Model(
        "gemini-3-flash-preview",
        Vendor.GEMINI,
        "google/gemini-3-flash-preview",
        Tier.LEGACY,
        reasoning=EFFORT,
    ),
    Model(
        "gemini-2.5-pro",
        Vendor.GEMINI,
        "google/gemini-2.5-pro",
        Tier.LEGACY,
        reasoning=Reasoning(adaptive=True, levels=("low", "medium", "high"), required=True),
        library=True,
    ),
    Model(
        "gemini-2.5-flash",
        Vendor.GEMINI,
        "google/gemini-2.5-flash",
        Tier.LEGACY,
        reasoning=Reasoning(adaptive=False, budgets=(-1,)),
    ),
    Model("gemini-2.5-flash-lite", Vendor.GEMINI, "google/gemini-2.5-flash-lite", Tier.LEGACY),
    Model("grok-4.20", Vendor.XAI, "x-ai/grok-4.20", Tier.FLAGSHIP, reasoning=EFFORT, library=True),
    Model(
        "deepseek-v3.2",
        Vendor.DEEPSEEK,
        "deepseek/deepseek-v3.2",
        Tier.BALANCED,
        reasoning=EFFORT,
        library=True,
    ),
    Model("moonshotai/kimi-k2", Vendor.MOONSHOT, "moonshotai/kimi-k2", Tier.LEGACY),
    # No OpenRouter listing; priced as the Kimi K2.5 that Composer 2.5 was built on.
    Model(
        "composer-2.5",
        Vendor.CURSOR,
        "",
        Tier.BALANCED,
        "Cursor's agentic coding model.",
        priced_as="moonshotai/kimi-k2.5",
    ),
    Model("llama-3.2-3b-instruct", Vendor.META, "meta-llama/llama-3.2-3b-instruct", Tier.OPEN),
    Model("llama-3.1-8b-instruct", Vendor.META, "meta-llama/llama-3.1-8b-instruct", Tier.OPEN),
    Model("llama-3.3-70b-instruct", Vendor.META, "meta-llama/llama-3.3-70b-instruct", Tier.OPEN),
    Model("qwen3-8b", Vendor.QWEN, "qwen/qwen3-8b", Tier.OPEN),
    Model("qwen3-14b", Vendor.QWEN, "qwen/qwen3-14b", Tier.OPEN),
    Model("qwen3-32b", Vendor.QWEN, "qwen/qwen3-32b", Tier.OPEN),
)

MODELS_BY_NAME: dict[str, Model] = {m.name: m for m in CATALOG}
OPENROUTER_MODEL_SLUGS: dict[str, str] = {m.name: m.slug for m in CATALOG if m.slug}
LLM_PROVIDER_BY_MODEL: dict[str, str] = {m.name: str(m.vendor) for m in CATALOG}

_DATE_SUFFIX_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def normalize_model_name(model_name: str) -> str:
    base = _DATE_SUFFIX_RE.sub("", model_name)
    return base if base in MODELS_BY_NAME else model_name


def model(model_name: str) -> Model | None:
    return MODELS_BY_NAME.get(normalize_model_name(model_name))


def reasoning_of(model_name: str) -> Reasoning:
    m = model(model_name)
    return m.reasoning if m else NO_REASONING


def pricing_slug(model_name: str) -> str | None:
    m = model(model_name)
    if m is not None:
        return m.priced_as or m.slug or None
    name = normalize_model_name(model_name)
    return name.removeprefix("openrouter/") if "/" in name else None


_VENDOR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("chatgpt", "openai/"),
    ("gpt-", "openai/"),
    ("o1", "openai/"),
    ("o3", "openai/"),
    ("o4", "openai/"),
    ("claude", "anthropic/"),
    ("gemini", "google/"),
    ("grok", "x-ai/"),
    ("deepseek", "deepseek/"),
    ("llama", "meta-llama/"),
    ("qwen", "qwen/"),
    ("mistral", "mistralai/"),
    ("kimi", "moonshotai/"),
)


def openrouter_slug(model_name: str) -> str:
    name = normalize_model_name(model_name)
    if name in OPENROUTER_MODEL_SLUGS:
        return OPENROUTER_MODEL_SLUGS[name]
    if "/" in name:
        return name.removeprefix("openrouter/")
    raise ValueError(f"Unsupported model: {name}")


def resolve_openrouter_slug(model_name: str) -> str:
    try:
        return openrouter_slug(model_name)
    except ValueError:
        name = (model_name or "").strip()
        lowered = name.lower()
        for stem, prefix in _VENDOR_PREFIXES:
            if lowered.startswith(stem):
                return f"{prefix}{name}"
        return model_name


def picker_models(*tiers: Tier) -> list[Model]:
    wanted = set(tiers)
    return [m for m in CATALOG if m.tier in wanted and m.slug]


def library_models() -> list[Model]:
    return [m for m in CATALOG if m.library]


def inference_models() -> list[Model]:
    return [m for m in CATALOG if m.library or m.tier is Tier.OPEN]


_INFERENCE_PREFIXES: frozenset[str] = frozenset(
    m.slug.split("/")[0] + "/" for m in inference_models()
)


def is_inference_model(model_id: str) -> bool:
    return any(model_id.startswith(p) for p in _INFERENCE_PREFIXES)


class Role(StrEnum):
    FAST = "fast"
    AGENT = "agent"


ROLE_CHAINS: dict[Role, tuple[str, ...]] = {
    Role.FAST: ("gpt-5.6-luna", "claude-sonnet-5", "gemini-3.8-flash"),
    Role.AGENT: ("gpt-5.6-terra", "claude-sonnet-5", "gemini-3.1-pro-preview"),
}


class TaskType(StrEnum):
    JUDGE_SCORING = "judge_scoring"
    CRITERIA_GENERATION = "criteria_generation"
    EVAL_RECOMMENDATION = "eval_recommendation"
    WORKSHOP = "workshop"
    DEFAULT = "default"


TASK_ROLES: dict[TaskType, Role] = {
    TaskType.JUDGE_SCORING: Role.FAST,
    TaskType.CRITERIA_GENERATION: Role.FAST,
    TaskType.EVAL_RECOMMENDATION: Role.FAST,
    TaskType.WORKSHOP: Role.AGENT,
    TaskType.DEFAULT: Role.FAST,
}


def model_chain(task: TaskType | str) -> list[str]:
    role = TASK_ROLES.get(task, Role.FAST)  # type: ignore[arg-type]
    return list(ROLE_CHAINS[role])


def resolve_model(task: TaskType) -> str:
    if not openrouter_configured():
        raise RuntimeError(
            f"No LLM API key configured for task '{task.value}'. Set OPENROUTER_API_KEY."
        )
    return model_chain(task)[0]


def default_judge_model() -> str:
    return model_chain(TaskType.JUDGE_SCORING)[0]


def judge_picker_models() -> list[str]:
    default = default_judge_model()
    rest = [m.name for m in picker_models(Tier.FAST, Tier.BALANCED) if m.name != default]
    return [default, *rest]


def model_defaults() -> dict[str, object]:
    return {
        "judge_model": default_judge_model(),
        "judge_models": judge_picker_models(),
        "backtest_models": default_backtest_models(),
    }


def default_backtest_models() -> list[str]:
    return [OPENROUTER_MODEL_SLUGS[name] for name in model_chain(TaskType.DEFAULT)[:2]]


@dataclass(frozen=True)
class Provider:
    name: str
    key_env: str
    base_url: str | None = None
    openrouter_extras: bool = False
    reasoning_effort: bool = False
    headers: dict[str, str] = field(default_factory=dict)

    def key(self) -> str:
        return os.environ.get(self.key_env, "")

    def configured(self) -> bool:
        return bool(self.key())


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider(
        "openrouter",
        "OPENROUTER_API_KEY",
        OPENROUTER_BASE_URL,
        openrouter_extras=True,
        headers={"HTTP-Referer": "https://overmindlab.ai", "X-Title": "Overmind Platform"},
    ),
    "openai": Provider("openai", "OPENAI_API_KEY", reasoning_effort=True),
    "anthropic": Provider("anthropic", "ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/"),
    "gemini": Provider(
        "gemini",
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        reasoning_effort=True,
    ),
    "cursor": Provider("cursor", "CURSOR_API_KEY"),
}


def openrouter_configured() -> bool:
    return PROVIDERS["openrouter"].configured()


@dataclass(frozen=True)
class Engine:
    provider: Provider
    models: tuple[str, ...]

    @property
    def model(self) -> str:
        return self.models[0]


def _agent_model_for(vendor: Vendor) -> str:
    return next(n for n in ROLE_CHAINS[Role.AGENT] if MODELS_BY_NAME[n].vendor is vendor)


WORKSHOP_ENGINES: tuple[Engine, ...] = (
    Engine(PROVIDERS["cursor"], ("composer-2.5",)),
    Engine(PROVIDERS["openrouter"], ROLE_CHAINS[Role.AGENT]),
    Engine(PROVIDERS["openai"], (_agent_model_for(Vendor.OPENAI),)),
    Engine(PROVIDERS["anthropic"], (_agent_model_for(Vendor.ANTHROPIC),)),
    Engine(PROVIDERS["gemini"], (_agent_model_for(Vendor.GEMINI),)),
)

WORKSHOP_KEY_ENVS: tuple[str, ...] = tuple(e.provider.key_env for e in WORKSHOP_ENGINES)


def workshop_engine() -> Engine | None:
    return next((e for e in WORKSHOP_ENGINES if e.provider.configured()), None)
