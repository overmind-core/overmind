"""Model-independent typed decisions shared by data, training and inference."""

import json
import math
from itertools import zip_longest

DECISION_OBJECTIVE = "decision_cross_entropy"
MEAN_DECISION_OBJECTIVE = "decision_supervised"
DECISION_OBJECTIVES = {DECISION_OBJECTIVE, MEAN_DECISION_OBJECTIVE}
TEXT_OBJECTIVE = "assistant_cross_entropy"
REQUEST_FIELDS = ("state", "question", "kind", "options")
TARGET_FIELDS = (
    "target_probabilities",
    "target_mean",
    "option_values",
    "target_semantics",
    "target_provenance",
)
DISTRIBUTION_SEMANTICS = {
    "categorical_gold",
    "annotator_distribution",
    "posterior",
    "ordinal_histogram",
    "pairwise_preference",
    "teacher_distribution",
}


def decision_request(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != set(REQUEST_FIELDS):
        raise ValueError("A decision request requires only state, question, kind and options")
    if not isinstance(value["state"], str):
        raise ValueError("decision.state must be text")
    if not isinstance(value["question"], str) or not value["question"].strip():
        raise ValueError("decision.question must be nonempty text")
    if value["kind"] not in {"choice", "noul", "score"}:
        raise ValueError("decision.kind must be choice, noul or score")
    options = value["options"]
    if (
        not isinstance(options, list)
        or not 2 <= len(options) <= 255
        or any(not isinstance(x, str) or not x.strip() for x in options)
        or len(set(options)) != len(options)
    ):
        raise ValueError("decision.options requires 2–255 distinct nonempty strings")
    if value["kind"] == "noul" and len(options) != 2:
        raise ValueError("noul decisions require two options in false/true order")
    return dict(value)


def decision_line(record: dict) -> dict:
    value = record.get("decision")
    if not isinstance(value, dict):
        raise ValueError("decision must be an object")
    required = set(REQUEST_FIELDS)
    if not required <= value.keys() or value.keys() - required - set(TARGET_FIELDS) - {"weight"}:
        raise ValueError("Decision fields must describe a request and explicit supervision")
    decision_request({key: value[key] for key in REQUEST_FIELDS})
    options = value["options"]
    semantics = value.get("target_semantics")
    if "target_mean" in value:
        if (
            "target_probabilities" in value
            or semantics != "ordinal_mean"
            or value["kind"] != "score"
        ):
            raise ValueError(
                "A mean-only score requires ordinal_mean semantics and no probabilities"
            )
        ordinal_mean(value["target_mean"], value.get("option_values"), len(options))
    else:
        if "option_values" in value or semantics not in DISTRIBUTION_SEMANTICS | {None}:
            raise ValueError("Unknown or incompatible distribution target semantics")
        target = value.get("target_probabilities")
        if (
            not isinstance(target, list)
            or len(target) != len(options)
            or any(
                type(x) not in {int, float} or not math.isfinite(x) or not 0 <= x <= 1
                for x in target
            )
            or not math.isclose(math.fsum(target), 1.0, abs_tol=1e-6, rel_tol=0)
        ):
            raise ValueError(
                "target_probabilities must be finite, normalized and match the options"
            )
        if semantics == "categorical_gold" and max(target) != 1:
            raise ValueError("Categorical gold requires one observed answer")
        if semantics == "ordinal_histogram" and value["kind"] != "score":
            raise ValueError("Ordinal histograms require score decisions")
        if semantics == "pairwise_preference" and len(options) != 2:
            raise ValueError("Pairwise preferences require two options")
    if "target_provenance" in value:
        if not isinstance(value["target_provenance"], dict):
            raise ValueError("Target provenance must be an object")
        try:
            json.dumps(value["target_provenance"], allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("Target provenance must contain finite JSON values") from exc
    weight = value.get("weight", 1.0)
    if type(weight) not in {int, float} or not math.isfinite(weight) or weight <= 0:
        raise ValueError("decision.weight must be finite and positive")
    return {"decision": dict(value)}


def ordinal_mean(mean, values, count):
    if (
        not isinstance(values, list)
        or len(values) != count
        or any(type(x) not in {int, float} or not math.isfinite(x) for x in values)
        or any(a >= b for a, b in zip(values[:-1], values[1:], strict=True))
        or type(mean) not in {int, float}
        or not math.isfinite(mean)
        or not values[0] <= mean <= values[-1]
    ):
        raise ValueError(
            "An ordinal mean requires explicit increasing option values and an in-range mean"
        )
    return values


def decision_reference(request, reference):
    decision_request(request)
    if not isinstance(reference, dict):
        raise ValueError("A decision reference must be an object")
    if set(reference) == {"mean", "values"} and request["kind"] == "score":
        ordinal_mean(reference["mean"], reference["values"], len(request["options"]))
    elif set(reference) == {"probabilities"}:
        decision_line({"decision": {**request, "target_probabilities": reference["probabilities"]}})
    else:
        raise ValueError("Use a probability distribution or an explicit ordinal mean reference")
    return reference


def compare_predictions(expected, restored, *, tolerance=1e-4):
    count = 0
    largest = 0.0
    for left, right in zip_longest(expected, restored):
        if left is None or right is None:
            raise ValueError("Checkpoint prediction coverage differs")
        for key in ("key", "kind", "question", "options", *TARGET_FIELDS):
            if left.get(key) != right.get(key):
                raise ValueError("Checkpoint prediction identity differs")
        for row in (left, right):
            p = row["probabilities"]
            if (
                len(p) != len(row.get("target_probabilities", row.get("option_values", [])))
                or any(not math.isfinite(x) or not 0 <= x <= 1 for x in p)
                or not math.isclose(sum(p), 1.0, abs_tol=1e-6, rel_tol=0)
            ):
                raise ValueError("Checkpoint probabilities are invalid")
        largest = max(
            largest,
            max(
                abs(a - b)
                for a, b in zip(left["probabilities"], right["probabilities"], strict=True)
            ),
        )
        count += 1
    if not count or largest > tolerance:
        raise ValueError(f"Checkpoint prediction parity failed: maximum difference {largest}")
    return {"decisions": count, "max_absolute_error": largest, "tolerance": tolerance}
