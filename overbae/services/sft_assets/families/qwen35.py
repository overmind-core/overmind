"""Qwen3.5/3.6/3.8 — SDPA; skip Unsloth's dense S² causal mask above 131k."""

from __future__ import annotations

from typing import Any

from families import DefaultHooks

# 131k LoRA already trains with the dense SDPA mask. 262k's mask is ~50 GiB.
_SKIP_DENSE_MASK_ABOVE = 140000


def _seq_len_from_mask_kwargs(*args: Any, **kwargs: Any) -> int | None:
    for key in ("inputs_embeds", "input_ids"):
        t = kwargs.get(key)
        if t is not None and hasattr(t, "shape") and len(t.shape) >= 2:
            return int(t.shape[1])
    cp = kwargs.get("cache_position")
    if cp is not None and hasattr(cp, "numel"):
        return int(cp.numel())
    return None


class Qwen35Hooks(DefaultHooks):
    def env_overrides(self, *, use_lora: bool) -> dict[str, str]:
        # Unsloth flex patch falls through to eager_attention_forward on this family.
        return {"UNSLOTH_ENABLE_FLEX_ATTENTION": "0"}

    def load_kwargs(self, *, use_lora: bool) -> dict[str, Any]:
        # Image has no FA2. SDPA + is_causal still uses Hopper flash; Unsloth's
        # create_causal_mask wrapper is what materializes S².
        return {"attn_implementation": "sdpa"}

    def post_load(self, model: Any, tokenizer: Any, *, use_lora: bool) -> None:
        # modeling_qwen3_5 from-imports create_causal_mask; patch that binding.
        import transformers.models.qwen3_5.modeling_qwen3_5 as q35

        inner = q35.create_causal_mask
        if getattr(inner, "_overmind_skip_dense_s2", False):
            return

        def _skip_dense_s2(*args: Any, **kwargs: Any):
            seq = _seq_len_from_mask_kwargs(*args, **kwargs)
            if seq is not None and seq > _SKIP_DENSE_MASK_ABOVE:
                print(f"qwen35: skip dense causal mask seq={seq}", flush=True)
                return None
            return inner(*args, **kwargs)

        _skip_dense_s2._overmind_skip_dense_s2 = True  # type: ignore[attr-defined]
        q35.create_causal_mask = _skip_dense_s2


hooks = Qwen35Hooks()
