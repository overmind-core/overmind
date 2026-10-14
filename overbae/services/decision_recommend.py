"""Which catalog models fit a decision dataset, as labelled trade-off picks.

A model fits when its context holds the longest row's input with a 10% margin and its
serving limits hold the dataset's widest question shape. Among the fitting models:
``strongest`` has the highest Jev Decision Index, ``smaller`` has the highest index among
indexed models at most half the strongest's size, and ``smallest`` has the fewest
parameters. No pick is ranked above another.
"""

from __future__ import annotations

import math

from overbae.modal.decision_catalog import models as catalog_models
from overbae.services.datasets import decisions, rows, versions
from overbae.services.datasets.text import approx_tokens

CONTEXT_MARGIN = 1.1


def choose(specs: list[dict], longest: int, shape: dict | None = None) -> dict:
    """``longest`` is the approximate token count of the longest row's input."""
    checked = [(spec, _why_not(spec, longest, shape)) for spec in specs]
    return {
        "picks": _picks([spec for spec, why_not in checked if why_not is None]),
        "candidates": [_candidate(spec, why_not) for spec, why_not in checked],
    }


def recommend(dataset, version: str | None = None, *, eval_cell=None) -> dict:
    """Fit is judged on the train version and, when given, the eval version too:
    a job is refused if either exceeds the model."""
    cell = versions.resolve_cell(dataset, version)
    count = longest = 0
    shapes = []
    for scanned in (cell, eval_cell):
        if scanned is None:
            continue
        for row in rows.iter_rows(scanned):
            count += scanned is cell
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


def _picks(fitting: list[dict]) -> list[dict]:
    indexed = [spec for spec in fitting if spec.get("decision_index") is not None]
    found: list[tuple[str, dict, str]] = []
    if indexed:
        strongest = max(indexed, key=lambda spec: float(spec["decision_index"]))
        found.append(
            ("strongest", strongest, f"Highest Decision Index that fits ({_index(strongest)}).")
        )
        halves = [spec for spec in indexed if int(spec["params"]) * 2 <= int(strongest["params"])]
        if halves:
            smaller = max(halves, key=lambda spec: float(spec["decision_index"]))
            found.append(
                (
                    "smaller",
                    smaller,
                    f"{_size(smaller)}, Decision Index {_index(smaller)}; under half the size "
                    f"of {strongest['id']}.",
                )
            )
    if fitting:
        smallest = min(
            fitting,
            key=lambda spec: (int(spec["params"]), -float(spec.get("decision_index") or 0)),
        )
        found.append(("smallest", smallest, f"Smallest model that fits ({_size(smallest)})."))
    picks: dict[str, dict] = {}
    for label, spec, reason in found:
        pick = picks.setdefault(spec["id"], {"labels": [], "model": spec["id"], "reasons": []})
        pick["labels"].append(label)
        pick["reasons"].append(reason)
    return [
        {"labels": pick["labels"], "model": pick["model"], "reason": " ".join(pick["reasons"])}
        for pick in picks.values()
    ]


def _index(spec: dict) -> str:
    return f"{float(spec['decision_index']):g}"


def _size(spec: dict) -> str:
    return f"{int(spec['params']) / 1e9:.1f}B"


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
