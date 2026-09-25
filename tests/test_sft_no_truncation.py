"""No-truncation guards for Unsloth Modal training."""

from __future__ import annotations

import pytest

from overbae.services.sft_assets.truncation import refuse_truncation


def test_refuse_truncation_raises_on_overlong_row():
    with pytest.raises(RuntimeError, match="refusing to truncate"):
        refuse_truncation(9000, 8192, row_index=3)


def test_refuse_truncation_allows_exact_fit():
    refuse_truncation(8192, 8192, row_index=0)
