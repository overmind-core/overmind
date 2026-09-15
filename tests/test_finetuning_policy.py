from __future__ import annotations

from overbae.services.finetuning_policy import default_epochs


def test_default_epochs_scaling():
    assert default_epochs(10) == 10  # capped
    assert default_epochs(49) == 7
    assert default_epochs(100) == 3
    assert default_epochs(5000) == 3  # floored
    assert default_epochs(10_000) == 2
    assert default_epochs(50_000) == 1
