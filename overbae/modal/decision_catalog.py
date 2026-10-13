from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_CATALOG = Path(__file__).with_name("decision_models.json")


@lru_cache(maxsize=1)
def models() -> dict[str, dict]:
    rows = json.loads(_CATALOG.read_text())["models"]
    return {row["id"]: row for row in rows}


def get(model_id: str) -> dict | None:
    return models().get(model_id)
