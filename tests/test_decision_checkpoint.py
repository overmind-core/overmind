import copy

import pytest

from modal_shared.decisions import compare_predictions


def predictions():
    return [
        {
            "key": "one",
            "probabilities": [0.2, 0.8],
            "target_probabilities": [0.3, 0.7],
            "kind": "choice",
        },
        {
            "key": "two",
            "probabilities": [0.4, 0.3, 0.3],
            "target_probabilities": [0, 0, 1],
            "kind": "score",
        },
    ]


def test_checkpoint_reload_accepts_only_matching_complete_predictions():
    original = predictions()
    restored = copy.deepcopy(original)
    restored[0]["probabilities"] = [0.20001, 0.79999]
    result = compare_predictions(iter(original), iter(restored))
    assert result["decisions"] == 2 and result["max_absolute_error"] < 0.0001


@pytest.mark.parametrize(
    "problem", ["missing", "key", "target", "nonfinite", "normalization", "drift"]
)
def test_checkpoint_reload_rejects_changed_or_missing_predictions(problem):
    original, restored = predictions(), predictions()
    if problem == "missing":
        restored.pop()
    elif problem == "key":
        restored[0]["key"] = "other"
    elif problem == "target":
        restored[0]["target_probabilities"] = [0.5, 0.5]
    elif problem == "nonfinite":
        restored[0]["probabilities"] = [float("nan"), 0.8]
    elif problem == "normalization":
        restored[0]["probabilities"] = [0.2, 0.7]
    else:
        restored[0]["probabilities"] = [0.3, 0.7]
    with pytest.raises(ValueError):
        compare_predictions(iter(original), iter(restored))
