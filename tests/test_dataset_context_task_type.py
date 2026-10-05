from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import frozen_dataset

from overbae.models import Capability, Dataset, DatasetContext, Project
from overbae.services.benchmarks.taxonomy import TaskType
from overbae.services.datasets import rows as row_store
from overbae.services.eval import context_extractor, profiler
from overbae.services.recommendation import get_recommendation

pytestmark = pytest.mark.django_db

_LONG_PARAGRAPH = (
    "The quarterly filing restates revenue across every regional segment and "
    "reconciles the deferred balance carried since the prior audit. "
) * 8


@pytest.fixture(autouse=True)
def _rubric_authoring_unavailable(fake_llm):
    fake_llm.fail(lambda r: r.schema_name != "_SemanticAnalysis", 400, "no network in tests")


def _semantic(fake_llm, payload: dict[str, Any]) -> None:
    if not payload.get("available"):
        fake_llm.fail(lambda r: True, 400, payload.get("error", "provider down"))
        return
    fields = {k: payload[k] for k in ("domain", "task_description", "task_type")}
    fake_llm.on_json(lambda r: r.schema_name == "_SemanticAnalysis", lambda r: fields)


def _available(task_type: str) -> dict[str, Any]:
    return {
        "available": True,
        "domain": "customer support",
        "task_description": "Route an inbound ticket to a queue.",
        "task_type": task_type,
        "evaluator_scores": {},
        "suggested_rubrics": [],
    }


def _dataset_of(rows: list[tuple[str, str]]) -> Dataset:
    suffix = uuid.uuid4().hex[:8]
    project = Project.objects.create(name="P", slug=f"p-{suffix}")
    capability = Capability.objects.create(project=project, name="A", slug=f"a-{suffix}")
    return frozen_dataset(
        project,
        [
            {"input": [{"role": "user", "content": prompt}], "expected_output": expected}
            for prompt, expected in rows
        ],
        capability=capability,
    )


def _boom(*args, **kwargs):
    raise RuntimeError("stats unavailable")


@pytest.fixture
def label_dataset() -> Dataset:
    """Labelled expected outputs, so the heuristic branch resolves to
    ``classification`` and any semantic value is visibly different."""
    return _dataset_of([(f"ticket {i}", "billing" if i % 2 else "shipping") for i in range(8)])


@pytest.fixture
def condensing_dataset() -> Dataset:
    return _dataset_of(
        [
            (
                f"{_LONG_PARAGRAPH} Filing {i}.",
                f"Revenue restated across every region; deferred balance cleared in quarter {i}.",
            )
            for i in range(8)
        ]
    )


@pytest.fixture
def expanding_dataset() -> Dataset:
    return _dataset_of(
        [
            (
                f"Write a short story about a lighthouse keeper, number {i}.",
                f"{_LONG_PARAGRAPH} Chapter {i}.",
            )
            for i in range(8)
        ]
    )


def test_valid_semantic_task_type_is_stored_as_semantic(fake_llm, label_dataset):
    _semantic(fake_llm, _available(TaskType.SUMMARIZATION.value))

    ctx = context_extractor.extract_and_save(label_dataset)

    assert ctx.task_type == TaskType.SUMMARIZATION.value
    assert ctx.task_type_source == "semantic"


def test_semantic_task_type_is_normalized_before_matching(fake_llm, label_dataset):
    _semantic(fake_llm, _available("  Summarization "))

    ctx = context_extractor.extract_and_save(label_dataset)

    assert ctx.task_type == TaskType.SUMMARIZATION.value
    assert ctx.task_type_source == "semantic"


@pytest.mark.parametrize("candidate", ["chat", "instruction_following", "", "  ", "None"])
def test_invalid_semantic_task_type_falls_back_to_heuristic(fake_llm, label_dataset, candidate):
    _semantic(fake_llm, _available(candidate))

    ctx = context_extractor.extract_and_save(label_dataset)

    assert ctx.task_type == TaskType.CLASSIFICATION.value
    assert ctx.task_type_source == "heuristic"


def test_unavailable_semantic_analysis_falls_back_to_heuristic(fake_llm, label_dataset):
    _semantic(fake_llm, {"available": False, "error": "provider down"})

    ctx = context_extractor.extract_and_save(label_dataset)

    assert ctx.task_type == TaskType.CLASSIFICATION.value
    assert ctx.task_type_source == "heuristic"
    assert "provider down" in ctx.notes


