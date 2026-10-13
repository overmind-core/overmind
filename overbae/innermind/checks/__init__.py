"""Deterministic checks that ship with innermind, seeded as project-wide EvalMatrix rows.

Each script in scripts/ is the row's source. The ready checks need nothing and
abstain where they do not apply. A template needs a contract (a reference, a
pattern, a budget) in the constants at its top: until then it abstains, so it
seeds disabled. Fill the constants, scope the row with span_name when the
contract fits one function, and enable it.
"""

from __future__ import annotations

import re
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


def _literal(value: object) -> str:
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return "{" + ", ".join(f"{key!r}: {_literal(item)}" for key, item in value.items()) + "}"
    raise ValueError(f"unsupported parameter value {value!r}")


def render(name: str, params: dict) -> str:
    """The catalog script with its top-of-file constants set to *params*.

    Only the single-line ``NAME = value`` constants above the first function are
    parameters; anything else is rejected rather than spliced into the script."""
    if name not in {entry[0] for entry in CATALOG}:
        raise ValueError(f"unknown check {name!r}")
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    text = source(name)
    header = text.split("\ndef ", 1)[0]
    for key, value in params.items():
        line = re.compile(rf"^{re.escape(str(key))} = .*$", re.MULTILINE)
        if not re.fullmatch(r"[A-Z][A-Z_]*", str(key)) or not line.search(header):
            raise ValueError(f"{name} has no parameter {key!r}")
        assignment = f"{key} = {_literal(value)}"
        text = text.replace(line.search(text).group(0), assignment, 1)
    return text


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
