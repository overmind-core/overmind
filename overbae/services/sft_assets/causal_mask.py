"""Unsloth's training call reaches the decoder with ``causal_mask=None``.

That forward is not causal. A serving call passes an xformers lower-triangular
mask, so a LoRA trained on the unmasked path does not show up in greedy decode.
"""

from __future__ import annotations

from typing import Any

_FAST_FORWARD = "unsloth.models.llama"


def install_training_causal_mask(model: Any, mask: Any) -> bool:
    decoder = model.get_decoder()
    forward = getattr(decoder.forward, "__func__", decoder.forward)
    if getattr(forward, "__module__", "") != _FAST_FORWARD:
        return False
    model_type = getattr(getattr(model, "config", None), "model_type", "")
    if model_type not in {"qwen3", "qwen3_moe"}:
        return False
    orig = decoder.forward

    def _forward(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("causal_mask") is None:
            kwargs["causal_mask"] = mask
        return orig(*args, **kwargs)

    decoder.forward = _forward
    return True
