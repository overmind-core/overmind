"""No-truncation guards for Unsloth Modal training."""

from __future__ import annotations

import pytest

from overbae.services.finetuning_policy import TrainingPlanError, baseten_context_length
from overbae.services.sft_assets.truncation import refuse_truncation


def test_refuse_truncation_raises_on_overlong_row():
    with pytest.raises(RuntimeError, match="refusing to truncate"):
        refuse_truncation(9000, 8192, row_index=3)


def test_refuse_truncation_allows_exact_fit():
    refuse_truncation(8192, 8192, row_index=0)


def test_baseten_context_length_refuses_clamp_below_dataset():
    with pytest.raises(TrainingPlanError, match="refusing to clamp"):
        baseten_context_length(20_000, model_max=4096)


def test_baseten_context_length_allows_clamp_when_rows_are_short():
    # Requested 16k but model max 4k and rows only need ~100 — clamp is fine.
    assert baseten_context_length(100, model_max=4096, requested=16384) == 4096
