from __future__ import annotations

from typing import Any

import pytest

from overbae.services.benchmarks.classify import classify_task_type
from overbae.services.benchmarks.taxonomy import TaskType


def _profile(**overrides: Any) -> dict[str, Any]:
    profile = {
        "count": 40,
        "sampled": 40,
        "has_reference": True,
        "reference_ratio": 1.0,
        "output_kind": "free_text",
        "modality": "single_turn",
        "has_tool_calls": False,
        "avg_steps": 0.0,
        "max_steps": 0,
        "is_long": False,
    }
    profile.update(overrides)
    return profile


def _lengths(avg_in: float, avg_out: float) -> dict[str, Any]:
    return {"avg_input_chars": avg_in, "avg_output_chars": avg_out}


def test_tool_calls_in_profile_give_tool_calling():
    assert classify_task_type(_profile(has_tool_calls=True)) == TaskType.TOOL_CALLING.value


def test_tool_calling_modality_gives_tool_calling():
    profile = _profile(modality="tool_calling", has_tool_calls=False)
    assert classify_task_type(profile) == TaskType.TOOL_CALLING.value


def test_label_output_gives_classification():
    assert classify_task_type(_profile(output_kind="label")) == TaskType.CLASSIFICATION.value


def test_json_output_gives_extraction():
    assert classify_task_type(_profile(output_kind="json")) == TaskType.EXTRACTION.value


def test_multi_turn_modality_gives_dialogue():
    profile = _profile(modality="multi_turn", output_kind="free_text")
    assert classify_task_type(profile) == TaskType.DIALOGUE.value


def test_free_text_condensing_output_gives_summarization():
    result = classify_task_type(_profile(), _lengths(4000, 300))
    assert result == TaskType.SUMMARIZATION.value


def test_free_text_expanding_output_gives_creative_writing():
    result = classify_task_type(_profile(), _lengths(120, 2400))
    assert result == TaskType.CREATIVE_WRITING.value


def test_free_text_comparable_lengths_give_question_answering():
    result = classify_task_type(_profile(), _lengths(500, 550))
    assert result == TaskType.QUESTION_ANSWERING.value


@pytest.mark.parametrize(
    ("avg_in", "avg_out", "expected"),
    [
        (1000, 500, TaskType.SUMMARIZATION.value),
        (1000, 501, TaskType.QUESTION_ANSWERING.value),
        (500, 1000, TaskType.CREATIVE_WRITING.value),
        (500, 999, TaskType.QUESTION_ANSWERING.value),
    ],
)
def test_free_text_ratio_thresholds_are_inclusive(avg_in, avg_out, expected):
    assert classify_task_type(_profile(), _lengths(avg_in, avg_out)) == expected


def test_free_text_without_lengths_gives_question_answering():
    assert classify_task_type(_profile()) == TaskType.QUESTION_ANSWERING.value


@pytest.mark.parametrize(
    "stats",
    [
        {},
        {"avg_input_chars": 800},
        {"avg_input_chars": 800, "avg_output_chars": 0},
        {"avg_input_chars": "long", "avg_output_chars": "short"},
        {"avg_input_chars": None, "avg_output_chars": None},
    ],
)
def test_unusable_lengths_give_question_answering(stats):
    assert classify_task_type(_profile(), stats) == TaskType.QUESTION_ANSWERING.value


@pytest.mark.parametrize(
    "profile",
    [
        {},
        _profile(output_kind="unknown", modality="single_turn"),
        _profile(output_kind=None, modality=None, has_tool_calls=None),
        {"output_kind": "something_new", "modality": "something_new"},
    ],
)
def test_unknown_profile_still_resolves_to_a_legal_task_type(profile):
    assert classify_task_type(profile) in set(TaskType.values)


def test_tool_calling_outranks_output_kind():
    profile = _profile(has_tool_calls=True, output_kind="json", modality="multi_turn")
    assert classify_task_type(profile) == TaskType.TOOL_CALLING.value


def test_label_outranks_multi_turn_modality():
    profile = _profile(output_kind="label", modality="multi_turn")
    assert classify_task_type(profile) == TaskType.CLASSIFICATION.value


def test_multi_turn_outranks_free_text_lengths():
    profile = _profile(modality="multi_turn", output_kind="free_text")
    assert classify_task_type(profile, _lengths(4000, 300)) == TaskType.DIALOGUE.value
