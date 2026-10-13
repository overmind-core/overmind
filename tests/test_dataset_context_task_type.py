from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import frozen_dataset

from overbae.models import Capability, Dataset, DatasetContext, Project
from overbae.services.benchmarks.taxonomy import TaskType
from overbae.services.datasets import profiler
from overbae.services.recommendation import get_recommendation

pytestmark = pytest.mark.django_db

_LONG_PARAGRAPH = (
    "The quarterly filing restates revenue across every regional segment and "
    "reconciles the deferred balance carried since the prior audit. "
) * 8


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
        contract="eval",
    )


def _boom(*args, **kwargs):
    raise RuntimeError("stats unavailable")


@pytest.fixture
def label_dataset() -> Dataset:
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


def test_labelled_outputs_give_classification(label_dataset):
    ctx = profiler.extract_and_save(label_dataset)

    assert ctx.task_type == TaskType.CLASSIFICATION.value
    assert ctx.task_type_source == "heuristic"


def test_heuristic_task_type_follows_the_dataset_profile(label_dataset):
    label_dataset = _dataset_of(
        [("summarize this report", '{"queue": "billing", "priority": 2}')] * 4
    )

    ctx = profiler.extract_and_save(label_dataset)

    assert ctx.profile["output_kind"] == "json"
    assert ctx.task_type == TaskType.EXTRACTION.value
    assert ctx.task_type_source == "heuristic"


def test_condensing_free_text_gives_summarization(condensing_dataset):

    ctx = profiler.extract_and_save(condensing_dataset)

    assert ctx.profile["output_kind"] == "free_text"
    assert ctx.task_type == TaskType.SUMMARIZATION.value
    assert ctx.task_type_source == "heuristic"


def test_expanding_free_text_gives_creative_writing(expanding_dataset):

    ctx = profiler.extract_and_save(expanding_dataset)

    assert ctx.profile["output_kind"] == "free_text"
    assert ctx.task_type == TaskType.CREATIVE_WRITING.value
    assert ctx.task_type_source == "heuristic"


def test_cached_stats_are_used_without_recomputing(condensing_dataset):
    version = condensing_dataset.active_cell
    version.stats = {"avg_input_chars": 100, "avg_output_chars": 900}
    version.save(update_fields=["stats"])

    ctx = profiler.extract_and_save(condensing_dataset)

    assert ctx.task_type == TaskType.CREATIVE_WRITING.value


def test_task_type_is_always_a_legal_taxonomy_value(label_dataset):

    ctx = profiler.extract_and_save(label_dataset)

    assert ctx.task_type in set(TaskType.values)
    assert ctx.task_type_source == "heuristic"


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
