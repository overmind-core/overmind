"""Which catalog model fits a decision dataset.

A model fits when its context holds the longest row. Among those, the highest
published Jev Decision Index wins. A model with no index is chosen only when
no scored model fits.
"""

from __future__ import annotations

from overbae.modal.decision_catalog import models as catalog_models
from overbae.services.datasets import rows, versions
from overbae.services.datasets.text import approx_tokens


def choose(specs: list[dict], longest: int) -> dict:
    fitting = [spec for spec in specs if int(spec["max_context_length"]) >= longest]
    scored = [spec for spec in fitting if spec.get("decision_index") is not None]
    pool = scored or fitting
    chosen = (
        max(pool, key=lambda spec: (float(spec.get("decision_index") or -1), -int(spec["params"])))
        if pool
        else None
    )
    return {
        "model": None if chosen is None else chosen["id"],
        "longest_row_tokens": longest,
        "reason": _reason(chosen, longest),
    }


def recommend(dataset, version: str | None = None) -> dict:
    cell = versions.resolve_cell(dataset, version)
    longest = 0
    for row in rows.iter_rows(cell):
        longest = max(
            longest, approx_tokens({"input": row.input, "expected_output": row.expected_output})
        )
    return choose(list(catalog_models().values()), longest)


def _reason(chosen: dict | None, longest: int) -> str:
    length = f"Longest row is about {longest:,} tokens."
    if chosen is None:
        return f"{length} No model fits."
    score = chosen.get("decision_index")
    if score is None:
        return f"{length} {chosen['id']} fits."
    edition = chosen.get("decision_index_edition")
    label = f" ({edition})" if edition else ""
    return f"{length} {chosen['id']} scores {score:g} on the Jev Decision Index{label}."
