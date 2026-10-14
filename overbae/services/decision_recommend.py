"""Which catalog model fits a decision dataset.

A model fits when its context holds the longest row's input with a 10% margin and its
serving limits hold the dataset's widest question shape. Among the fitting models with a
published Jev Decision Index, the pool is every model within 3 points of the best index,
and the smallest model in the pool is chosen. With no scored fitting model, the smallest
fitting model is chosen.
"""

from __future__ import annotations

import math

from overbae.modal.decision_catalog import models as catalog_models
from overbae.services.datasets import decisions, rows, versions
from overbae.services.datasets.text import approx_tokens

INDEX_MARGIN = 3.0
CONTEXT_MARGIN = 1.1


def choose(specs: list[dict], longest: int, shape: dict | None = None) -> dict:
    """``longest`` is the approximate token count of the longest row's input."""
    checked = [(spec, _why_not(spec, longest, shape)) for spec in specs]
    chosen, reason = _pick([spec for spec, why_not in checked if why_not is None])
    return {
        "model": None if chosen is None else chosen["id"],
        "reason": reason,
        "candidates": [_candidate(spec, why_not) for spec, why_not in checked],
    }


def recommend(dataset, version: str | None = None) -> dict:
    cell = versions.resolve_cell(dataset, version)
    count = longest = 0
    shapes = []
    for row in rows.iter_rows(cell):
        count += 1
        longest = max(longest, approx_tokens(row.input))
        try:
            shapes.append(decisions.shape(decisions.request(row.input)["questions"]))
        except decisions.DecisionRowError:
            continue
    shape = decisions.widest(shapes)
    result = choose(list(catalog_models().values()), longest, shape)
    return {
        **result,
        "dataset": {"rows": count, "longest_input_tokens": longest, "shape": shape},
    }


def _why_not(spec: dict, longest: int, shape: dict | None) -> str | None:
    reasons = []
    context = int(spec["max_context_length"])
    if longest * CONTEXT_MARGIN > context:
        if longest > context:
            reasons.append(f"Longest input is about {longest:,} tokens; context is {context:,}.")
        else:
            needed = math.ceil(longest * CONTEXT_MARGIN)
            reasons.append(
                f"Longest input is about {longest:,} tokens; with a 10% margin it needs "
                f"{needed:,}; context is {context:,}."
            )
    if shape is not None:
        reasons.extend(_limit_reasons(spec["limits"], shape))
    return " ".join(reasons) or None


def _limit_reasons(limits: dict, shape: dict) -> list[str]:
    found = []
    if shape["questions"] > limits["questions"]:
        found.append(
            f"Takes up to {limits['questions']} questions; this dataset has {shape['questions']}."
        )
    if shape["options"] > limits["options"]:
        found.append(
            f"Takes up to {limits['options']} options; this dataset has {shape['options']}."
        )
    if shape["score_levels"] > limits["score_levels"]:
        found.append(
            f"Takes up to {limits['score_levels']} score levels; this dataset has "
            f"{shape['score_levels']}."
        )
    return found


def _pick(fitting: list[dict]) -> tuple[dict | None, str]:
    if not fitting:
        return None, "No model fits."
    scored = [spec for spec in fitting if spec.get("decision_index") is not None]
    if not scored:
        chosen = min(fitting, key=lambda spec: int(spec["params"]))
        return chosen, (
            f"{chosen['id']}: smallest of {_count(len(fitting))}; none has a published "
            "Decision Index."
        )
    best = max(float(spec["decision_index"]) for spec in scored)
    pool = [spec for spec in scored if best - float(spec["decision_index"]) <= INDEX_MARGIN]
    chosen = min(pool, key=lambda spec: (int(spec["params"]), -float(spec["decision_index"])))
    if len(pool) == 1:
        return chosen, f"{chosen['id']}: best Decision Index ({best:g}) among models that fit."
    return chosen, (
        f"{chosen['id']}: smallest of {len(pool)} models within {INDEX_MARGIN:g} points of the "
        f"best Decision Index ({best:g})."
    )


def _count(n: int) -> str:
    return f"{n} model" if n == 1 else f"{n} models"


def _candidate(spec: dict, why_not: str | None) -> dict:
    index = spec.get("decision_index")
    return {
        "model": spec["id"],
        "fits": why_not is None,
        "why_not": why_not,
        "decision_index": None if index is None else float(index),
        "decision_index_edition": spec.get("decision_index_edition"),
        "params": int(spec["params"]),
    }
