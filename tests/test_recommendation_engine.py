from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from conftest import frozen_dataset
from django.test import override_settings
from django.utils import timezone

from overbae.api.serializers import FinetuningRecommendationResponseSerializer
from overbae.models import Dataset, DatasetContext, Project
from overbae.services.benchmarks.taxonomy import TaskType, weights_for
from overbae.services.recommendation import get_recommendation
from overbae.services.recommendation.constraints import eligible_models
from overbae.services.recommendation.ranking import DEFAULT_PICKS

_PACKAGE = Path(__file__).resolve().parents[1] / "overbae" / "services" / "recommendation"

_STATS = {
    "num_examples": 500,
    "avg_input_chars": 300,
    "avg_output_chars": 60,
    "has_tool_calling": False,
    "max_token_length": 200,
}


@pytest.fixture
def dataset(db) -> Dataset:
    project = Project.objects.create(name="Recs", slug=f"recs-{uuid.uuid4().hex[:8]}")
    dataset = frozen_dataset(
        project,
        [
            {
                "input": {"messages": [{"role": "user", "content": f"extract field {i}"}]},
                "expected_output": {"value": i},
            }
            for i in range(3)
        ],
    )
    _set_stats(dataset)
    return dataset


def _recommend(dataset: Dataset) -> dict[str, Any]:
    with override_settings(FINETUNING_BACKEND="baseten"):
        return get_recommendation(str(dataset.id))


def _set_stats(dataset: Dataset, **overrides: Any) -> None:
    version = dataset.active_cell
    version.stats = {**_STATS, **overrides}
    version.save(update_fields=["stats"])


def test_every_constraint_passing_model_is_returned_in_rank_order(dataset):
    analysis = _recommend(dataset)
    with override_settings(FINETUNING_BACKEND="baseten"):
        eligible, _exclusions = eligible_models(
            has_tool_calling=False, max_row_tokens=_STATS["max_token_length"]
        )

    expected = {entry["id"] for models in eligible.values() for entry in models}
    assert {row["model"] for row in analysis["candidates"]} == expected

    grades = [row["grade"] for row in analysis["candidates"]]
    graded = [grade for grade in grades if grade is not None]
    assert graded
    assert grades[len(graded) :] == [None] * (len(grades) - len(graded))

    # The displayed grade is the posterior mean; the order is a lower bound on it, so the
    # two need not agree row by row. What the order does promise is the evidence tier.
    self_reported = [
        all(item["provenance"] != "measured" for item in row["evidence"])
        for row in analysis["candidates"][: len(graded)]
    ]
    assert any(self_reported)
    assert self_reported == sorted(self_reported)


def test_excluded_names_every_dropped_model_and_the_reason(dataset):
    _set_stats(dataset, has_tool_calling=True, max_token_length=50_000)

    analysis = _recommend(dataset)

    excluded = {row["model"]: row["reason"] for row in analysis["excluded"]}
    assert len(excluded) == len(analysis["excluded"])
    assert all(reason.strip() for reason in excluded.values())
    assert not excluded.keys() & {row["model"] for row in analysis["candidates"]}
    assert analysis["candidates"]


def test_the_wizard_opens_on_the_default_picks_with_only_the_best_one_checked(dataset):
    analysis = _recommend(dataset)

    assert len(analysis["candidates"]) > DEFAULT_PICKS
    assert len(analysis["shown"]) == DEFAULT_PICKS
    assert [row["model"] for row in analysis["candidates"] if row["selected"]] == [
        analysis["shown"][0]
    ]


def test_the_selection_shrinks_to_the_models_that_exist(dataset, monkeypatch):
    catalog = {
        "small": [
            {
                "id": f"vendor/tiny-{i}",
                "display": f"Tiny {i}",
                "params": "1B",
                "total_params_b": 1.0 + i,
                "context_length_sft": 8192,
                "max_batch_size": 8,
                "min_batch_size": 1,
                "supports_tool_calling": True,
                "training_type": {"lora": {"enabled": True}, "full": {"enabled": False}},
            }
            for i in range(2)
        ]
    }
    monkeypatch.setattr(
        "overbae.services.recommendation.constraints.tier_models", lambda **_kwargs: catalog
    )

    analysis = _recommend(dataset)

    assert [row["model"] for row in analysis["candidates"]] == ["vendor/tiny-0", "vendor/tiny-1"]
    assert analysis["shown"] == ["vendor/tiny-0", "vendor/tiny-1"]


def test_the_headline_scores_a_model_against_the_field_the_user_can_train(dataset):
    analysis = _recommend(dataset)
    graded = [row for row in analysis["candidates"] if row["match"] is not None]

    assert graded
    top = graded[0]
    assert top["match"] >= 90
    assert top["match_rank"] == 1
    assert top["match_pool"] == len(graded)
    assert top["match_pool"] < len(analysis["candidates"])
    ranks = [row["match_rank"] for row in graded]
    first_at: dict[int, int] = {}
    for i, rank in enumerate(ranks):
        first_at.setdefault(rank, i)
        assert rank == first_at[rank] + 1
    assert (top["grade"], top["adjusted_grade"], top["lower_bound"]) != (None, None, None)


