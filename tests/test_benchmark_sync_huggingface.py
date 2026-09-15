from __future__ import annotations

from typing import Any

import pytest

from overbae.services.benchmarks.schema import Provenance
from overbae.services.benchmarks.sync import huggingface as hf


def _entry(
    dataset_id: str,
    task_id: str,
    value: Any,
    *,
    source_name: str | None = None,
    source_url: str = "",
    date: str = "2026-06-25",
    verified: bool | None = False,
) -> dict[str, Any]:
    source: dict[str, Any] = {"url": source_url}
    if source_name is not None:
        source["name"] = source_name
    return {
        "verified": verified,
        "data": {
            "dataset": {"id": dataset_id, "task_id": task_id},
            "value": value,
            "date": date,
            "source": source,
        },
    }


def _fetcher(payloads: dict[str, Any]):
    def fetch(model_id: str) -> dict[str, Any]:
        payload = payloads[model_id]
        if isinstance(payload, Exception):
            raise payload
        return payload

    return fetch


def test_maps_dataset_id_to_canonical_benchmark():
    report = hf.collect(
        ["Qwen/Qwen3.5-9B"],
        fetcher=_fetcher(
            {"Qwen/Qwen3.5-9B": {"evalResults": [_entry("Idavidrein/gpqa", "diamond", 81.7)]}}
        ),
    )

    (row,) = report.rows
    assert row["eval_slug"] == "gpqa-diamond"
    assert row["skills"] == ("Reasoning",)
    assert row["domains"] == ("Science",)
    assert row["scale"] == "accuracy_0_1"
    assert row["raw_score"] == pytest.approx(0.817)
    assert row["observed_at"] == "2026-06-25"


def test_percent_scores_normalise_onto_the_shared_scale():
    report = hf.collect(
        ["m"],
        fetcher=_fetcher({"m": {"evalResults": [_entry("TIGER-Lab/MMLU-Pro", "mmlu_pro", 20.25)]}}),
    )

    assert report.rows[0]["raw_score"] == pytest.approx(0.2025)


def test_unmapped_dataset_is_skipped_and_reported():
    report = hf.collect(
        ["m"],
        fetcher=_fetcher(
            {"m": {"evalResults": [_entry("thamilvendhan/signalbench", "src", 0.38)]}}
        ),
    )

    assert report.rows == []
    assert report.unmapped_datasets == {"thamilvendhan/signalbench": ["m"]}


def test_unused_subtask_of_a_known_dataset_reports_separately():
    report = hf.collect(
        ["m"],
        fetcher=_fetcher({"m": {"evalResults": [_entry("llamaindex/ParseBench", "table", 9.9)]}}),
    )

    assert report.rows == []
    assert report.unmapped_datasets == {}
    assert report.unused_subtasks == {"llamaindex/ParseBench#table": 1}


def test_model_card_result_is_lab_claimed():
    report = hf.collect(
        ["Qwen/Qwen3.6-27B"],
        fetcher=_fetcher(
            {
                "Qwen/Qwen3.6-27B": {
                    "evalResults": [
                        _entry(
                            "Idavidrein/gpqa",
                            "diamond",
                            87.8,
                            source_name="Model Card",
                            source_url="https://huggingface.co/Qwen/Qwen3.6-27B",
                        )
                    ]
                }
            }
        ),
    )

    assert report.rows[0]["provenance"] == Provenance.LAB_CLAIMED


def test_lab_domain_result_about_its_own_model_is_lab_claimed():
    report = hf.collect(
        ["LiquidAI/LFM2.5-350M"],
        fetcher=_fetcher(
            {
                "LiquidAI/LFM2.5-350M": {
                    "evalResults": [
                        _entry(
                            "LiquidAI/ifstruct-v1.0",
                            "ifstruct_v1",
                            44.9,
                            source_name="Liquid AI — IFStruct v1.0 blog",
                            source_url="https://www.liquid.ai/blog/ifstruct-v1.0",
                        )
                    ]
                }
            }
        ),
    )

    assert report.rows[0]["provenance"] == Provenance.LAB_CLAIMED


def test_third_party_run_is_measured():
    report = hf.collect(
        ["Qwen/Qwen3-8B"],
        fetcher=_fetcher(
            {
                "Qwen/Qwen3-8B": {
                    "evalResults": [
                        _entry(
                            "LiquidAI/ifstruct-v1.0",
                            "ifstruct_v1",
                            79.75,
                            source_name="Liquid AI — IFStruct v1.0 blog (Qwen3-8B)",
                            source_url="https://www.liquid.ai/blog/ifstruct-v1.0",
                        )
                    ]
                }
            }
        ),
    )

    assert report.rows[0]["provenance"] == Provenance.MEASURED


