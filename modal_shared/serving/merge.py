"""Fold one LoRA into base tensors as vLLM reads them.

The delta is applied to each tensor the runai streamer yields. No second
checkpoint is written; another container cannot see this process's memory.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Generator
from pathlib import Path

import torch
from safetensors import safe_open

_ENV = "OVERMIND_FOLD_ADAPTER"


def install_weight_fold(adapter: Path | None = None) -> None:
    path = adapter or os.environ.get(_ENV)
    if not path:
        return
    adapter_dir = Path(path)
    scale = _scale(json.loads((adapter_dir / "adapter_config.json").read_text()))
    weights = _load_adapter(adapter_dir / "adapter_model.safetensors")
    consumed: set[str] = set()

    import vllm.model_executor.model_loader.weight_utils as weight_utils

    original = weight_utils.runai_safetensors_weights_iterator

    def folded(
        hf_weights_files: list[str],
        use_tqdm_on_load: bool,
        is_distributed: bool = False,
    ) -> Generator[tuple[str, torch.Tensor]]:
        for name, tensor in original(hf_weights_files, use_tqdm_on_load, is_distributed):
            prefix = _pair_prefix(name, weights)
            if prefix is not None:
                delta = weights[f"{prefix}.lora_B.weight"] @ weights[f"{prefix}.lora_A.weight"]
                tensor = tensor + scale * delta.to(device=tensor.device, dtype=tensor.dtype)
                consumed.add(f"{prefix}.lora_A.weight")
                consumed.add(f"{prefix}.lora_B.weight")
            yield name, tensor
        leftover = set(weights) - consumed
        if leftover:
            first = sorted(leftover)[0]
            raise RuntimeError(f"{len(leftover)} adapter keys were not merged; first: {first}")

    weight_utils.runai_safetensors_weights_iterator = folded
    loader = sys.modules.get("vllm.model_executor.model_loader.runai_streamer_loader")
    if loader is not None:
        loader.runai_safetensors_weights_iterator = folded
    print(f"[vLLM] folding {adapter_dir} into weights as they load", flush=True)


def _scale(config: dict) -> float:
    rank = config.get("r")
    alpha = config.get("lora_alpha")
    if not isinstance(rank, int) or rank < 1 or not isinstance(alpha, (int, float)):
        raise ValueError("adapter_config.json needs integer r and lora_alpha")
    divisor = rank**0.5 if config.get("use_rslora") else rank
    return float(alpha) / divisor


def _load_adapter(path: Path) -> dict[str, torch.Tensor]:
    weights: dict[str, torch.Tensor] = {}
    with safe_open(path, framework="torch") as handle:
        for name in handle.keys():  # noqa: SIM118
            key = name.removeprefix("base_model.model.")
            if not (key.endswith(".lora_A.weight") or key.endswith(".lora_B.weight")):
                raise ValueError(f"Unexpected adapter key: {name}")
            weights[key] = handle.get_tensor(name)
    return weights


def _pair_prefix(tensor_name: str, lora_weights: dict[str, torch.Tensor]) -> str | None:
    if not tensor_name.endswith(".weight"):
        return None
    prefix = tensor_name.rsplit(".", 1)[0]
    if f"{prefix}.lora_A.weight" in lora_weights and f"{prefix}.lora_B.weight" in lora_weights:
        return prefix
    if prefix.endswith(".linear"):
        outer = prefix[: -len(".linear")]
        if f"{outer}.lora_A.weight" in lora_weights and f"{outer}.lora_B.weight" in lora_weights:
            return outer
    return None