def test_every_skill_number_is_read_against_the_field_the_fit_is_read_against(dataset):
    analysis = _recommend(dataset)
    graded = [row for row in analysis["candidates"] if row["match"] is not None]

    assert graded
    for row in graded:
        assert row["skill_scores"]
        weights = [skill["weight"] for skill in row["skill_scores"]]
        assert weights == sorted(weights, reverse=True)
        for skill in row["skill_scores"]:
            assert skill["skill"] in analysis["skill_weights"]
            assert 1 <= skill["rank_in_field"] <= skill["field_n"] <= row["match_pool"]
            assert 0.0 <= skill["percentile_in_field"] <= 100.0
            assert 0.0 <= skill["percentile_global"] <= 100.0


def test_a_model_no_benchmark_covers_is_still_offered(dataset):
    ungraded = [row for row in _recommend(dataset)["candidates"] if row["grade"] is None]

    assert ungraded, "the catalog carries models no benchmark covers"
    for row in ungraded:
        assert (row["match"], row["match_rank"]) == (None, None)
        assert row["confidence"] == "none"
        assert row["n_benchmarks"] == 0
        assert row["evidence"] == []
        assert row["skill_scores"] == []
        assert row["hyperparams"]["n_epochs"] >= 1


def test_the_evidence_matches_the_grade_it_explains(dataset):
    analysis = _recommend(dataset)
    graded = [row for row in analysis["candidates"] if row["grade"] is not None]

    assert graded
    for row in graded:
        assert len(row["evidence"]) == row["n_benchmarks"]
        assert row["confidence"] in {"high", "medium", "low"}
        for evidence in row["evidence"]:
            assert 0.0 <= evidence["percentile"] <= 100.0
            assert "raw_score" not in evidence
            assert evidence["skill"] in analysis["skill_weights"]
        self_reported = [item["provenance"] != "measured" for item in row["evidence"]]
        assert self_reported == sorted(self_reported)


def test_the_payload_states_the_dataset_and_the_snapshot_behind_the_grades(dataset):
    analysis = _recommend(dataset)

    facts = analysis["dataset"]
    assert facts["rows"] == _STATS["num_examples"]
    assert facts["max_token_length"] == _STATS["max_token_length"]
    assert facts["has_tool_calling"] is False
    assert facts["total_tokens"] > 0
    assert analysis["benchmark_snapshot"]["generated_at"]


def test_the_task_type_comes_from_the_stored_context(dataset):
    DatasetContext.objects.create(
        dataset=dataset,
        project=dataset.project,
        profile={"output_kind": "json"},
        task_type=TaskType.SUMMARIZATION,
        task_type_source="semantic",
        extracted_at=timezone.now(),
    )

    analysis = _recommend(dataset)

    assert analysis["task_type"] == TaskType.SUMMARIZATION.value
    assert analysis["task_type_source"] == "semantic"
    assert analysis["skill_weights"] == {
        str(skill): weight for skill, weight in weights_for(TaskType.SUMMARIZATION).items()
    }


def test_a_context_without_a_task_type_falls_back_to_its_profile(dataset):
    DatasetContext.objects.create(
        dataset=dataset,
        project=dataset.project,
        profile={"output_kind": "label", "modality": "single_turn"},
        extracted_at=timezone.now(),
    )

    analysis = _recommend(dataset)

    assert analysis["task_type"] == TaskType.CLASSIFICATION.value
    assert analysis["task_type_source"] == "heuristic"


def test_a_dataset_with_no_context_is_classified_from_its_rows(dataset):
    assert not DatasetContext.objects.filter(dataset=dataset).exists()

    analysis = _recommend(dataset)

    assert analysis["task_type"] == TaskType.EXTRACTION.value
    assert analysis["task_type_source"] == "heuristic"


def test_recommending_never_calls_an_llm(dataset):
    with mock.patch("overbae.core.llms.call_llm", side_effect=AssertionError("LLM call")):
        analysis = _recommend(dataset)

    assert analysis["candidates"]


def test_no_module_in_the_package_imports_an_llm():
    imported = {module: _imported_names(path) for module, path in _package_modules().items()}

    assert "overbae.services.benchmarks.artifact" in imported["analysis"]
    assert {
        module: sorted(names)
        for module, names in imported.items()
        if any(name.startswith("overbae.core.llms") for name in names)
    } == {}


def test_the_payload_matches_the_published_contract(dataset):
    serializer = FinetuningRecommendationResponseSerializer(data=_recommend(dataset))

    assert serializer.is_valid(), serializer.errors


def _package_modules() -> dict[str, Path]:
    return {path.stem: path for path in sorted(_PACKAGE.glob("*.py"))}


def _imported_names(path: Path) -> set[str]:
    """Absolute module and symbol names imported anywhere in *path*, lazy imports included."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level:
            prefix = node.module or ""
            names.add(prefix)
            names |= {f"{prefix}.{alias.name}" for alias in node.names}
    return names
