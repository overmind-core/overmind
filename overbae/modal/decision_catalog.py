from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_CATALOG = Path(__file__).with_name("decision_models.json")
LIMITS = ("options", "score_levels", "questions")


@lru_cache(maxsize=1)
def models() -> dict[str, dict]:
    rows = json.loads(_CATALOG.read_text())["models"]
    for row in rows:
        _check_limits(row)
    return {row["id"]: row for row in rows}


def _check_limits(row: dict) -> None:
    limits = row.get("limits")
    if not isinstance(limits, dict):
        raise ValueError(f"decision model {row['id']} has no limits")
    for name in LIMITS:
        value = limits.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(
                f"decision model {row['id']} limit {name} must be a positive integer, got {value!r}"
            )


@lru_cache(maxsize=1)
def _by_folded_id() -> dict[str, dict]:
    return {model_id.casefold(): row for model_id, row in models().items()}


def get(model_id: str) -> dict | None:
    """The catalog row for ``model_id``, matched without regard to case."""
    return _by_folded_id().get(model_id.casefold())
