"""Deterministic checks that ship with innermind, seeded as project-wide EvalMatrix rows.

Each script in scripts/ is the row's source. The ready checks need nothing and
abstain where they do not apply. A template needs a contract (a reference, a
pattern, a budget) in the constants at its top: until then it abstains, so it
seeds disabled. Fill the constants, scope the row with span_name when the
contract fits one function, and enable it.
"""

from __future__ import annotations

from pathlib import Path

from overbae.models import EvalMatrix, Project

SCRIPTS = Path(__file__).resolve().parent / "scripts"

# (name, span_type, enabled); a blank span_type runs on every span.
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
    ("latency_budget", "llm_call", False),
    ("cost_budget", "llm_call", False),
)


def source(name: str) -> str:
    return (SCRIPTS / f"{name}.py").read_text(encoding="utf-8")


def seed(project: Project) -> list[EvalMatrix]:
    """Creates missing rows only, so an edited or disabled row stays as the user left it.
    Returns the project's enabled seeded rows."""
    rows = []
    for name, span_type, enabled in CATALOG:
        row, _ = EvalMatrix.objects.get_or_create(
            project=project,
            capability=None,
            name=name,
            defaults={
                "type": EvalMatrix.Type.DETERMINISTIC,
                "source": source(name),
                "span_type": span_type,
                "enabled": enabled,
            },
        )
        if row.enabled:
            rows.append(row)
    return rows
