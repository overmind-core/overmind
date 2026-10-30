"""How many full-context sequences fit on one GPU.

Chat serving stores the KV cache in FP8, so each element is one byte. The 2 in
the per-token size is the K tensor plus the V tensor, not the dtype width.
"""

from __future__ import annotations

import json
from pathlib import Path

GPU_MEMORY_UTILIZATION = 0.90
# Non-KV memory vLLM holds beyond the weights: peak activations, CUDA context,
# allocator slack, and captured CUDA graphs. Measured at 3.25 GiB on
# Qwen2.5-72B-Instruct FP8 / H200; the short capture list adds 0.16-0.22 GiB
# of graphs plus ~0.15 GiB of activation, so 4.0 keeps the eager 3.5 margin.
# MoE bases run higher (~5.1 GiB). That gap only skews the concurrency estimate.
ACTIVATION_OVERHEAD_GB = 4.0
KV_DTYPE_BYTES = 1
MAX_CONCURRENT = 32

GPU_VRAM_GB = {
    "L4": 24,
    "L40S": 48,
    "A100-80GB": 80,
    "H100": 80,
    "H200": 141,
    "B200": 192,
    "B300": 288,
}


def sequence_cap(
    *,
    vram_gb: float,
    weight_gb: float,
    num_attn_layers: int | None,
    num_kv_heads: int | None,
    head_dim: int | None,
    max_model_len: int,
) -> int | None:
    """Sequences that fit, or 0 when one full sequence does not. None if the shape is unknown."""
    if not vram_gb or not num_attn_layers or not num_kv_heads or not head_dim or not max_model_len:
        return None
    per_token = 2 * int(num_attn_layers) * int(num_kv_heads) * int(head_dim) * KV_DTYPE_BYTES
    one_seq_gb = (int(max_model_len) * per_token) / (1024**3)
    if one_seq_gb <= 0:
        return None
    budget = float(vram_gb) * GPU_MEMORY_UTILIZATION - float(weight_gb) - ACTIVATION_OVERHEAD_GB
    if budget < one_seq_gb:
        return 0
    return max(1, min(MAX_CONCURRENT, int(budget / one_seq_gb)))


def arch_from_config(cfg: dict) -> tuple[int, int, int] | None:
    text = cfg.get("text_config")
    if not isinstance(text, dict) or not text.get("num_hidden_layers"):
        text = cfg
    types = text.get("layer_types")
    if isinstance(types, list) and any(kind == "full_attention" for kind in types):
        layers = sum(1 for kind in types if kind == "full_attention")
    else:
        layers = text.get("num_hidden_layers") or text.get("n_layer")
    heads = text.get("num_key_value_heads") or text.get("num_kv_heads") or text.get("n_head_kv")
    dim = text.get("head_dim")
    if dim is None:
        hidden = text.get("hidden_size") or text.get("n_embd")
        n_heads = text.get("num_attention_heads") or text.get("n_head")
        if hidden and n_heads:
            dim = int(hidden) // int(n_heads)
    if not layers or not heads or not dim:
        return None
    return int(layers), int(heads), int(dim)


def weight_gib(path: Path) -> float | None:
    files = [p for p in Path(path).glob("*.safetensors") if p.is_file()]
    if not files:
        return None
    return sum(p.stat().st_size for p in files) / (1024**3)


def sequence_cap_for_checkpoint(path: Path, *, vram_gb: float, max_model_len: int) -> int | None:
    config_path = Path(path) / "config.json"
    if not config_path.is_file():
        return None
    arch = arch_from_config(json.loads(config_path.read_text()))
    weight = weight_gib(path)
    if arch is None or weight is None:
        return None
    layers, heads, dim = arch
    return sequence_cap(
        vram_gb=vram_gb,
        weight_gb=weight,
        num_attn_layers=layers,
        num_kv_heads=heads,
        head_dim=dim,
        max_model_len=max_model_len,
    )
