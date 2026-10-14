"""Decision rows: a System One request in ``input`` and its answers in ``expected_output``.

``input`` is ``{"state": str, "questions": {key: question}}`` and ``expected_output``
maps every question key to an answer: one option, or ``{option: weight}``. A question
is ``choice`` (``criteria`` maps option keys to descriptions), ``noul`` (options
``true`` and ``false``) or ``score`` (``criteria`` lists levels; options are the level
indexes ``"0"``, ``"1"``, …). The rules match what the decision trainer and server
accept, so a row that passes here trains and scores.
"""

from __future__ import annotations

import contextlib
import json
import math
from collections import Counter
from typing import Any

QUESTION_TYPES = ("choice", "noul", "score")
MAX_QUESTIONS = 100
MAX_OPTIONS = 255


class DecisionRowError(ValueError):
    pass


def _as_obj(value: Any) -> Any:
    if isinstance(value, str) and value.strip()[:1] in ("{", "["):
        with contextlib.suppress(ValueError):
            return json.loads(value)
    return value


def is_request(value: Any) -> bool:
    """Whether a value has the shape of a decision request, valid or not."""
    value = _as_obj(value)
    return isinstance(value, dict) and set(value) == {"state", "questions"}


def options(question: dict) -> list[str]:
    if question["type"] == "noul":
        return ["true", "false"]
    if question["type"] == "choice":
        return [str(key) for key in question["criteria"]]
    return [str(index) for index in range(len(question["criteria"]))]


def _question(key: str, question: Any) -> dict:
    if not isinstance(question, dict) or question.get("type") not in QUESTION_TYPES:
        raise DecisionRowError(f"question {key}: type must be choice, noul or score")
    instructions = question.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise DecisionRowError(f"question {key}: instructions must be text")
    if set(question) - {"type", "instructions", "criteria"}:
        raise DecisionRowError(f"question {key}: only type, instructions and criteria are allowed")
    kind, criteria = question["type"], question.get("criteria")
    if kind == "choice":
        if (
            not isinstance(criteria, dict)
            or not 2 <= len(criteria) <= MAX_OPTIONS
            or any(not str(k).strip() or not isinstance(v, str) for k, v in criteria.items())
        ):
            raise DecisionRowError(
                f"question {key}: choice criteria must be an object mapping 2–{MAX_OPTIONS} "
                'option keys to descriptions, e.g. {"refund": "...", "other": "..."}'
            )
    elif kind == "score":
        if (
            not isinstance(criteria, list)
            or not 2 <= len(criteria) <= MAX_OPTIONS
            or any(not isinstance(level, str) or not level.strip() for level in criteria)
        ):
            raise DecisionRowError(
                f"question {key}: score criteria must be a list of 2–{MAX_OPTIONS} level "
                'descriptions, e.g. ["poor", "fair", "good"]'
            )
    elif criteria is not None and (
        not isinstance(criteria, dict)
        or set(criteria) - {"true", "false"}
        or any(not isinstance(v, str) for v in criteria.values())
    ):
        raise DecisionRowError(
            f'question {key}: noul criteria may only describe "true" and "false"'
        )
    return question


def request(value: Any) -> dict:
    """A valid ``{state, questions}`` request, or ``DecisionRowError``."""
    value = _as_obj(value)
    if not is_request(value):
        raise DecisionRowError("input must be an object with only state and questions")
    if not isinstance(value["state"], str):
        raise DecisionRowError("state must be text")
    questions = value["questions"]
    if not isinstance(questions, dict) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise DecisionRowError(
            "questions must be an object keyed by question name, e.g. "
            '{"sentiment": {"type": "noul", "instructions": "..."}}, '
            f"with 1–{MAX_QUESTIONS} questions"
        )
    for key, question in questions.items():
        if not str(key).strip():
            raise DecisionRowError("question keys must not be empty")
        _question(key, question)
    return value


def target(question: dict, answer: Any) -> list[float]:
    """The answer as a probability per option, in ``options(question)`` order."""
    keys = options(question)
    answer = _as_obj(answer)
    if isinstance(answer, bool):
        answer = "true" if answer else "false"
    if isinstance(answer, int) and question["type"] == "score":
        answer = str(answer)
    if isinstance(answer, str):
        if answer not in keys and question["type"] == "score":
            raise DecisionRowError(
                f"answer {answer!r} is not a level; score answers are level indexes "
                f"0–{len(keys) - 1}"
            )
        if answer not in keys:
            raise DecisionRowError(f"answer {answer!r} is not one of {', '.join(keys)}")
        return [1.0 if key == answer else 0.0 for key in keys]
    if isinstance(answer, dict) and answer:
        if set(answer) - set(keys):
            raise DecisionRowError(f"answer has options outside {', '.join(keys)}")
        weights = [answer.get(key, 0) for key in keys]
        if any(
            isinstance(w, bool) or not isinstance(w, (int, float)) or not math.isfinite(w) or w < 0
            for w in weights
        ):
            raise DecisionRowError("answer weights must be finite and not negative")
        total = math.fsum(weights)
        if total <= 0:
            raise DecisionRowError("answer weights must not all be zero")
        return [w / total for w in weights]
    raise DecisionRowError("each answer must be one option or an object of option weights")


