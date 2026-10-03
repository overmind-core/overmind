import math

import pytest

from modal_shared.decision_metrics import score_decision


def request(kind="choice", count=2):
    return {
        "state": "evidence",
        "question": "Which?",
        "kind": kind,
        "options": [str(i) for i in range(count)],
    }


def prediction(probabilities):
    return {
        "probabilities": probabilities,
        "log_probabilities": [math.log(p) for p in probabilities],
    }


def test_soft_targets_use_full_distribution_and_do_not_claim_hard_accuracy():
    result = score_decision(request(), {"probabilities": [0.25, 0.75]}, prediction([0.8, 0.2]))
    assert result["cross_entropy"] == pytest.approx(-0.25 * math.log(0.8) - 0.75 * math.log(0.2))
    assert result["brier"] == pytest.approx(0.605)
    assert result["top1_reference_mass"] == 0.25
    assert result["mode_agreement"] == 0
    assert "accuracy" not in result and "rps" not in result


def test_hard_target_nll_remains_finite_for_underflowed_probability():
    result = score_decision(
        request(),
        {"probabilities": [0, 1]},
        {"probabilities": [1, 0], "log_probabilities": [0, -2000]},
    )
    assert result["cross_entropy"] == 2000
    assert result["accuracy"] == 0
    assert result["brier"] == 2


def test_ordinal_distribution_uses_cumulative_rps_and_expected_score():
    result = score_decision(
        request("score", 3), {"probabilities": [0, 1, 0]}, prediction([0.2, 0.3, 0.5])
    )
    assert result["rps"] == pytest.approx((0.2**2 + 0.5**2) / 2)
    assert result["expected_score"] == pytest.approx(0.65)
    assert result["expected_score_mae"] == pytest.approx(0.15)


def test_mean_only_gold_never_fabricates_a_distribution_or_class_label():
    result = score_decision(
        request("score", 3), {"mean": 4, "values": [0, 5, 10]}, prediction([0.2, 0.3, 0.5])
    )
    assert result["expected_score"] == pytest.approx(6.5)
    assert result["expected_score_mae"] == pytest.approx(2.5)
    assert not {"accuracy", "cross_entropy", "brier", "rps", "mode_agreement"} & result.keys()


@pytest.mark.parametrize(
    "bad",
    [
        {"probabilities": [0.5, 0.5], "log_probabilities": [0, 0]},
        {"probabilities": [0.5, 0.2], "log_probabilities": [math.log(0.5), math.log(0.2)]},
        {"probabilities": [0.5, 0.5], "log_probabilities": [math.nan, math.log(0.5)]},
        {"probabilities": [0.5], "log_probabilities": [math.log(0.5)]},
    ],
)
def test_invalid_predictions_fail_instead_of_entering_quality_scores(bad):
    with pytest.raises(ValueError):
        score_decision(request(), {"probabilities": [0, 1]}, bad)


@pytest.mark.parametrize(
    "reference",
    [
        {"probabilities": [0.1, 0.4]},
        {"probabilities": [0, 1], "mean": 0.5},
        {"mean": 2, "values": [0, 1]},
        {"mean": 0.5, "values": [1, 0]},
        {"mean": 0.5},
    ],
)
def test_invalid_or_ambiguous_references_are_technical_failures(reference):
    with pytest.raises(ValueError):
        score_decision(request("score"), reference, prediction([0.5, 0.5]))
