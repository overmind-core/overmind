import math

import pandas as pd
import pytest

from modal_shared.decisions import decision_line, decision_request
from overbae.services.datasets.contract import measure


def request():
    return {
        "state": "Evidence",
        "question": "Rate relevance",
        "kind": "score",
        "options": ["1", "2", "3", "4", "5"],
    }


def test_mean_only_target_survives_training_contract_without_fabricating_votes():
    row = {
        "decision": {
            **request(),
            "target_mean": 3.4,
            "option_values": [1, 2, 3, 4, 5],
            "target_semantics": "ordinal_mean",
            "target_provenance": {"source": "ratings"},
        }
    }
    validated = decision_line(row)
    assert validated == row
    assert "target_probabilities" not in validated["decision"]
    report = measure(pd.DataFrame([row]))
    assert report["train"]["ok"], report


@pytest.mark.parametrize(
    "change",
    [
        {"target_mean": math.nan},
        {"target_mean": 6},
        {"kind": "choice"},
        {"target_probabilities": [0, 0, 0.6, 0.4, 0]},
        {"target_semantics": "annotator_distribution"},
    ],
)
def test_mean_targets_reject_ambiguous_or_invalid_supervision(change):
    with pytest.raises(ValueError):
        decision_line(
            {
                "decision": {
                    **request(),
                    "target_mean": 3.4,
                    "option_values": [1, 2, 3, 4, 5],
                    "target_semantics": "ordinal_mean",
                    **change,
                }
            }
        )


def test_declared_gold_cannot_hide_a_soft_distribution():
    with pytest.raises(ValueError):
        decision_line(
            {
                "decision": {
                    **request(),
                    "target_probabilities": [0, 0, 0.6, 0.4, 0],
                    "target_semantics": "categorical_gold",
                }
            }
        )


def test_provenance_and_mean_targets_cannot_enter_inference():
    for extra in (
        {"target_mean": 3.4},
        {"target_semantics": "ordinal_mean"},
        {"target_provenance": {"answer": 3.4}},
    ):
        with pytest.raises(ValueError):
            decision_request({**request(), **extra})


def test_mean_evaluation_reference_is_valid_without_probability_metrics():
    report = measure(
        pd.DataFrame(
            [
                {
                    "input": {"decision": request()},
                    "expected_output": {"mean": 3.4, "values": [1, 2, 3, 4, 5]},
                }
            ]
        )
    )
    assert report["eval"]["ok"], report
