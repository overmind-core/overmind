"""Nemotron-H / Nemotron 3.5 hybrid hooks — keep loss_type=nll.

TRL's chunked_nll patches model.forward assuming a `.past_key_values` output;
Mamba/SSM hybrids return their own dataclass and crash on the first step.
"""

from __future__ import annotations

from typing import Any

from families import DefaultHooks


class NemotronHooks(DefaultHooks):
    def env_overrides(self, *, use_lora: bool) -> dict[str, str]:
        # NemotronH's full-attention layers default to eager, which materializes
        # a [B,H,S,S] weight matrix — 50 GiB at ctx=32768 on a single H100/H200.
        return {"UNSLOTH_ENABLE_FLEX_ATTENTION": "0"}

    def load_kwargs(self, *, use_lora: bool) -> dict[str, Any]:
        return {"attn_implementation": "sdpa"}

    def sft_config_overrides(self, *, use_lora: bool) -> dict[str, Any]:
        return {
            "optim": "adamw_8bit",
            "gradient_checkpointing": True,
            "loss_type": "nll",
        }


hooks = NemotronHooks()
