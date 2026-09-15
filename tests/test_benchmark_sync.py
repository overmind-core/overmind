from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from overbae.modal import model_registry
from overbae.services.benchmarks import artifact as artifact_module
from overbae.services.benchmarks.schema import PARSER_VERSION, Provenance, parse_artifact
from overbae.services.benchmarks.sync import build
from overbae.services.benchmarks.sync import huggingface as hf
from overbae.services.benchmarks.sync import leaderboard as lb
from overbae.services.benchmarks.sync.huggingface import BenchmarkRow, SyncReport
from overbae.services.benchmarks.sync.leaderboard import EvalScore

_FIXTURE = Path(__file__).parent / "fixtures" / "leaderboard_page.html"

_QWEN = "Qwen/Qwen3.5-9B-Instruct"


def _page() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def _by_slug(scores: list[EvalScore], model_slug: str) -> dict[str, list[EvalScore]]:
    grouped: dict[str, list[EvalScore]] = {}
    for score in scores:
        if score.model_slug == model_slug:
            grouped.setdefault(score.eval_slug, []).append(score)
    return grouped


def test_rsc_payload_is_reassembled_across_push_chunks():
    spanning = '"mmlu_pro":0.55,"ifbench":0.48'

    rsc = lb.extract_rsc(_page())

    assert spanning not in _page()
    assert spanning in rsc


def test_every_model_record_is_recovered_from_the_payload():
    records = lb.model_records(lb.extract_rsc(_page()))

    assert set(records) == {"qwen3-5-9b-instruct", "llama-4-scout", "closed-frontier-2"}
    assert records["llama-4-scout"]["name"] == "Llama 4 Scout — 17B"


def test_parsed_score_carries_its_scale_tags_and_hub_id():
    (score,) = _by_slug(lb.parse(_page()), "qwen3-5-9b-instruct")["ifbench"]

    assert score.raw_score == pytest.approx(0.48)
    assert score.scale == "accuracy_0_1"
    assert score.skills == ("Instruction Following",)
    assert score.provenance == Provenance.MEASURED
    assert score.hf_model_id == _QWEN
    assert score.is_open_weights


def test_elo_scored_evaluation_keeps_its_native_scale():
    (score,) = _by_slug(lb.parse(_page()), "qwen3-5-9b-instruct")["gdpval"]

    assert score.raw_score == pytest.approx(1180.5)
    assert score.scale == "elo"


def test_multilingual_evaluation_reduces_to_the_mean_of_its_languages():
    (score,) = _by_slug(lb.parse(_page()), "qwen3-5-9b-instruct")["global-mmlu-lite"]

    assert score.raw_score == pytest.approx(0.80)


def test_non_numeric_field_is_never_scored():
    slugs = {score.eval_slug for score in lb.parse(_page())}

    assert "critpt" not in slugs


def test_lab_claimed_value_lands_on_the_same_slug_at_a_lower_provenance():
    gpqa = _by_slug(lb.parse(_page()), "qwen3-5-9b-instruct")["gpqa-diamond"]

    assert {(score.raw_score, score.provenance) for score in gpqa} == {
        (0.61, Provenance.MEASURED),
        (0.69, Provenance.LAB_CLAIMED),
    }


def test_closed_weights_model_resolves_to_no_hub_id():
    scores = _by_slug(lb.parse(_page()), "closed-frontier-2")["gpqa-diamond"]

    assert scores[0].hf_model_id is None
    assert not scores[0].is_open_weights


def test_a_benchmark_carrying_no_skill_or_domain_is_dropped_before_grading():
    graded = _upstream_score("qwen-3-5-9b", "gpqa-diamond", 0.61, _QWEN)
    untagged = EvalScore(
        **{**asdict(graded), "eval_slug": "composite-index", "skills": (), "domains": ()}
    )

    tagged, dropped = build._split_untagged([graded, untagged])

    assert tagged == [graded]
    assert dropped == {"composite-index": 1}


@pytest.mark.parametrize(
    "url",
    ["", None, "https://example.com/Qwen/Qwen3.5-9B", "https://huggingface.co/Qwen"],
)
def test_hub_id_is_only_read_from_a_two_part_hub_url(url: str | None):
    assert lb.hf_model_id(url) is None


def _eval_entry(dataset_id: str, task_id: str, value: Any) -> dict[str, Any]:
    return {
        "verified": False,
        "data": {
            "dataset": {"id": dataset_id, "task_id": task_id},
            "value": value,
            "date": "2026-06-25",
            "source": {"name": "EvalEval", "url": "https://huggingface.co/datasets/evaleval"},
        },
    }


