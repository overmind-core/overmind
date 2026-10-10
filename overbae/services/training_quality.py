import math

from django.utils import timezone

from modal_shared.training_monitoring import fingerprint


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError("Classification counts are missing or invalid")
    return value


def _comparison(generation, labels):
    if generation.get("labels") != labels:
        raise ValueError("Classification labels do not match the frozen policy")
    expected, scored, technical, unscorable, invalid = (
        _count(generation.get(key))
        for key in ("expected", "scored", "technical_errors", "unscorable", "invalid_labels")
    )
    if not scored or scored + technical + unscorable != expected:
        raise ValueError("No comparable classification coverage")
    classes = generation.get("per_class")
    predictions = generation.get("prediction_distribution")
    if not isinstance(classes, dict) or not isinstance(predictions, dict):
        raise ValueError("Classification distributions are missing")
    if set(classes) != set(labels) or set(predictions) - set(labels):
        raise ValueError("Classification distributions do not match the frozen labels")
    supports = [_count(classes[label].get("support")) for label in labels]
    predicted = [_count(predictions.get(label, 0)) for label in labels]
    matrix = generation.get("confusion_matrix")
    if not isinstance(matrix, list) or len(matrix) != len(labels):
        raise ValueError("Classification matrix is missing")
    if any(not isinstance(row, list) or len(row) != len(labels) for row in matrix):
        raise ValueError("Classification matrix dimensions differ")
    for row in matrix:
        for value in row:
            _count(value)
    if (
        sum(supports) != scored
        or sum(predicted) + invalid != scored
        or any(sum(row) > support for row, support in zip(matrix, supports, strict=True))
        or any(sum(row[i] for row in matrix) != predicted[i] for i in range(len(labels)))
    ):
        raise ValueError("Classification counts disagree")
    correct = sum(matrix[i][i] for i in range(len(labels)))
    accuracy = generation.get("accuracy")
    if (
        type(accuracy) not in {int, float}
        or not 0 <= accuracy <= 1
        or not math.isfinite(accuracy)
        or not math.isclose(accuracy, correct / scored)
    ):
        raise ValueError("Classification accuracy does not match its counts")
    majority = max(supports)
    return {
        "expected": expected,
        "scored": scored,
        "correct": correct,
        "majority_correct": majority,
        "majority_label_indices": [i for i, count in enumerate(supports) if count == majority],
        "unpredicted_label_indices": [
            i for i, count in enumerate(supports) if count and not predicted[i]
        ],
    }


def assessment(job, check):
    policy = (job.hyperparameters or {}).get("monitoring") or {}
    probe = policy.get("generation") or {}
    generation = check.metrics.get("generation")
    if check.state != "completed" or probe.get("kind") != "classification" or not generation:
        return None
    previous = check.facts.get("assessment")
    if (
        previous
        and previous.get("rule_version") == 1
        and previous.get("source_receipt_fingerprint") == check.receipt_fingerprint
    ):
        return previous
    observed = timezone.now().isoformat()
    result = {
        "source": "recorded_generation_metrics",
        "rule_version": 1,
        "source_receipt_fingerprint": check.receipt_fingerprint,
        "source_observed_at": check.observed_at.isoformat() if check.observed_at else None,
        "assessed_at": observed,
        "state": "inconclusive",
        "comparison": None,
        "findings": [],
        "limitations": [
            "Same scored development subset, not the complete source population",
            "Missing predictions do not establish a cause or production class collapse",
            "No automatic training action or final-benchmark conclusion",
        ],
    }
    try:
        if fingerprint(policy) != check.policy_fingerprint:
            raise ValueError("Recorded policy identity differs")
        labels = probe.get("labels")
        if (
            not isinstance(labels, list)
            or not 2 <= len(labels) <= 1000
            or any(not isinstance(label, str) for label in labels)
            or len(set(labels)) != len(labels)
        ):
            raise ValueError("Frozen classification labels are unavailable")
        counts = _comparison(generation, labels)
    except (ValueError, TypeError, AttributeError):
        result["reason"] = (
            "Classification contract or counts are missing, inconsistent or unscorable"
        )
        return result
    result.update(state="measured", comparison=counts)
    if counts["unpredicted_label_indices"]:
        result["findings"].append(
            {
                "code": "represented_labels_not_predicted",
                "message": "Some labels present in scored references have no predictions",
            }
        )
    if counts["correct"] < counts["majority_correct"]:
        result["findings"].append(
            {
                "code": "below_probe_majority_baseline",
                "message": "Accuracy is below the majority-label baseline on the same scored examples",
            }
        )
    result["findings"] = [
        {
            **item,
            "id": f"{check.pk}:{item['code']}:1",
            "rule_version": 1,
            "state": "observed",
            "check_id": str(check.pk),
            "step": check.step,
            "sample_fingerprint": check.facts.get("generation_sample_fingerprint"),
            "assessed_at": observed,
            "scope": "scored_development_generation",
            "action": "none",
        }
        for item in result["findings"]
    ]
    return result
