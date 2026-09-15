"""Dataset-shaped task types and the skill blend each one grades against.

``Skill`` and ``Domain`` values are the labels the upstream leaderboards tag their
benchmarks with — the committed artifact is keyed by those strings, so they cannot be
renamed for taste.
"""

from __future__ import annotations

from enum import StrEnum

from django.db import models


class TaskType(models.TextChoices):
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    SUMMARIZATION = "summarization"
    QUESTION_ANSWERING = "question_answering"
    CODE_GENERATION = "code_generation"
    TOOL_CALLING = "tool_calling"
    REASONING_MATH = "reasoning_math"
    TRANSLATION = "translation"
    CREATIVE_WRITING = "creative_writing"
    DIALOGUE = "dialogue"


class Skill(StrEnum):
    INSTRUCTION_FOLLOWING = "Instruction Following"
    TOOL_USE = "Tool Use"
    AGENTIC = "Agentic"
    REASONING = "Reasoning"
    CODING = "Coding"
    FAITHFULNESS = "Faithfulness"
    WRITING = "Writing"
    LONG_CONTEXT = "Long Context"
    MULTIMODAL = "Multimodal"
    USER_INTERACTION = "User Interaction"


class Domain(StrEnum):
    BUSINESS = "Business"
    FINANCE = "Finance"
    LEGAL = "Legal"
    MEDICAL = "Medical"
    SCIENCE = "Science"
    MATH = "Math"
    HUMANITIES = "Humanities"
    CODING = "Coding"
    MULTILINGUAL = "Multilingual"


# Keys are label strings, not ``Skill`` alone: reasoning_math blends the *domain*
# "Math", which has no skill equivalent.
SKILL_WEIGHTS: dict[TaskType, dict[str, float]] = {
    TaskType.CLASSIFICATION: {
        Skill.INSTRUCTION_FOLLOWING: 0.6,
        Skill.REASONING: 0.4,
    },
    TaskType.EXTRACTION: {
        Skill.INSTRUCTION_FOLLOWING: 0.5,
        Skill.FAITHFULNESS: 0.3,
        Skill.REASONING: 0.2,
    },
    TaskType.SUMMARIZATION: {
        Skill.FAITHFULNESS: 0.4,
        Skill.WRITING: 0.3,
        Skill.INSTRUCTION_FOLLOWING: 0.2,
        Skill.REASONING: 0.1,
    },
    TaskType.QUESTION_ANSWERING: {
        Skill.FAITHFULNESS: 0.4,
        Skill.REASONING: 0.3,
        Skill.INSTRUCTION_FOLLOWING: 0.3,
    },
    TaskType.CODE_GENERATION: {
        Skill.CODING: 0.6,
        Skill.REASONING: 0.2,
        Skill.INSTRUCTION_FOLLOWING: 0.2,
    },
    TaskType.TOOL_CALLING: {
        Skill.TOOL_USE: 0.5,
        Skill.AGENTIC: 0.3,
        Skill.INSTRUCTION_FOLLOWING: 0.2,
    },
    TaskType.REASONING_MATH: {
        Skill.REASONING: 0.6,
        Domain.MATH: 0.4,
    },
    TaskType.TRANSLATION: {
        Skill.WRITING: 0.4,
        Skill.FAITHFULNESS: 0.3,
        Skill.INSTRUCTION_FOLLOWING: 0.3,
    },
    TaskType.CREATIVE_WRITING: {
        Skill.WRITING: 0.8,
        Skill.INSTRUCTION_FOLLOWING: 0.2,
    },
    TaskType.DIALOGUE: {
        Skill.INSTRUCTION_FOLLOWING: 0.4,
        Skill.REASONING: 0.2,
        Skill.FAITHFULNESS: 0.2,
        Skill.WRITING: 0.2,
    },
}

# A long-context dataset takes this share of the blend for Long Context; the task's own
# weights keep their proportions inside the remainder.
LONG_CONTEXT_MODIFIER = 0.15


def weights_for(task_type: TaskType | str, *, long_context: bool = False) -> dict[str, float]:
    """Skill weights for a task, empty when the task type is unknown or blank."""
    base = SKILL_WEIGHTS.get(task_type)
    if not base:
        return {}
    if not long_context:
        return dict(base)
    blended = {skill: weight * (1.0 - LONG_CONTEXT_MODIFIER) for skill, weight in base.items()}
    blended[Skill.LONG_CONTEXT] = blended.get(Skill.LONG_CONTEXT, 0.0) + LONG_CONTEXT_MODIFIER
    return blended
