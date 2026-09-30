from dataclasses import dataclass


@dataclass
class Skill:
    name: str
    slug: str
    description: str
    version: str
    provider: str


skills = [
    Skill(
        name="Overmind",
        slug="overmind",
        description=(
            "Connect and set up Overmind, discover repository capabilities, "
            "and coordinate workflows across product surfaces"
        ),
        version="1.2.0",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Agent",
        slug="overmind-agent",
        description="Inspect capabilities, contracts and agent coverage",
        version="1.2.0",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Observability",
        slug="overmind-observability",
        description="Investigate traces, failures and latency",
        version="1.2.0",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Datasets",
        slug="overmind-datasets",
        description="Prepare and verify Data Workshop versions",
        version="1.2.0",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Evaluations",
        slug="overmind-evaluations",
        description="Prepare evaluations and compare measured results",
        version="1.2.0",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Optimiser",
        slug="overmind-optimiser",
        description="Improve prompts, code and model choices",
        version="1.2.0",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Training",
        slug="overmind-training",
        description="Prepare, estimate and inspect model training",
        version="1.2.0",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Inference",
        slug="overmind-inference",
        description="Inspect deployments, metrics and live routing",
        version="1.2.0",
        provider="overmind-core",
    ),
    Skill(
        name="Overmind Integrations",
        slug="overmind-integrations",
        description="Connect providers and verify imported traces",
        version="1.2.0",
        provider="overmind-core",
    ),
]
