from __future__ import annotations

import pytest

from overbae.services.benchmarks.taxonomy import (
    LONG_CONTEXT_MODIFIER,
    SKILL_WEIGHTS,
    Domain,
    Skill,
    TaskType,
    weights_for,
)

_LEGAL_AXES = {str(skill) for skill in Skill} | {str(domain) for domain in Domain}

_TASK_TYPES = list(TaskType)


@pytest.mark.parametrize("task_type", _TASK_TYPES)
def test_every_task_type_carries_a_skill_blend(task_type: TaskType):
    assert SKILL_WEIGHTS.get(task_type), f"{task_type.value} grades against nothing"


def test_no_weights_for_unknown_task_types():
    assert set(SKILL_WEIGHTS) == set(_TASK_TYPES)


@pytest.mark.parametrize("task_type", _TASK_TYPES)
def test_weights_sum_to_one(task_type: TaskType):
    assert sum(SKILL_WEIGHTS[task_type].values()) == pytest.approx(1.0)


@pytest.mark.parametrize("task_type", _TASK_TYPES)
def test_weights_are_positive(task_type: TaskType):
    assert all(weight > 0 for weight in SKILL_WEIGHTS[task_type].values())


@pytest.mark.parametrize("task_type", _TASK_TYPES)
def test_weight_keys_are_legal_skill_or_domain_labels(task_type: TaskType):
    unknown = {str(axis) for axis in SKILL_WEIGHTS[task_type]} - _LEGAL_AXES
    assert not unknown, f"{task_type.value} references unknown axes {sorted(unknown)}"


@pytest.mark.parametrize("task_type", _TASK_TYPES)
def test_long_context_blend_still_sums_to_one(task_type: TaskType):
    blended = weights_for(task_type, long_context=True)
    assert sum(blended.values()) == pytest.approx(1.0)
    assert blended[Skill.LONG_CONTEXT] == pytest.approx(LONG_CONTEXT_MODIFIER)


def test_long_context_keeps_the_base_proportions():
    base = weights_for(TaskType.EXTRACTION)
    blended = weights_for(TaskType.EXTRACTION, long_context=True)
    ratio = blended[Skill.INSTRUCTION_FOLLOWING] / blended[Skill.FAITHFULNESS]
    assert ratio == pytest.approx(base[Skill.INSTRUCTION_FOLLOWING] / base[Skill.FAITHFULNESS])


@pytest.mark.parametrize("task_type", ["", "instruction_following", "chat"])
def test_unknown_task_type_has_no_weights(task_type: str):
    assert weights_for(task_type) == {}
    assert weights_for(task_type, long_context=True) == {}
