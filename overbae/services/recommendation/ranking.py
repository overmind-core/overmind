"""Turns benchmark evidence into a defensible order. Soft signal only — never a filter.

Hard constraints live in constraints.py. Nothing here may exclude a model from the list;
thin evidence lowers a model's standing, it does not disqualify it.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from overbae.services.benchmarks import artifact
from overbae.services.benchmarks.grading import (
    Confidence,
    Contribution,
    SkillScore,
    grade,
    skill_index,
)
from overbae.services.benchmarks.schema import Provenance
from overbae.services.benchmarks.taxonomy import TaskType, weights_for

# Below ten peers a percentile has coarser granularity than ten points and the cohort is
# not a market: "75th of 2" carries no information about standing. Such scores are
# discarded before grading rather than down-weighted, because there is nothing to weigh.
MIN_COHORT = 10

# Pull toward the prior with the weight of five benchmarks — the evidence count at which
# grading.confidence is willing to say "high". A model at that threshold is half prior;
# one benchmark is mostly prior; seventeen barely moves.
SHRINKAGE_K = 5.0

NEUTRAL_PRIOR = 50.0
_MIN_GRADED_FOR_PRIOR = 3

# Ordering on the posterior mean rewards ignorance: shrinkage pulls a LOW grade up when
# evidence is thin, so knowing nothing about a model moves it toward the middle of the
# pack. Order instead on the grade the evidence defends — a one-sided 95% lower bound
# whose width narrows as sqrt(n + k). Its scale is the spread of the graded population,
# the same population the prior mean comes from, falling back to an uninformed uniform
# 0-100 prior (sd = 100/sqrt(12)) when too few models are graded to measure it.
Z_LOWER_BOUND = 1.645
UNINFORMED_PRIOR_SD = 100.0 / math.sqrt(12.0)

# How many models the wizard opens with. Only the first is checked: the rest are there to
# be compared against, not to be trained by default.
DEFAULT_PICKS = 3


@dataclass(frozen=True, slots=True)
class SkillStanding:
    """One weighted skill on both scales: where the model sits among the graded candidates
    in this request, and where it sits among every model the artifact tracks."""

    skill: str
    weight: float
    percentile_in_field: float
    rank_in_field: int
    field_n: int
    percentile_global: float


@dataclass(frozen=True, slots=True)
class Ranked:
    """One model's standing. ``grade`` through ``lower_bound`` are the global evidence;
    ``match`` and ``skill_standings`` are that same evidence read against the other
    candidates in this request."""

    model: str
    grade: float | None
    adjusted_grade: float | None
    lower_bound: float | None
    confidence: Confidence
    n_benchmarks: int
    lab_claimed_only: bool
    skill_scores: dict[str, SkillScore]
    contributions: list[Contribution]
    dropped_thin_scores: int
    match: float | None = None
    match_rank: int | None = None
    match_pool: int = 0
    skill_standings: tuple[SkillStanding, ...] = ()


def rank(models: list[str], task_type: str, *, long_context: bool = False) -> list[Ranked]:
    """Every model graded and ordered by the grade its evidence defends, ungraded last."""
    weights = weights_for(task_type, long_context=long_context)
    graded = [
        _grade_model(model, task_type, weights, long_context=long_context)
        for model in dict.fromkeys(models)
    ]
    prior, prior_sd = _prior_moments(row.grade for row in graded)
    adjusted = []
    for row in graded:
        mean = _shrink(row.grade, row.n_benchmarks, prior)
        adjusted.append(
            replace(
                row,
                adjusted_grade=mean,
                lower_bound=_lower_bound(mean, row.n_benchmarks, prior_sd),
            )
        )
    return sorted(_with_match(_with_standings(adjusted, weights)), key=_order)


def select_default(ranked: Sequence[Ranked]) -> list[str]:
    """The highest-ranked models, in order."""
    return [row.model for row in ranked[:DEFAULT_PICKS]]


def _grade_model(
    model: str,
    task_type: TaskType | str,
    weights: Mapping[str, float],
    *,
    long_context: bool,
) -> Ranked:
    scores = artifact.scores_for(model)
    kept = [score for score in scores if score.cohort_n >= MIN_COHORT]
    index = skill_index(kept)
    grade_0_100, band, contributions = grade(index, task_type, long_context=long_context)

    evidence = _evidence_benchmarks(index, weights)
    measured = {s.benchmark for s in kept if s.provenance == Provenance.MEASURED}
    return Ranked(
        model=model,
        grade=grade_0_100,
        adjusted_grade=None,
        lower_bound=None,
        confidence=band,
        n_benchmarks=len(evidence),
        lab_claimed_only=bool(evidence) and evidence.isdisjoint(measured),
        skill_scores={str(axis): index[axis] for axis in weights if axis in index},
        contributions=contributions,
        dropped_thin_scores=len(scores) - len(kept),
    )


def _evidence_benchmarks(
    index: Mapping[str, SkillScore], weights: Mapping[str, float]
) -> frozenset[str]:
    """The benchmarks actually behind the grade — the skills the task blends, deduplicated."""
    benchmarks: set[str] = set()
    for axis in weights:
        score = index.get(axis)
        if score is not None:
            benchmarks |= score.benchmarks
    return frozenset(benchmarks)


def _prior_moments(grades: Iterable[float | None]) -> tuple[float, float]:
    known = [g for g in grades if g is not None]
    if len(known) < _MIN_GRADED_FOR_PRIOR:
        return NEUTRAL_PRIOR, UNINFORMED_PRIOR_SD
    mean = sum(known) / len(known)
    variance = sum((g - mean) ** 2 for g in known) / (len(known) - 1)
    return mean, math.sqrt(variance)


def _shrink(raw: float | None, n_benchmarks: int, prior: float) -> float | None:
    if raw is None:
        return None
    return (n_benchmarks * raw + SHRINKAGE_K * prior) / (n_benchmarks + SHRINKAGE_K)


def _lower_bound(adjusted: float | None, n_benchmarks: int, prior_sd: float) -> float | None:
    if adjusted is None:
        return None
    return adjusted - Z_LOWER_BOUND * prior_sd / math.sqrt(n_benchmarks + SHRINKAGE_K)


def _with_match(ordered: list[Ranked]) -> list[Ranked]:
    """The task's weights applied to the per-skill standings, and the place that earns.

    The match is the weighted mean of ``skill_standings`` over the *full* weight of the
    task, not over the skills a model happens to have data for: an uncovered skill scores
    zero and drags the mean down by its weight. That keeps the number derivable from the
    chart the wizard draws — every weighted skill is a bar, and the bars are all there is
    — and prices thin coverage without a separate shrinkage term.
    """
    graded = [row for row in ordered if row.lower_bound is not None]
    scored = [replace(row, match=round(_weighted_match(row), 2)) for row in graded]
    keys = sorted(_match_key(row) for row in scored)
    by_model = {row.model: row for row in scored}
    ranked = []
    for row in ordered:
        scored_row = by_model.get(row.model)
        if scored_row is None:
            ranked.append(replace(row, match_pool=len(keys)))
            continue
        _percentile, place = _midrank(keys, _match_key(scored_row))
        ranked.append(replace(scored_row, match_rank=place, match_pool=len(keys)))
    return ranked


def _weighted_match(row: Ranked) -> float:
    """Weights sum to 1 across the task, so an uncovered skill simply contributes nothing."""
    return sum(s.weight * s.percentile_in_field for s in row.skill_standings)


def _with_standings(ordered: list[Ranked], weights: Mapping[str, float]) -> list[Ranked]:
    """Per-skill standing among the graded candidates, heaviest skill first.

    A skill only part of the field has evidence for is scored against that smaller pool,
    so ``field_n`` is per skill rather than the size of the graded set.
    """
    fields = {
        str(skill): sorted(
            -row.skill_scores[str(skill)].score
            for row in ordered
            if row.lower_bound is not None and str(skill) in row.skill_scores
        )
        for skill in weights
    }
    by_weight = sorted(weights.items(), key=lambda item: (-item[1], str(item[0])))
    return [
        row
        if row.lower_bound is None
        else replace(
            row,
            skill_standings=tuple(
                _standing(str(skill), weight, row.skill_scores[str(skill)], fields[str(skill)])
                for skill, weight in by_weight
                if str(skill) in row.skill_scores
            ),
        )
        for row in ordered
    ]


def _standing(
    skill: str, weight: float, score: SkillScore, field: Sequence[float]
) -> SkillStanding:
    percentile, place = _midrank(field, -score.score)
    return SkillStanding(
        skill=skill,
        weight=float(weight),
        percentile_in_field=round(percentile, 1),
        rank_in_field=place,
        field_n=len(field),
        percentile_global=round(score.score, 1),
    )


def _midrank(keys: Sequence[Any], key: Any) -> tuple[float, int]:
    """Percentile and place of ``key`` in an ascending list of best-first keys. Ties share
    the better place and split the percentile between them."""
    better = bisect_left(keys, key)
    equal = bisect_right(keys, key) - better
    return 100.0 * (len(keys) - better - 0.5 * equal) / len(keys), better + 1


def _match_key(row: Ranked) -> tuple[int, float]:
    return (_evidence_tier(row), -(row.match or 0.0))


def _order(row: Ranked) -> tuple[int, float, int, str]:
    return (*_match_key(row), -row.n_benchmarks, row.model)


def _evidence_tier(row: Ranked) -> int:
    """Self-reported evidence sorts below every measured grade, however high it scores.

    A guarantee that only holds while today's numbers happen to line up is not a guarantee.
    """
    if row.lower_bound is None:
        return 2
    return 1 if row.lab_claimed_only else 0


__all__ = [
    "DEFAULT_PICKS",
    "MIN_COHORT",
    "SHRINKAGE_K",
    "UNINFORMED_PRIOR_SD",
    "Z_LOWER_BOUND",
    "Ranked",
    "SkillStanding",
    "rank",
    "select_default",
]
