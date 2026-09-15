from __future__ import annotations

import pytest

from overbae.services.benchmarks.grading import (
    Confidence,
    SkillScore,
    confidence,
    grade,
    percentile_of,
    skill_index,
)
from overbae.services.benchmarks.schema import BenchmarkScore, Provenance
from overbae.services.benchmarks.taxonomy import Skill, TaskType

_IF = str(Skill.INSTRUCTION_FOLLOWING)
_FAITH = str(Skill.FAITHFULNESS)
_REASONING = str(Skill.REASONING)
_CODING = str(Skill.CODING)


def _score(
    skill: Skill,
    percentile: float,
    cohort_n: int,
    *,
    benchmark: str = "bench",
    provenance: Provenance = Provenance.MEASURED,
    extra_skills: tuple[Skill, ...] = (),
) -> BenchmarkScore:
    return BenchmarkScore(
        benchmark=benchmark,
        skills=(str(skill), *(str(s) for s in extra_skills)),
        domains=(),
        raw_score=0.5,
        scale="accuracy_0_1",
        percentile=percentile,
        cohort_n=cohort_n,
        source="leaderboard",
        source_url="https://arxiv.org/abs/2507.02833",
        observed_at="2026-08-11",
        provenance=provenance,
    )


def _skill(score: float, *benchmarks: str) -> SkillScore:
    return SkillScore(score=score, benchmarks=frozenset(benchmarks))


@pytest.mark.parametrize(
    ("cohort", "score", "expected"),
    [
        ([10.0, 20.0, 30.0, 40.0], 10.0, 12.5),
        ([10.0, 20.0, 30.0, 40.0], 40.0, 87.5),
        ([10.0, 20.0, 30.0, 40.0], 25.0, 50.0),
        ([10.0, 20.0, 30.0, 40.0], 5.0, 0.0),
        ([10.0, 20.0, 30.0, 40.0], 99.0, 100.0),
        ([10.0, 20.0, 20.0, 20.0, 50.0], 20.0, 50.0),
        ([5.0, 5.0, 9.0], 5.0, 100.0 / 3),
        ([5.0, 5.0, 9.0], 9.0, 250.0 / 3),
        ([42.0], 42.0, 50.0),
        ([], 42.0, 0.0),
    ],
)
def test_percentile_is_the_midrank_of_the_cohort(cohort, score, expected):
    assert percentile_of(cohort, score) == pytest.approx(expected)


def test_percentile_rises_with_score():
    cohort = [1.0, 2.0, 3.0, 4.0, 5.0]
    percentiles = [percentile_of(cohort, s) for s in (0.5, 1.0, 3.0, 5.0, 9.0)]
    assert percentiles == sorted(percentiles)


def test_single_benchmark_indexes_at_its_own_percentile():
    index = skill_index([_score(Skill.CODING, 73.0, 500)])
    assert index[_CODING].score == pytest.approx(73.0)
    assert index[_CODING].benchmarks == frozenset({"bench"})


def test_cohort_of_one_does_not_vanish_from_the_weighted_mean():
    index = skill_index([_score(Skill.CODING, 73.0, 1)])
    assert index[_CODING].score == pytest.approx(73.0)


def test_a_tiny_cohort_cannot_outweigh_a_large_one():
    tiny_win = skill_index(
        [
            _score(Skill.REASONING, 95.0, 4, benchmark="tiny"),
            _score(Skill.REASONING, 55.0, 900, benchmark="huge"),
        ]
    )
    large_win = skill_index(
        [
            _score(Skill.REASONING, 55.0, 4, benchmark="tiny"),
            _score(Skill.REASONING, 95.0, 900, benchmark="huge"),
        ]
    )
    unweighted_mean = 75.0
    assert tiny_win[_REASONING].score < unweighted_mean < large_win[_REASONING].score
    assert tiny_win[_REASONING].benchmarks == large_win[_REASONING].benchmarks == {"tiny", "huge"}


def test_measured_scores_retire_lab_claimed_ones_for_that_skill():
    index = skill_index(
        [
            _score(Skill.CODING, 40.0, 500, benchmark="measured"),
            _score(Skill.CODING, 99.0, 500, benchmark="claimed", provenance=Provenance.LAB_CLAIMED),
        ]
    )
    assert index[_CODING].score == pytest.approx(40.0)
    assert index[_CODING].benchmarks == frozenset({"measured"})


def test_lab_claimed_scores_are_used_when_nothing_is_measured():
    index = skill_index(
        [_score(Skill.CODING, 99.0, 500, provenance=Provenance.LAB_CLAIMED)],
    )
    assert index[_CODING].score == pytest.approx(99.0)
    assert index[_CODING].n_benchmarks == 1


def test_lab_claimed_retirement_is_per_skill():
    index = skill_index(
        [
            _score(Skill.CODING, 40.0, 500, benchmark="measured"),
            _score(
                Skill.REASONING, 99.0, 500, benchmark="claimed", provenance=Provenance.LAB_CLAIMED
            ),
        ]
    )
    assert index[_REASONING].score == pytest.approx(99.0)