def answers(questions: dict, value: Any) -> dict[str, list[float]]:
    value = _as_obj(value)
    if not isinstance(value, dict):
        raise DecisionRowError("expected_output must map each question to its answer")
    missing = [key for key in questions if key not in value]
    if missing:
        raise DecisionRowError(f"no answer for {', '.join(missing[:3])}")
    extra = [key for key in value if key not in questions]
    if extra:
        raise DecisionRowError(f"answers for unknown questions: {', '.join(extra[:3])}")
    return {key: target(questions[key], value[key]) for key in questions}


def record(input_value: Any, expected: Any) -> dict:
    """One trainer record: ``{state, questions, gold}`` with gold as probabilities."""
    req = request(input_value)
    targets = answers(req["questions"], expected)
    return {
        "state": req["state"],
        "questions": req["questions"],
        "gold": {
            key: {"probabilities": dict(zip(options(req["questions"][key]), vector, strict=True))}
            for key, vector in targets.items()
        },
    }


def shape(questions: dict) -> dict:
    """The widest choice or noul option list, the most score levels and the question count."""
    option_counts = [
        2 if question["type"] == "noul" else len(question["criteria"])
        for question in questions.values()
        if question["type"] in ("choice", "noul")
    ]
    levels = [
        len(question["criteria"]) for question in questions.values() if question["type"] == "score"
    ]
    return {
        "options": max(option_counts, default=0),
        "score_levels": max(levels, default=0),
        "questions": len(questions),
    }


def widest(shapes: list[dict]) -> dict:
    """The element-wise maximum of several shapes."""
    return {
        key: max((shape[key] for shape in shapes), default=0)
        for key in ("options", "score_levels", "questions")
    }


def violations(questions: dict, limits: dict) -> list[str]:
    """Why a model with ``limits`` cannot answer ``questions``; empty when it can."""
    found = []
    if len(questions) > limits["questions"]:
        found.append(
            f"{len(questions)} questions in one request; this model takes up to "
            f"{limits['questions']}"
        )
    for key, question in questions.items():
        if question["type"] == "choice" and len(question["criteria"]) > limits["options"]:
            found.append(
                f"question {key} has {len(question['criteria'])} options; this model takes up "
                f"to {limits['options']}"
            )
        if question["type"] == "score" and len(question["criteria"]) > limits["score_levels"]:
            found.append(
                f"question {key} has {len(question['criteria'])} levels; this model takes up "
                f"to {limits['score_levels']}"
            )
    return found


def predicted(question: dict, answer: Any) -> list[float]:
    """A System One answer as a probability per option, in ``options(question)`` order."""
    if not isinstance(answer, dict):
        raise DecisionRowError("the model returned no answer")
    keys = options(question)
    if question["type"] == "noul":
        true = answer.get("noul")
        if isinstance(true, bool) or not isinstance(true, (int, float)) or not 0 <= true <= 1:
            raise DecisionRowError("the model returned no noul probability")
        return [float(true), 1.0 - float(true)]
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(keys):
        raise DecisionRowError("the model's probabilities do not match the options")
    values = [probabilities[key] for key in keys]
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
        for v in values
    ):
        raise DecisionRowError("the model returned invalid probabilities")
    total = math.fsum(values)
    if not math.isclose(total, 1.0, abs_tol=0.01):
        raise DecisionRowError("the model's probabilities do not sum to one")
    return [v / total for v in values]


def gold_option(probabilities: dict[str, float]) -> str | None:
    """The one option a gold answer favours, or None when its top weight is shared."""
    top = max(probabilities.values())
    leaders = [key for key, weight in probabilities.items() if weight == top]
    return leaders[0] if len(leaders) == 1 else None


class Coverage:
    """Whether a table's gold answers reach every option of each question.

    Feed it ``record()`` outputs; ``warnings()`` names thin options and a dominant answer.
    Advisory only: the table stays usable.
    """

    MIN_OPTION_ROWS = 5
    MIN_DOMINANT_ROWS = 10
    DOMINANT_SHARE = 0.9

    def __init__(self) -> None:
        self.total = 0
        self.questions: dict[str, dict] = {}
        self.leaders: dict[str, Counter[str]] = {}

    def add(self, record: dict) -> None:
        self.total += 1
        for key, question in record["questions"].items():
            self.questions.setdefault(key, question)
            leader = gold_option(record["gold"][key]["probabilities"])
            if leader is not None:
                self.leaders.setdefault(key, Counter())[leader] += 1

    def warnings(self) -> list[str]:
        found = []
        for key, question in self.questions.items():
            counts = self.leaders.get(key, Counter())
            if question["type"] != "noul":
                keys = options(question)
                thin = sum(1 for option in keys if counts[option] < self.MIN_OPTION_ROWS)
                if thin:
                    found.append(
                        f"question {key}: {thin} of {len(keys)} options have fewer than "
                        f"{self.MIN_OPTION_ROWS} rows"
                    )
            if self.total >= self.MIN_DOMINANT_ROWS and counts:
                answer, rows = counts.most_common(1)[0]
                if rows / self.total >= self.DOMINANT_SHARE:
                    found.append(
                        f"question {key}: '{answer}' is the answer in "
                        f"{round(100 * rows / self.total)}% of rows"
                    )
        return found
