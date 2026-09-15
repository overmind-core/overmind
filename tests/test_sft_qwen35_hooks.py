from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "overbae" / "services" / "sft_assets"))

from families.qwen35 import _seq_len_from_mask_kwargs, hooks


def test_qwen35_sdpa_and_flex_off():
    assert hooks.env_overrides(use_lora=True)["UNSLOTH_ENABLE_FLEX_ATTENTION"] == "0"
    assert hooks.load_kwargs(use_lora=True)["attn_implementation"] == "sdpa"


def test_seq_len_from_mask_kwargs():
    ids = SimpleNamespace(shape=(1, 262144))
    assert _seq_len_from_mask_kwargs(input_ids=ids) == 262144
    assert _seq_len_from_mask_kwargs() is None