def _stub(payloads: dict[str, Any]):
    def fetch(model_id: str) -> Any:
        return payloads[model_id]

    return fetch


def test_hub_adapter_reads_a_mapped_benchmark_off_a_stubbed_fetcher():
    report = hf.collect(
        [_QWEN],
        fetcher=_stub({_QWEN: {"evalResults": [_eval_entry("openai/gsm8k", "gsm8k", 88.4)]}}),
    )

    (row,) = report.rows
    assert row["hf_model_id"] == _QWEN
    assert row["eval_slug"] == "gsm8k"
    assert row["raw_score"] == pytest.approx(0.884)
    assert row["provenance"] == Provenance.MEASURED


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "not json",
        ["evalResults"],
        {"evalResults": {"gsm8k": 0.9}},
        {"evalResults": ["a bare string"]},
        {"evalResults": [{"data": "not an object"}]},
        {"evalResults": [_eval_entry("", "", 0.9)]},
        {"evalResults": [_eval_entry("openai/gsm8k", "gsm8k", "n/a")]},
        {"evalResults": [_eval_entry("openai/gsm8k", "gsm8k", True)]},
    ],
)
def test_malformed_hub_response_is_skipped_rather_than_raised(payload: Any):
    report = hf.collect(["m"], fetcher=_stub({"m": payload}))

    assert report.rows == []


def test_a_malformed_model_does_not_cost_the_next_one():
    report = hf.collect(
        ["broken", "fine"],
        fetcher=_stub(
            {
                "broken": {"evalResults": [{"filename": "gsm8k.yaml", "error": "Invalid input"}]},
                "fine": {"evalResults": [_eval_entry("openai/gsm8k", "gsm8k", 71.0)]},
            }
        ),
    )

    assert [row["hf_model_id"] for row in report.rows] == ["fine"]
    assert report.client_parse_failures == ["broken"]


def _indexes(*hub_ids: tuple[str, str]):
    return build._slug_indexes(
        [_upstream_score(slug, "gpqa-diamond", 0.5, hf_id) for slug, hf_id in hub_ids]
    )


def test_exact_hub_id_joins_the_catalog_entry():
    match = build._resolve(_QWEN, (_QWEN,), _indexes(("qwen-3-5-9b", _QWEN)))

    assert (match.match_type, match.upstream_slugs) == ("exact", ("qwen-3-5-9b",))
    assert match.hf_model_id == _QWEN


def test_instruct_suffix_is_normalised_away_when_no_id_matches_exactly():
    match = build._resolve(
        "meta-llama/Llama-3.2-3B",
        ("meta-llama/Llama-3.2-3B",),
        _indexes(("llama-3-2-3b", "meta-llama/Llama-3.2-3B-Instruct")),
    )

    assert (match.match_type, match.upstream_slugs) == ("normalized", ("llama-3-2-3b",))


def test_mirror_org_reupload_joins_the_original_on_the_model_name():
    match = build._resolve(
        "google/gemma-4-12B-it",
        ("unsloth/gemma-4-12b-it",),
        _indexes(("gemma-4-12b", "google/gemma-4-12b-it")),
    )

    assert (match.match_type, match.upstream_slugs) == ("name_only", ("gemma-4-12b",))


def test_a_catalog_entry_upstream_never_saw_falls_back_to_the_hub_alone():
    match = build._resolve(
        "fdtn-ai/antares-1b", ("fdtn-ai/antares-1b",), _indexes(("qwen-3-5-9b", _QWEN))
    )

    assert (match.match_type, match.upstream_slugs) == ("hub_only", ())
    assert match.hf_model_id == "fdtn-ai/antares-1b"


def test_explicit_leaderboard_slug_joins_when_the_record_has_no_hub_url():
    match = build._resolve(
        "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B",
        ("unsloth/NVIDIA-Nemotron-3.5-Lightning-30B-A3B",),
        _indexes(("qwen-3-5-9b", _QWEN)),
        known_slugs={"nemotron-3-5-lightning"},
        leaderboard_slugs=("nemotron-3-5-lightning",),
    )

    assert match.match_type == "slug"
    assert match.upstream_slugs == ("nemotron-3-5-lightning",)


def test_nemotron_lightning_joins_official_card_and_leaderboard_slug():
    key = "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B"
    assert "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16" in build._catalog()[key]
    assert build._leaderboard_slugs()[key] == ("nemotron-3-5-lightning",)


