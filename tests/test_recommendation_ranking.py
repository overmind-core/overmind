from __future__ import annotations

from itertools import pairwise

import pytest

from overbae.services.benchmarks import artifact
from overbae.services.benchmarks.grading import grade, skill_index
from overbae.services.benchmarks.schema import BenchmarkScore, Provenance
from overbae.services.benchmarks.taxonomy import Skill, TaskType
from overbae.services.recommendation.ranking import (
    MIN_COHORT,
    Ranked,
    rank,
    select_default,
)

_THIN_EVIDENCE_MODEL = "LiquidAI/LFM2.5-2.6B"
_MEASURED_MODEL = "Qwen/Qwen3-8B"
_TWO_SCORE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
_GSM8K_COHORT_OF_ONE_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
_SELF_REPORTED_MODEL = "meta-llama/Llama-3.1-8B-Instruct"


def _score(
    benchmark: str,
    percentile: float,
    cohort_n: int,
    *,
    skill: Skill = Skill.INSTRUCTION_FOLLOWING,
    provenance: Provenance = Provenance.MEASURED,
) -> BenchmarkScore:
    return BenchmarkScore(
        benchmark=benchmark,
        skills=(str(skill),),
        domains=(),
        raw_score=0.5,
        scale="accuracy_0_1",
        percentile=percentile,
        cohort_n=cohort_n,
        source="leaderboard",
        source_url=f"https://arxiv.org/abs/{benchmark}",
        observed_at="2026-08-11",
        provenance=str(provenance),
    )


# Every skill extraction weights, so a model built with _spread covers the whole task and
# its match is its standing rather than a measure of what it is missing.
_EXTRACTION_SKILLS = (Skill.INSTRUCTION_FOLLOWING, Skill.FAITHFULNESS, Skill.REASONING)


def _spread(
    model: str,
    percentile: float,
    n: int,
    *,
    cohort_n: int = 400,
    skills: tuple[Skill, ...] = _EXTRACTION_SKILLS,
) -> list[BenchmarkScore]:
    return [
        _score(f"{model}-{i}-{skill}", percentile, cohort_n, skill=skill)
        for i in range(n)
        for skill in skills
    ]


@pytest.fixture
def fake_scores(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[BenchmarkScore]]:
    table: dict[str, list[BenchmarkScore]] = {}
    monkeypatch.setattr(artifact, "scores_for", lambda model: list(table.get(model, ())))
    return table


def _by_model(ranked: list[Ranked]) -> dict[str, Ranked]:
    return {row.model: row for row in ranked}


def test_one_lab_claimed_score_no_longer_beats_seventeen_measured_ones():
    unfiltered_thin = grade(
        skill_index(artifact.scores_for(_THIN_EVIDENCE_MODEL)), TaskType.EXTRACTION
    )[0]
    unfiltered_measured = grade(
        skill_index(artifact.scores_for(_MEASURED_MODEL)), TaskType.EXTRACTION
    )[0]
    assert unfiltered_thin is not None and unfiltered_measured is not None
    assert unfiltered_thin > unfiltered_measured

    ranked = rank([_THIN_EVIDENCE_MODEL, _MEASURED_MODEL], TaskType.EXTRACTION)

    assert [row.model for row in ranked] == [_MEASURED_MODEL, _THIN_EVIDENCE_MODEL]
    measured, thin = ranked
    assert measured.n_benchmarks >= 15
    assert thin.grade is None
    assert thin.adjusted_grade is None
    assert thin.dropped_thin_scores == 1


def test_two_score_model_from_a_cohort_of_five_carries_no_evidence():
    row = rank([_TWO_SCORE_MODEL], TaskType.EXTRACTION)[0]

    assert row.grade is None
    assert row.n_benchmarks == 0
    assert row.dropped_thin_scores == 2


def test_a_cohort_of_one_never_reaches_the_grade():
    row = rank([_GSM8K_COHORT_OF_ONE_MODEL], TaskType.REASONING_MATH)[0]

    evidence = {benchmark for score in row.skill_scores.values() for benchmark in score.benchmarks}
    assert "gsm8k" not in evidence
    assert row.dropped_thin_scores >= 1
    assert row.grade is not None


def test_a_single_self_reported_score_does_not_outrank_a_wall_of_measured_ones():
    ranked = rank([_SELF_REPORTED_MODEL, _MEASURED_MODEL], TaskType.EXTRACTION)
    order = [row.model for row in ranked]
    by_model = _by_model(ranked)
    self_reported, measured = by_model[_SELF_REPORTED_MODEL], by_model[_MEASURED_MODEL]

    assert (self_reported.lab_claimed_only, self_reported.n_benchmarks) == (True, 1)
    assert measured.lab_claimed_only is False
    assert measured.n_benchmarks > 10
    assert self_reported.grade < measured.grade
    assert self_reported.adjusted_grade > measured.adjusted_grade
    assert order.index(_SELF_REPORTED_MODEL) > order.index(_MEASURED_MODEL)


