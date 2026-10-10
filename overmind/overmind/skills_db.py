from dataclasses import dataclass

# Every plugin manifest (.claude-plugin, .codex-plugin, .cursor-plugin) carries this version.
SKILLS_VERSION = "1.2.0"


@dataclass
class Skill:
    name: str
    slug: str
    description: str
    provider: str


skills = [
    Skill(
        name="Overmind",
        slug="overmind",
        description=(
            "Connect and set up Overmind, discover repository capabilities, "
            "and coordinate workflows across product surfaces"
        ),
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Agent",
        slug="overmind-agent",
        description="Inspect capabilities, contracts and agent coverage",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Observability",
        slug="overmind-observability",
        description="Investigate traces, failures and latency",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Datasets",
        slug="overmind-datasets",
        description="Prepare and verify Data Workshop versions",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Evaluations",
        slug="overmind-evaluations",
        description="Prepare evaluations and compare measured results",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Optimiser",
        slug="overmind-optimiser",
        description="Improve prompts, code and model choices",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Training",
        slug="overmind-training",
        description="Prepare, estimate and inspect model training",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Inference",
        slug="overmind-inference",
        description="Inspect deployments, metrics and live routing",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Integrations",
        slug="overmind-integrations",
        description="Connect providers and verify imported traces",
        provider="overmind-core",
    ),
]