def test_domains_index_alongside_skills():
    score = BenchmarkScore(
        benchmark="aime",
        skills=(str(Skill.REASONING),),
        domains=("Math",),
        raw_score=0.5,
        scale="accuracy_0_1",
        percentile=64.0,
        cohort_n=300,
        source="leaderboard",
        source_url="https://arxiv.org/abs/2501.14249",
        observed_at="2026-08-11",
        provenance=Provenance.MEASURED,
    )
    index = skill_index([score])
    assert index["Math"].score == pytest.approx(64.0)
    assert index[_REASONING].score == pytest.approx(64.0)


@pytest.mark.parametrize(
    ("n_benchmarks", "n_skills", "expected"),
    [
        (0, 0, "none"),
        (0, 3, "none"),
        (1, 1, "low"),
        (1, 2, "low"),
        (2, 1, "medium"),
        (4, 2, "medium"),
        (5, 1, "medium"),
        (5, 2, "high"),
        (12, 3, "high"),
    ],
)
def test_confidence_bands(n_benchmarks: int, n_skills: int, expected: Confidence):
    assert confidence(n_benchmarks, n_skills) == expected


def test_a_missing_skill_costs_confidence_not_grade():
    complete = {
        _IF: _skill(80.0, "if_a", "if_b", "if_c"),
        _FAITH: _skill(80.0, "faith_a", "faith_b"),
        _REASONING: _skill(80.0, "reason_a"),
    }
    partial = {_IF: _skill(80.0, "if_a"), _FAITH: _skill(80.0, "faith_a")}

    full_grade, full_confidence, _ = grade(complete, TaskType.EXTRACTION)
    partial_grade, partial_confidence, contributions = grade(partial, TaskType.EXTRACTION)

    assert partial_grade == pytest.approx(full_grade)
    assert full_confidence == "high"
    assert partial_confidence == "medium"
    assert {c.skill for c in contributions} == {_IF, _FAITH}


def test_grade_renormalises_over_the_skills_present():
    partial_grade, _, contributions = grade(
        {_IF: _skill(90.0, "if_a"), _FAITH: _skill(50.0, "faith_a")}, TaskType.EXTRACTION
    )

    assert partial_grade == pytest.approx((90.0 * 0.5 + 50.0 * 0.3) / 0.8)
    assert sum(c.weight for c in contributions) == pytest.approx(1.0)


def test_contributions_sum_to_the_grade():
    grade_0_100, _, contributions = grade(
        {
            _IF: _skill(88.2, "ifbench"),
            _FAITH: _skill(71.0, "tau2"),
            _REASONING: _skill(64.0, "mmlu_pro"),
        },
        TaskType.EXTRACTION,
    )

    assert sum(c.contribution for c in contributions) == pytest.approx(grade_0_100)
    assert sum(c.weight for c in contributions) == pytest.approx(1.0)
    assert [c.skill for c in contributions] == [_IF, _FAITH, _REASONING]


def test_grade_is_none_without_any_scores():
    grade_0_100, band, contributions = grade({}, TaskType.EXTRACTION)

    assert grade_0_100 is None
    assert band == "none"
    assert contributions == []


def test_grade_is_none_when_no_scored_skill_is_in_the_blend():
    grade_0_100, band, _ = grade({_CODING: _skill(90.0, "swebench")}, TaskType.TRANSLATION)

    assert grade_0_100 is None
    assert band == "none"


def test_grade_is_none_for_an_unknown_task_type():
    grade_0_100, band, _ = grade({_IF: _skill(90.0, "ifbench")}, "")

    assert grade_0_100 is None
    assert band == "none"


def test_a_benchmark_shared_by_several_skills_counts_once_towards_confidence():
    shared = skill_index(
        [
            _score(
                Skill.INSTRUCTION_FOLLOWING,
                90.0,
                400,
                benchmark="ifbench",
                extra_skills=(Skill.FAITHFULNESS, Skill.REASONING),
            )
        ]
    )
    assert {axis: shared[axis].benchmarks for axis in (_IF, _FAITH, _REASONING)} == {
        _IF: frozenset({"ifbench"}),
        _FAITH: frozenset({"ifbench"}),
        _REASONING: frozenset({"ifbench"}),
    }

    _, band, contributions = grade(shared, TaskType.EXTRACTION)

    assert len(contributions) == 3
    assert band == "low"


def test_overlapping_and_distinct_benchmarks_are_counted_as_a_union():
    index = {
        _IF: _skill(90.0, "ifbench", "shared"),
        _FAITH: _skill(80.0, "shared"),
        _REASONING: _skill(70.0, "shared", "mmlu_pro"),
    }

    _, band, _ = grade(index, TaskType.EXTRACTION)

    assert band == "medium"


def test_long_context_enters_the_blend_when_the_dataset_is_long():
    index = {
        _IF: _skill(90.0, "if_a", "if_b"),
        _FAITH: _skill(90.0, "faith_a", "faith_b"),
        str(Skill.LONG_CONTEXT): _skill(10.0, "lc_a", "lc_b"),
    }

    short_grade, _, _ = grade(index, TaskType.EXTRACTION)
    long_grade, _, contributions = grade(index, TaskType.EXTRACTION, long_context=True)

    assert long_grade < short_grade
    assert str(Skill.LONG_CONTEXT) in {c.skill for c in contributions}