def test_heuristic_task_type_follows_the_dataset_profile(fake_llm, label_dataset):
    _semantic(fake_llm, {"available": False})
    label_dataset = _dataset_of(
        [("summarize this report", '{"queue": "billing", "priority": 2}')] * 4
    )

    ctx = context_extractor.extract_and_save(label_dataset)

    assert ctx.profile["output_kind"] == "json"
    assert ctx.task_type == TaskType.EXTRACTION.value
    assert ctx.task_type_source == "heuristic"


def test_condensing_free_text_gives_summarization_without_semantic(fake_llm, condensing_dataset):
    _semantic(fake_llm, {"available": False})

    ctx = context_extractor.extract_and_save(condensing_dataset)

    assert ctx.profile["output_kind"] == "free_text"
    assert ctx.task_type == TaskType.SUMMARIZATION.value
    assert ctx.task_type_source == "heuristic"


def test_expanding_free_text_gives_creative_writing_without_semantic(fake_llm, expanding_dataset):
    _semantic(fake_llm, {"available": False})

    ctx = context_extractor.extract_and_save(expanding_dataset)

    assert ctx.profile["output_kind"] == "free_text"
    assert ctx.task_type == TaskType.CREATIVE_WRITING.value
    assert ctx.task_type_source == "heuristic"


def test_cached_stats_are_used_without_recomputing(fake_llm, condensing_dataset):
    _semantic(fake_llm, {"available": False})
    version = condensing_dataset.active_cell
    version.stats = {"avg_input_chars": 100, "avg_output_chars": 900}
    version.save(update_fields=["stats"])

    ctx = context_extractor.extract_and_save(condensing_dataset)

    assert ctx.task_type == TaskType.CREATIVE_WRITING.value


def test_unavailable_stats_still_give_a_legal_task_type(monkeypatch, condensing_dataset, fake_llm):
    _semantic(fake_llm, {"available": False})
    monkeypatch.setattr(row_store, "dataset_stats", _boom)

    ctx = context_extractor.extract_and_save(condensing_dataset)

    assert ctx.task_type == TaskType.QUESTION_ANSWERING.value
    assert ctx.task_type_source == "heuristic"


def test_task_type_is_always_a_legal_taxonomy_value(fake_llm, label_dataset):
    _semantic(fake_llm, {"available": False})

    ctx = context_extractor.extract_and_save(label_dataset)

    assert ctx.task_type in set(TaskType.values)
    assert ctx.task_type_source in {"semantic", "heuristic"}


@pytest.fixture
def extraction_dataset() -> Dataset:
    return _dataset_of(
        [
            (
                f"OCR dump for receipt {i}: CAFE MERIDIAN, total EUR {12 + i}.40",
                json.dumps(
                    {"merchant": "Cafe Meridian", "total_amount": 12 + i, "currency": "EUR"}
                ),
            )
            for i in range(8)
        ]
    )


def _save_context(dataset: Dataset, **fields: Any) -> DatasetContext:
    return DatasetContext.objects.create(
        dataset=dataset,
        project=dataset.project,
        extracted_at=datetime.now(UTC),
        **fields,
    )


def test_stored_context_without_classifier_fields_is_reprofiled(extraction_dataset):
    _save_context(extraction_dataset, profile={}, task_type="", task_type_source="")

    analysis = get_recommendation(str(extraction_dataset.id))

    assert analysis["task_type"] == TaskType.EXTRACTION.value
    assert analysis["task_type_source"] == "heuristic"


def test_stored_task_type_is_used_without_reprofiling(monkeypatch, extraction_dataset):
    profiled: list[Any] = []
    monkeypatch.setattr(profiler, "profile_dataset", lambda ds, **kw: profiled.append(ds) or {})
    _save_context(
        extraction_dataset,
        profile={},
        task_type=TaskType.SUMMARIZATION.value,
        task_type_source="semantic",
    )

    analysis = get_recommendation(str(extraction_dataset.id))

    assert analysis["task_type"] == TaskType.SUMMARIZATION.value
    assert analysis["task_type_source"] == "semantic"
    assert profiled == []


def test_failed_reprofiling_still_gives_a_legal_task_type(monkeypatch, extraction_dataset):
    monkeypatch.setattr(profiler, "profile_dataset", _boom)
    _save_context(extraction_dataset, profile={}, task_type="", task_type_source="")

    analysis = get_recommendation(str(extraction_dataset.id))

    assert analysis["task_type"] in set(TaskType.values)
    assert analysis["task_type_source"] == "heuristic"