def test_no_self_reported_model_outranks_a_measured_one_across_the_artifact():
    graded = [
        row
        for row in rank(list(artifact.load().models), TaskType.EXTRACTION)
        if row.adjusted_grade is not None
    ]

    tiers = [row.lab_claimed_only for row in graded]
    assert any(tiers) and not all(tiers)
    assert tiers == sorted(tiers)


@pytest.mark.parametrize("cohort_n", [1, 2, MIN_COHORT - 1])
def test_scores_below_the_evidence_floor_are_dropped(fake_scores, cohort_n: int):
    fake_scores["thin"] = [_score("bench", 99.0, cohort_n)]
    fake_scores["thick"] = [_score("bench", 99.0, MIN_COHORT)]

    ranked = _by_model(rank(["thin", "thick"], TaskType.EXTRACTION))

    assert ranked["thin"].grade is None
    assert ranked["thin"].n_benchmarks == 0
    assert ranked["thin"].dropped_thin_scores == 1
    assert ranked["thick"].grade == pytest.approx(99.0)
    assert ranked["thick"].dropped_thin_scores == 0


def test_shrinkage_pulls_thin_evidence_further_toward_the_prior(fake_scores):
    for i in range(4):
        fake_scores[f"filler-{i}"] = _spread(f"filler-{i}", 10.0, 6)
    for n in (1, 4, 16):
        fake_scores[f"probe-{n}"] = _spread(f"probe-{n}", 90.0, n)

    ranked = _by_model(rank([*fake_scores], TaskType.EXTRACTION))
    probes = [ranked[f"probe-{n}"] for n in (1, 4, 16)]

    assert [p.grade for p in probes] == [pytest.approx(90.0)] * 3
    gaps = [p.grade - p.adjusted_grade for p in probes]
    assert gaps[0] > gaps[1] > gaps[2] > 0
    assert [p.model for p in sorted(probes, key=lambda p: -p.adjusted_grade)] == [
        "probe-16",
        "probe-4",
        "probe-1",
    ]


def test_the_lower_bound_closes_on_the_grade_as_evidence_accumulates(fake_scores):
    counts = (1, 2, 4, 8, 16)
    for i in range(4):
        fake_scores[f"filler-{i}"] = _spread(f"filler-{i}", 10.0, 6)
    for n in counts:
        fake_scores[f"probe-{n}"] = _spread(f"probe-{n}", 90.0, n)

    ranked = _by_model(rank([*fake_scores], TaskType.EXTRACTION))
    probes = [ranked[f"probe-{n}"] for n in counts]

    assert all(p.lower_bound < p.adjusted_grade for p in probes)
    widths = [p.adjusted_grade - p.lower_bound for p in probes]
    assert all(wider > narrower for wider, narrower in pairwise(widths))
    bounds = [p.lower_bound for p in probes]
    assert all(lower < higher for lower, higher in pairwise(bounds))


def test_a_lone_prior_holds_when_too_few_models_are_graded(fake_scores):
    fake_scores["only"] = _spread("only", 90.0, 1, skills=(Skill.INSTRUCTION_FOLLOWING,))

    row = rank(["only", "ungraded"], TaskType.EXTRACTION)[0]

    assert row.adjusted_grade == pytest.approx((90.0 + 5 * 50.0) / 6)


def test_ungraded_models_sort_last(fake_scores):
    fake_scores["weak"] = _spread("weak", 1.0, 3)
    fake_scores["strong"] = _spread("strong", 95.0, 3)

    ranked = rank(["ungraded-b", "weak", "ungraded-a", "strong"], TaskType.EXTRACTION)

    assert [row.model for row in ranked] == ["strong", "weak", "ungraded-a", "ungraded-b"]
    assert [row.adjusted_grade is None for row in ranked] == [False, False, True, True]
    assert ranked[-1].confidence == "none"


def test_a_model_with_only_self_reported_scores_is_flagged(fake_scores):
    fake_scores["lab"] = [
        _score("bench-a", 99.0, 400, provenance=Provenance.LAB_CLAIMED),
        _score("bench-b", 99.0, 400, provenance=Provenance.LAB_CLAIMED),
    ]
    fake_scores["mixed"] = [
        _score("bench-a", 99.0, 400, provenance=Provenance.LAB_CLAIMED),
        _score("bench-b", 80.0, 400),
    ]
    fake_scores["measured"] = _spread("measured", 70.0, 2)

    ranked = _by_model(rank(["lab", "mixed", "measured"], TaskType.EXTRACTION))

    assert ranked["lab"].lab_claimed_only is True
    assert ranked["mixed"].lab_claimed_only is False
    assert ranked["measured"].lab_claimed_only is False


