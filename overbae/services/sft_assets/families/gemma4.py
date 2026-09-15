"""Gemma 4 Unsloth hooks — FastModel; adamw_torch for full FT."""

from __future__ import annotations

import os
from typing import Any

from families import DefaultHooks


def _model_id() -> str:
    return os.environ.get("MODEL_ID", "")


def _needs_torch_gc(mid: str) -> bool:
    m = mid.lower()
    return "31b" in m or "e2b" in m or "e4b" in m or "12b" in m or "a4b" in m


class Gemma4Hooks(DefaultHooks):
    def env_overrides(self, *, use_lora: bool) -> dict[str, str]:
        if use_lora:
            # flex_attention's compiled path silently falls back to dense
            # math_attention on Gemma4 (materializes [B,H,S,S] scores — 50GB+
            # at S=64k), OOMing far below the real ceiling.
            return {"UNSLOTH_ENABLE_FLEX_ATTENTION": "0"}
        # Full FT on 12B+ hits torch._dynamo SideEffects / HigherOrderOperator
        # under Unsloth's auto-compile. Disable compile for full only — LoRA
        # path is fine and keep its speed patches.
        return {"UNSLOTH_COMPILE_DISABLE": "1"}

    def load_kwargs(self, *, use_lora: bool) -> dict[str, Any]:
        # engine_unsloth only setdefaults attn_implementation=sdpa for Full FT;
        # LoRA needs the same override to avoid the flex_attention fallback.
        return {"attn_implementation": "sdpa"}

    def fast_model_cls(self, fast_language_model: Any, fast_model: Any | None) -> Any:
        if fast_model is not None:
            return fast_model
        return fast_language_model

    def peft_gradient_checkpointing(self) -> bool | str:
        # Unsloth offload GC ("unsloth") mismatches Gemma4 activation recomputes
        # (CheckpointError). Torch GC (True) is the path that actually shrinks
        # the S² working set. 31B needs it to fit at all; E2B/E4B/12B/A4B LoRA
        # need it to climb past the no-GC activation wall.
        return _needs_torch_gc(_model_id())

    def device_map(self, n_gpus: int) -> str | dict | None:
        # balanced multi-GPU hits cross-device index errors in flex_attention / PLE.
        if n_gpus > 1:
            return {"": 0}
        return None

    def peft_lora_dropout(self, default: float, *, model_id: str) -> float:
        # MoE expert ParamWrapper LoRA rejects dropout ≠ 0 (PEFT).
        if default and "a4b" in model_id.lower():
            return 0.0
        return default

    def sft_config_overrides(self, *, use_lora: bool) -> dict[str, Any]:
        # Full FT: adamw_8bit dies with ops.cu on multimodal path.
        mid = _model_id()
        m = mid.lower()
        gc = "31b" in m or "12b" in m or (use_lora and _needs_torch_gc(mid))
        return {
            "optim": "adamw_8bit" if use_lora else "adamw_torch",
            "gradient_checkpointing": gc,
        }


hooks = Gemma4Hooks()