def test_verified_result_is_measured_despite_a_model_card_source():
    report = hf.collect(
        ["Qwen/Qwen3.6-27B"],
        fetcher=_fetcher(
            {
                "Qwen/Qwen3.6-27B": {
                    "evalResults": [
                        _entry(
                            "Idavidrein/gpqa",
                            "diamond",
                            87.8,
                            source_name="Model Card",
                            source_url="https://huggingface.co/Qwen/Qwen3.6-27B",
                            verified=True,
                        )
                    ]
                }
            }
        ),
    )

    assert report.rows[0]["provenance"] == Provenance.MEASURED


def test_entry_the_hub_could_not_parse_keeps_the_rest_of_the_model():
    report = hf.collect(
        ["m"],
        fetcher=_fetcher(
            {
                "m": {
                    "evalResults": [
                        {"filename": "mmlu-pro.yaml", "error": "Invalid input"},
                        _entry("Idavidrein/gpqa", "diamond", 30.4),
                    ]
                }
            }
        ),
    )

    assert [row["eval_slug"] for row in report.rows] == ["gpqa-diamond"]
    assert report.client_parse_failures == ["m"]


def test_duplicate_benchmark_keeps_the_measured_row():
    report = hf.collect(
        ["Qwen/Qwen3.5-27B"],
        fetcher=_fetcher(
            {
                "Qwen/Qwen3.5-27B": {
                    "evalResults": [
                        _entry(
                            "Idavidrein/gpqa",
                            "diamond",
                            85.5,
                            source_name="Model Card",
                            source_url="https://huggingface.co/Qwen/Qwen3.5-27B",
                        ),
                        _entry(
                            "Idavidrein/gpqa",
                            "diamond",
                            81.8,
                            source_name="EvalEval",
                            source_url="https://huggingface.co/datasets/evaleval/EEE_datastore",
                        ),
                    ]
                }
            }
        ),
    )

    (row,) = report.rows
    assert row["provenance"] == Provenance.MEASURED
    assert row["raw_score"] == pytest.approx(0.818)
    assert report.duplicates_dropped == 1


def test_duplicate_lab_claimed_rows_keep_the_newest_then_the_lower_score():
    report = hf.collect(
        ["LiquidAI/LFM2.5-350M"],
        fetcher=_fetcher(
            {
                "LiquidAI/LFM2.5-350M": {
                    "evalResults": [
                        _entry(
                            "LiquidAI/ifstruct-v1.0",
                            "ifstruct_v1",
                            44.9,
                            source_name="Model Card",
                            date="2026-06-30",
                        ),
                        _entry(
                            "LiquidAI/ifstruct-v1.0",
                            "ifstruct_v1",
                            21.1,
                            source_name="Model Card",
                            date="2026-06-30",
                        ),
                        _entry(
                            "LiquidAI/ifstruct-v1.0",
                            "ifstruct_v1",
                            15.0,
                            source_name="Model Card",
                            date="2026-01-01",
                        ),
                    ]
                }
            }
        ),
    )

    (row,) = report.rows
    assert row["raw_score"] == pytest.approx(0.211)
    assert report.duplicates_dropped == 2


def test_one_failed_model_does_not_stop_the_run():
    report = hf.collect(
        ["broken", "fine"],
        fetcher=_fetcher(
            {
                "broken": RuntimeError("401 Unauthorized"),
                "fine": {"evalResults": [_entry("Idavidrein/gpqa", "diamond", 30.4)]},
            }
        ),
    )

    assert [row["hf_model_id"] for row in report.rows] == ["fine"]
    assert "401 Unauthorized" in report.fetch_failures["broken"]


def test_model_without_results_is_reported_not_dropped_silently():
    report = hf.collect(["m"], fetcher=_fetcher({"m": {"evalResults": []}}))

    assert report.rows == []
    assert report.models_without_results == ["m"]


def test_non_numeric_value_is_counted_as_malformed():
    report = hf.collect(
        ["m"],
        fetcher=_fetcher({"m": {"evalResults": [_entry("Idavidrein/gpqa", "diamond", "n/a")]}}),
    )

    assert report.rows == []
    assert report.malformed_entries == 1


def test_required_benchmarks_are_mapped():
    expected = {
        "gpqa-diamond",
        "mmlu-pro",
        "ifstruct",
        "parsebench",
        "aime-2026",
        "hmmt-feb-2026",
        "swe-bench-verified",
        "humanitys-last-exam",
        "mmmu-pro-vision",
        "screenspot-pro",
    }

    assert expected <= {benchmark.slug for benchmark in hf.BENCHMARKS.values()}


def test_every_mapped_benchmark_carries_a_skill_or_domain():
    for key, benchmark in hf.BENCHMARKS.items():
        assert benchmark.skills or benchmark.domains, key