def test_self_reported_evidence_sorts_below_measured_even_when_it_grades_higher(fake_scores):
    fake_scores["lab"] = [
        _score(f"bench-{i}", 99.0, 400, provenance=Provenance.LAB_CLAIMED) for i in range(8)
    ]
    for model, percentile in (("measured-a", 70.0), ("measured-b", 60.0), ("measured-c", 55.0)):
        fake_scores[model] = _spread(model, percentile, 8)

    ranked = rank([*fake_scores], TaskType.EXTRACTION)
    lab = _by_model(ranked)["lab"]
    measured = [row for row in ranked if not row.lab_claimed_only]

    assert lab.adjusted_grade > max(row.adjusted_grade for row in measured)
    assert lab.lower_bound > max(row.lower_bound for row in measured)
    assert [row.model for row in ranked] == ["measured-a", "measured-b", "measured-c", "lab"]


def test_a_self_reported_model_never_takes_the_primary_slot(fake_scores):
    fake_scores["lab"] = [
        _score(f"bench-{i}", 99.0, 400, provenance=Provenance.LAB_CLAIMED) for i in range(8)
    ]
    fake_scores["measured"] = _spread("measured", 70.0, 8)

    # Ordered lab-first so the sort, not the input order, decides the pick.
    ranked = sorted(rank(["lab", "measured"], TaskType.EXTRACTION), key=lambda row: row.model)
    assert ranked[0].lab_claimed_only is True

    assert select_default(rank(["lab", "measured"], TaskType.EXTRACTION))[0] == "measured"


def test_the_defaults_are_the_highest_ranked_models_in_order(fake_scores):
    for i, percentile in enumerate((60.0, 90.0, 70.0, 80.0)):
        fake_scores[f"model-{i}"] = _spread(f"model-{i}", percentile, 8)

    assert select_default(rank([f"model-{i}" for i in range(4)], TaskType.EXTRACTION)) == [
        "model-1",
        "model-3",
        "model-2",
    ]


def test_the_defaults_stop_at_the_models_there_are(fake_scores):
    fake_scores["a"] = _spread("a", 90.0, 8)
    fake_scores["b"] = _spread("b", 80.0, 8)

    assert select_default(rank(["a", "b"], TaskType.EXTRACTION)) == ["a", "b"]
    assert select_default([]) == []


def test_ranking_never_drops_a_model(fake_scores):
    fake_scores["graded"] = _spread("graded", 90.0, 8)
    fake_scores["thin"] = [_score("bench", 99.0, 2)]

    ranked = rank(["graded", "thin", "unknown", "graded"], TaskType.EXTRACTION)

    assert sorted(row.model for row in ranked) == ["graded", "thin", "unknown"]


def _catalog(fake_scores, count: int) -> list[str]:
    """A field of ``count`` models spread across the percentile range, all measured."""
    for i in range(count):
        model = f"model-{i:02d}"
        fake_scores[model] = _spread(model, 95.0 - i * 2.5, 8)
    return [*fake_scores]


def test_the_best_of_a_full_catalog_reads_as_the_best_not_as_mid_pack(fake_scores):
    ranked = rank(_catalog(fake_scores, 30), TaskType.EXTRACTION)

    top = ranked[0]
    assert top.match >= 90
    assert (top.match_rank, top.match_pool) == (1, 30)
    # The global grade is what made a 27B model look mediocre; match is the same evidence
    # read against the field the user can actually train.
    assert top.grade < 96


def test_fit_never_disagrees_with_the_order_it_presents(fake_scores):
    ranked = rank(_catalog(fake_scores, 30), TaskType.EXTRACTION)

    matches = [row.match for row in ranked if row.match is not None]
    assert len(matches) == 30
    assert all(higher >= lower for higher, lower in pairwise(matches))
    assert [row.match_rank for row in ranked] == list(range(1, 31))


def test_equal_evidence_scores_equal_without_breaking_the_run(fake_scores):
    for model in ("tie-a", "tie-b", "tie-c"):
        fake_scores[model] = _spread(model, 80.0, 8)
    for i, percentile in enumerate((60.0, 40.0, 20.0)):
        fake_scores[f"rest-{i}"] = _spread(f"rest-{i}", percentile, 8)

    ranked = _by_model(rank([*fake_scores], TaskType.EXTRACTION))
    tied = [ranked[model] for model in ("tie-a", "tie-b", "tie-c")]

    assert [row.match for row in tied] == [pytest.approx(100.0 * (6 - 0 - 1.5) / 6)] * 3
    assert {row.match_rank for row in tied} == {1}
    assert ranked["rest-0"].match < min(row.match for row in tied)


