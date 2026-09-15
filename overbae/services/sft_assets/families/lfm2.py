"""LFM2 hooks — SDPA + disable flex attention on full FT (Q/K fp32 vs V bf16)."""

from __future__ import annotations

from typing import Any

from families import DefaultHooks


class Lfm2Hooks(DefaultHooks):
    def env_overrides(self, *, use_lora: bool) -> dict[str, str]:
        if use_lora:
            return {}
        return {"UNSLOTH_ENABLE_FLEX_ATTENTION": "0"}

    def load_kwargs(self, *, use_lora: bool) -> dict[str, Any]:
        if use_lora:
            return {}
        return {"attn_implementation": "sdpa"}


hooks = Lfm2Hooks()