def test_an_exact_hub_id_outranks_a_looser_candidate():
    match = build._resolve(
        _QWEN, (_QWEN,), _indexes(("exact-hit", _QWEN), ("loose-hit", "unsloth/Qwen3.5-9B-bf16"))
    )

    assert (match.match_type, match.upstream_slugs) == ("exact", ("exact-hit",))


def _upstream_score(
    model_slug: str,
    eval_slug: str,
    raw_score: float,
    hf_model_id: str | None = None,
    *,
    scale: str = "accuracy_0_1",
    provenance: Provenance = Provenance.MEASURED,
) -> EvalScore:
    return EvalScore(
        model_slug=model_slug,
        model_name=model_slug,
        hf_model_id=hf_model_id,
        is_open_weights=True,
        eval_slug=eval_slug,
        raw_score=raw_score,
        scale=scale,
        skills=("Reasoning",),
        domains=("Science",),
        provenance=str(provenance),
    )


def _hub_row(
    hf_model_id: str,
    eval_slug: str,
    raw_score: float,
    *,
    provenance: Provenance = Provenance.MEASURED,
) -> BenchmarkRow:
    return BenchmarkRow(
        hf_model_id=hf_model_id,
        eval_slug=eval_slug,
        raw_score=raw_score,
        scale="accuracy_0_1",
        skills=("Coding",),
        domains=(),
        provenance=str(provenance),
        source=hf.SOURCE,
        source_url=f"https://huggingface.co/{hf_model_id}",
        observed_at="2026-06-25",
    )


