"""
Deterministic checks that ship with innermind, seeded as DeterministicCheck rows.

Each script in scripts/ is the row's source, run by innermind.deterministic. The
ready checks need nothing and abstain where they do not apply. A template needs a
contract (a reference, a pattern, a budget) in the constants at its top: until
then it abstains, so it seeds disabled. Fill the constants, scope the row with
span_name when the contract fits one function, and enable it.
"""

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent / "scripts"

# (name, kind, enabled); kind "" runs on every span kind.
CATALOG = (
    ("output_is_valid_json", "", True),
    ("tool_selection", "", True),
    ("exact_match", "", False),
    ("contains", "", False),
    ("regex", "", False),
    ("goal_state_match", "", False),
    ("json_required_keys", "", False),
    ("schema_field_conformance", "", False),
    ("tool_args_match", "", False),
    ("llm_call_budget", "", False),
    ("latency_budget", "llm", False),
    ("cost_budget", "llm", False),
)
READY = tuple(name for name, _, enabled in CATALOG if enabled)
TEMPLATES = tuple(name for name, _, enabled in CATALOG if not enabled)


def source(name: str) -> str:
    return (SCRIPTS / f"{name}.py").read_text(encoding="utf-8")


def seed(check_model) -> int:
    """Creates missing rows only, so an edited or disabled row stays as the user left it."""
    created = 0
    for name, kind, enabled in CATALOG:
        _, new = check_model.objects.get_or_create(
            name=name, defaults={"source": source(name), "kind": kind, "enabled": enabled}
        )
        created += new
    return created
