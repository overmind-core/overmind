"""Percentile → skill index → task grade. Pure functions, no I/O.

Raw scores are never compared across benchmarks: different scales, different cohorts,
different saturation. A percentile inside a benchmark's own cohort is the only unit that
survives the comparison.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple

from .schema import BenchmarkScore, Provenance
from .taxonomy import TaskType, weights_for

Confidence = Literal["high", "medium", "low", "none"]

# A cohort of 1 would score log(1) = 0 and vanish from the weighted mean.
_MIN_COHORT_FOR_WEIGHT = 2


class SkillScore(NamedTuple):
    score: float
    benchmarks: frozenset[str]

    @property
    def n_benchmarks(self) -> int:
        return len(self.benchmarks)


@dataclass(frozen=True, slots=True)
class Contribution:
    skill: str
    percentile: float
    weight: float
    contribution: float


def percentile_of(sorted_cohort_scores: Sequence[float], score: float) -> float:
    """Midrank percentile of ``score`` within an ascending cohort: below + half of equal."""
    cohort_size = len(sorted_cohort_scores)
    if cohort_size == 0:
        return 0.0
    below = bisect_left(sorted_cohort_scores, score)
    equal = bisect_right(sorted_cohort_scores, score) - below
    return 100.0 * (below + 0.5 * equal) / cohort_size


def skill_index(scores: list[BenchmarkScore]) -> dict[str, SkillScore]:
    """Per-skill score out of 100 and the benchmarks behind it, weighted by log cohort size.

    A rank out of 563 is stronger evidence than a rank out of 29, so cohort size enters
    logarithmically rather than linearly. Each skill carries its benchmark slugs so callers
    can count evidence across skills without counting one benchmark several times.
    """
    by_axis: dict[str, list[BenchmarkScore]] = defaultdict(list)
    for score in scores:
        # Domains index alongside skills: SKILL_WEIGHTS blends the domain "Math".
        for axis in (*score.skills, *score.domains):
            by_axis[axis].append(score)

    index: dict[str, SkillScore] = {}
    for axis, axis_scores in by_axis.items():
        measured = [s for s in axis_scores if s.provenance == Provenance.MEASURED]
        # Lab-claimed values are a lower tier: one measured score retires all of them
        # for that skill rather than averaging with them.
        used = measured or axis_scores
        weights = [math.log(max(s.cohort_n, _MIN_COHORT_FOR_WEIGHT)) for s in used]
        total = sum(weights)
        weighted = sum(s.percentile * w for s, w in zip(used, weights, strict=True))
        index[axis] = SkillScore(
            score=weighted / total,
            benchmarks=frozenset(s.benchmark for s in used),
        )
    return index


def confidence(n_benchmarks: int, n_skills: int) -> Confidence:
    if n_benchmarks <= 0:
        return "none"
    if n_benchmarks == 1:
        return "low"
    if n_benchmarks >= 5 and n_skills >= 2:
        return "high"
    return "medium"


def grade(
    skill_idx: Mapping[str, SkillScore],
    task_type: TaskType | str,
    *,
    long_context: bool = False,
) -> tuple[float | None, Confidence, list[Contribution]]:
    """Blend the skill index by task weights, renormalised over the skills actually present.

    A skill with no benchmarks drops out of the blend, so a gap costs confidence, not grade.
    """
    weights = weights_for(task_type, long_context=long_context)
    present = {skill: weight for skill, weight in weights.items() if skill in skill_idx}
    total_weight = sum(present.values())
    if total_weight <= 0:
        return None, "none", []

    contributions = [
        Contribution(
            skill=str(skill),
            percentile=skill_idx[skill].score,
            weight=weight / total_weight,
            contribution=skill_idx[skill].score * weight / total_weight,
        )
        for skill, weight in sorted(present.items(), key=lambda item: (-item[1], item[0]))
    ]
    # One benchmark tagged with several skills is one piece of evidence, so confidence
    # reads the union of slugs rather than the sum of per-skill counts.
    evidence: set[str] = set()
    for skill in present:
        evidence |= skill_idx[skill].benchmarks
    n_benchmarks = len(evidence)
    grade_0_100 = sum(c.contribution for c in contributions)
    return grade_0_100, confidence(n_benchmarks, len(present)), contributions