@pytest.fixture
def small_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A build over a two-model catalog, with the size floors and the previous artifact
    out of the way so composition can be asserted on its own."""
    entries = [
        {"id": _QWEN, "hf_model_id": _QWEN, "backend": "together"},
        {"id": "fdtn-ai/antares-1b", "hf_model_id": "fdtn-ai/antares-1b", "backend": "together"},
    ]
    monkeypatch.setattr(model_registry, "all_model_entries", lambda: entries)
    monkeypatch.setattr(artifact_module, "ARTIFACT_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(artifact_module, "_cached", None)
    monkeypatch.setattr(build, "MIN_MODELS", 1)
    monkeypatch.setattr(build, "MIN_BENCHMARKS", 1)


def test_percentile_ranks_the_score_inside_the_whole_upstream_cohort(small_catalog):
    cohort = [0.20, 0.35, 0.44, 0.61, 0.80]
    upstream = [
        _upstream_score(f"model-{i}", "gpqa-diamond", value, f"other/model-{i}")
        for i, value in enumerate(cohort)
    ]
    upstream[3] = _upstream_score("qwen-3-5-9b", "gpqa-diamond", 0.61, _QWEN)

    result = build.build(
        upstream_scores=upstream, hf_report=SyncReport(), generated_at="2026-08-11"
    )

    (score,) = result.payload["models"][_QWEN]
    assert score["percentile"] == pytest.approx(70.0)
    assert score["cohort_n"] == len(cohort)
    assert result.uncovered == ["fdtn-ai/antares-1b"]


def test_lab_claimed_scores_rank_against_lab_claimed_scores_only(small_catalog):
    upstream = [
        _upstream_score("a", "gpqa-diamond", 0.10, "other/a"),
        _upstream_score("b", "gpqa-diamond", 0.20, "other/b"),
        _upstream_score("c", "gpqa-diamond", 0.30, "other/c"),
        _upstream_score("d", "gpqa-diamond", 0.50, "other/d", provenance=Provenance.LAB_CLAIMED),
        _upstream_score(
            "qwen-3-5-9b", "gpqa-diamond", 0.90, _QWEN, provenance=Provenance.LAB_CLAIMED
        ),
    ]

    result = build.build(
        upstream_scores=upstream, hf_report=SyncReport(), generated_at="2026-08-11"
    )

    (score,) = result.payload["models"][_QWEN]
    assert score["provenance"] == Provenance.LAB_CLAIMED
    assert score["cohort_n"] == 2
    assert score["percentile"] == pytest.approx(75.0)


def test_the_leaderboard_wins_a_benchmark_the_hub_also_reports(small_catalog):
    upstream = [_upstream_score("qwen-3-5-9b", "gpqa-diamond", 0.61, _QWEN)]
    hub = SyncReport(
        rows=[
            _hub_row(_QWEN, "gpqa-diamond", 0.99),
            _hub_row(_QWEN, "swe-bench-verified", 0.42),
        ]
    )

    result = build.build(upstream_scores=upstream, hf_report=hub, generated_at="2026-08-11")

    emitted = {score["benchmark"]: score for score in result.payload["models"][_QWEN]}
    assert emitted["gpqa-diamond"]["source"] == lb.SOURCE
    assert emitted["gpqa-diamond"]["raw_score"] == pytest.approx(0.61)
    assert emitted["swe-bench-verified"]["source"] == hf.SOURCE
    assert result.overlaps == [(_QWEN, "gpqa-diamond")]


def test_a_benchmark_measured_on_two_scales_refuses_to_rank(small_catalog):
    upstream = [
        _upstream_score("qwen-3-5-9b", "gpqa-diamond", 0.61, _QWEN),
        _upstream_score("other", "gpqa-diamond", 61.0, "other/other", scale="index_0_100"),
    ]

    with pytest.raises(build.CohortScaleError, match="mixed scales"):
        build.build(upstream_scores=upstream, hf_report=SyncReport(), generated_at="2026-08-11")


def _score_row(benchmark: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "benchmark": benchmark,
        "skills": ["Reasoning"],
        "domains": ["Science"],
        "raw_score": 0.61,
        "scale": "accuracy_0_1",
        "percentile": 70.0,
        "cohort_n": 120,
        "source": lb.SOURCE,
        "source_url": f"https://arxiv.org/abs/{benchmark}",
        "observed_at": "2026-08-11",
        "provenance": str(Provenance.MEASURED),
    }
    row.update(overrides)
    return row


def _healthy(*, n_models: int | None = None, n_benchmarks: int | None = None) -> dict[str, Any]:
    n_models = build.MIN_MODELS if n_models is None else n_models
    n_benchmarks = build.MIN_BENCHMARKS if n_benchmarks is None else n_benchmarks
    keys = sorted(build._catalog())[:n_models]
    assert len(keys) == n_models, "models.json no longer holds enough entries for this gate"
    return {
        "generated_at": "2026-08-11",
        "parser_version": PARSER_VERSION,
        "models": {key: [_score_row(f"bench-{i}") for i in range(n_benchmarks)] for key in keys},
    }


def _empty_previous() -> Any:
    return parse_artifact({"generated_at": "", "parser_version": PARSER_VERSION, "models": {}})


def test_gates_pass_a_healthy_payload():
    build.check(_healthy(), previous=_empty_previous())


def test_too_few_models_refuses_the_run():
    payload = _healthy(n_models=build.MIN_MODELS - 1)

    with pytest.raises(build.TooFewModelsError, match="below the floor"):
        build.check(payload, previous=_empty_previous())


def test_too_few_benchmarks_refuses_the_run():
    payload = _healthy(n_benchmarks=build.MIN_BENCHMARKS - 1)

    with pytest.raises(build.TooFewBenchmarksError, match="below the floor"):
        build.check(payload, previous=_empty_previous())


def test_a_model_key_outside_the_catalog_refuses_the_run():
    payload = _healthy()
    payload["models"]["acme/not-a-model"] = [_score_row("bench-0")]

    with pytest.raises(build.UnknownModelKeyError, match="acme/not-a-model"):
        build.check(payload, previous=_empty_previous())


def test_an_illegal_provenance_refuses_the_run():
    payload = _healthy()
    first = next(iter(payload["models"]))
    payload["models"][first][0]["provenance"] = "vibes"

    with pytest.raises(build.ScoreValueError, match="vibes"):
        build.check(payload, previous=_empty_previous())


@pytest.mark.parametrize("percentile", [100.1, -0.1])
def test_a_percentile_outside_the_range_refuses_the_run(percentile: float):
    payload = _healthy()
    first = next(iter(payload["models"]))
    payload["models"][first][0]["percentile"] = percentile

    with pytest.raises(build.ScoreValueError, match="percentile"):
        build.check(payload, previous=_empty_previous())


def test_a_model_losing_most_of_its_benchmarks_refuses_the_run():
    payload = _healthy()
    first = next(iter(payload["models"]))
    payload["models"][first] = payload["models"][first][:1]
    previous = parse_artifact(_healthy())

    with pytest.raises(build.CoverageLossError, match=first):
        build.check(payload, previous=previous)


def test_a_model_holding_most_of_its_benchmarks_still_passes():
    kept = int(build.MIN_BENCHMARKS * 0.6)
    payload = _healthy()
    first = next(iter(payload["models"]))
    payload["models"][first] = payload["models"][first][:kept]

    build.check(payload, previous=parse_artifact(_healthy()))
