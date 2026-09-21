from __future__ import annotations

import pytest

from overbae.services.finetuning_policy import (
    baseten_context_length,
    default_epochs,
    estimated_training_context_length,
)


def test_default_epochs_scaling():
    assert default_epochs(10) == 10  # capped
    assert default_epochs(49) == 7
    assert default_epochs(100) == 3
    assert default_epochs(5000) == 3  # floored
    assert default_epochs(10_000) == 2
    assert default_epochs(50_000) == 1


@pytest.mark.parametrize(
    ("estimate", "requested", "expected"),
    [(0, None, 4096), (7145, 4096, 8192), (7145, 16384, 16384), (100_000, None, 32768)],
)
def test_estimated_context_sizes_without_rejecting_unmeasured_data(estimate, requested, expected):
    assert (
        estimated_training_context_length(estimate, model_max=32768, requested=requested)
        == expected
    )


def test_context_above_largest_bucket_does_not_round_down():
    assert baseten_context_length(model_max=1_000_000, requested=300_000) == 300_000
