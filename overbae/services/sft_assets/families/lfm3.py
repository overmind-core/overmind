"""LFM3 hooks — full-FT attention dtype split, and conv layers have no RoPE entry."""

from __future__ import annotations

from typing import Any

from families import DefaultHooks


def _skip_conv_rope() -> None:
    # transformers 5.17 indexes rope_parameters by every layer_types entry, then
    # validates each value as a dict. LFM3 conv layers have no entry, and the
    # rewrite leaves a float where a dict is required. The checkpoint already
    # carries per-layer rope_parameters, so both steps are skipped for lfm3.
    from transformers.configuration_utils import PreTrainedConfig

    original = PreTrainedConfig.standardize_rope_params
    if getattr(original, "_lfm3_conv", False):
        return
    validate = PreTrainedConfig.validate_rope

    def wrapped(self):
        if getattr(self, "model_type", None) == "lfm3":
            return None
        return original(self)

    def wrapped_validate(self):
        if getattr(self, "model_type", None) == "lfm3":
            return None
        return validate(self)

    wrapped._lfm3_conv = True
    PreTrainedConfig.standardize_rope_params = wrapped
    PreTrainedConfig.validate_rope = wrapped_validate


class Lfm3Hooks(DefaultHooks):
    def env_overrides(self, *, use_lora: bool) -> dict[str, str]:
        _skip_conv_rope()
        # Unsloth's Lfm3 block compile rewrites conv layers to linear_attention,
        # which the model's causal mask map does not contain.
        out = {"UNSLOTH_COMPILE_DISABLE": "1", "UNSLOTH_BLOCK_COMPILE": "0"}
        if not use_lora:
            out["UNSLOTH_ENABLE_FLEX_ATTENTION"] = "0"
        return out

    def load_kwargs(self, *, use_lora: bool) -> dict[str, Any]:
        if use_lora:
            return {}
        return {"attn_implementation": "sdpa"}

    def post_load(self, model: Any, tokenizer: Any, *, use_lora: bool) -> None:
        # Unsloth's Lfm3 patch renames conv to linear_attention. The remote
        # mask builder only knows conv, so the first step raises KeyError.
        base = model.get_base_model() if hasattr(model, "get_base_model") else model
        config = base.config
        types = getattr(config, "layer_types", None)
        if isinstance(types, list) and "linear_attention" in types:
            config.layer_types = [
                "conv" if layer == "linear_attention" else layer for layer in types
            ]
        for module in base.modules():
            if getattr(module, "layer_type", None) == "linear_attention":
                module.layer_type = "conv"


hooks = Lfm3Hooks()