def test_an_ungraded_model_states_no_fit(fake_scores):
    fake_scores["graded"] = _spread("graded", 90.0, 8)

    ranked = _by_model(rank(["graded", "ungraded"], TaskType.EXTRACTION))

    assert (ranked["ungraded"].match, ranked["ungraded"].match_rank) == (None, None)
    assert ranked["ungraded"].match_pool == 1
    assert ranked["graded"].match is not None


def test_a_field_of_three_reads_as_a_field_of_three(fake_scores):
    ranked = rank(_catalog(fake_scores, 3), TaskType.EXTRACTION)

    assert [row.match for row in ranked] == [83.3, 50.0, 16.7]
    assert [row.match_pool for row in ranked] == [3, 3, 3]


def test_fit_scores_the_models_passed_in_not_the_whole_artifact():
    shortlist = [_MEASURED_MODEL, _SELF_REPORTED_MODEL, _GSM8K_COHORT_OF_ONE_MODEL]

    shortlisted = _by_model(rank(shortlist, TaskType.EXTRACTION))
    whole = _by_model(rank(list(artifact.load().models), TaskType.EXTRACTION))

    short, full = shortlisted[_MEASURED_MODEL], whole[_MEASURED_MODEL]
    assert short.match_pool < full.match_pool
    assert short.match > full.match
    assert short.match_rank < full.match_rank
    assert short.lower_bound != full.lower_bound


def test_the_top_candidate_leads_the_field_on_the_skill_that_decides_the_pick(fake_scores):
    ranked = rank(_catalog(fake_scores, 30), TaskType.EXTRACTION)

    leading = ranked[0].skill_standings[0]
    assert (leading.skill, leading.weight) == (str(Skill.INSTRUCTION_FOLLOWING), 0.5)
    assert (leading.rank_in_field, leading.field_n) == (1, 30)
    assert leading.percentile_in_field > 98


def test_a_skill_rank_reads_the_same_order_as_the_percentile_beside_it(fake_scores):
    ranked = rank(_catalog(fake_scores, 30), TaskType.EXTRACTION)
    standings = [row.skill_standings[0] for row in ranked]

    assert [row.rank_in_field for row in standings] == list(range(1, 31))
    percentiles = [row.percentile_in_field for row in standings]
    assert all(higher > lower for higher, lower in pairwise(percentiles))
    for row in standings:
        share = (row.field_n - row.rank_in_field + 0.5) / row.field_n
        assert row.percentile_in_field == pytest.approx(100.0 * share, abs=0.05)


def test_a_skill_only_some_candidates_carry_is_read_against_that_smaller_field(fake_scores):
    for i in range(4):
        fake_scores[f"model-{i}"] = _spread(
            f"model-{i}", 90.0 - i * 10, 4, skills=(Skill.INSTRUCTION_FOLLOWING,)
        )
    for i in range(2):
        fake_scores[f"model-{i}"].append(
            _score(f"faith-{i}", 40.0 - i * 10, 400, skill=Skill.FAITHFULNESS)
        )

    ranked = _by_model(rank([f"model-{i}" for i in range(4)], TaskType.EXTRACTION))
    standings = {row.skill: row for row in ranked["model-0"].skill_standings}

    assert standings[str(Skill.INSTRUCTION_FOLLOWING)].field_n == 4
    faithfulness = standings[str(Skill.FAITHFULNESS)]
    assert (faithfulness.rank_in_field, faithfulness.field_n) == (1, 2)
    # Leading a field of two on a middling global percentile is exactly the contrast the
    # candidate-set scale exists to show.
    assert faithfulness.percentile_global == 40.0
    assert faithfulness.percentile_in_field == 75.0
    assert {row.skill for row in ranked["model-2"].skill_standings} == {
        str(Skill.INSTRUCTION_FOLLOWING)
    }


def test_the_global_percentile_survives_beside_the_in_field_one(fake_scores):
    ranked = rank(_catalog(fake_scores, 30), TaskType.EXTRACTION)

    top = ranked[0].skill_standings[0]
    assert top.percentile_global == 95.0
    assert ranked[-1].skill_standings[0].percentile_global == pytest.approx(95.0 - 29 * 2.5)
    assert top.percentile_in_field > top.percentile_global


def test_an_ungraded_model_states_no_skill_standing(fake_scores):
    fake_scores["graded"] = _spread("graded", 90.0, 8)

    ranked = _by_model(rank(["graded", "ungraded"], TaskType.EXTRACTION))

    assert ranked["ungraded"].skill_standings == ()
    assert ranked["graded"].skill_standings
