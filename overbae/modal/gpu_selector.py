"""Picks the cheapest Modal GPU tier whose VRAM holds a model's weights plus enough KV cache for
one full-length sequence, and the max_concurrent_inputs cap that follows from the leftover budget.

``num_attn_layers`` counts FULL attention layers only: a hybrid model's linear-attention layers
carry no traditional KV cache.
"""

from __future__ import annotations

import logging
from typing import Any

from overbae.core.errors import InputValidationError

logger = logging.getLogger(__name__)

GPU_MEMORY_UTILIZATION = 0.90
# Non-KV memory vLLM holds beyond the weights: peak activations, CUDA context,
# NCCL/allocator slack, and captured CUDA graphs. Measured at 3.25 GiB on
# Qwen2.5-72B-Instruct FP8 / H200; capturing CUDAGRAPH_CAPTURE_SIZES adds
# 0.16-0.22 GiB of graphs plus ~0.15 GiB of activation on L4/L40S/H100, so 4.0
# keeps the same margin the eager 3.5 had. Widening the capture list costs
# 1.0-1.43 GiB instead and would need this raised again.
#
# MoE bases run higher — Qwen3-Coder-30B-A3B measured ~5.1 GiB even eager, from
# expert-routing buffers rather than graphs. That gap predates graph capture and
# only skews the concurrency estimate, since vLLM sizes KV from actual free VRAM.
ACTIVATION_OVERHEAD_GB = 4.0
# KV cache dtype bytes: vLLM uses BF16 KV cache by default even for FP8 models.
KV_DTYPE_BYTES = 2
# Hard ceiling on concurrent inputs — matches MAX_CONCURRENT_INPUTS in modal_vllm_worker.py
MAX_CONCURRENT = 32
# KV allowance used only by the missing-arch-constants fallback, where the real
# per-token KV size is unknown. Roughly one mid-length sequence.
FALLBACK_KV_RESERVE_GB = 4.0

# Efficient frontier, cheapest → most expensive. Dominated options are deliberately
# absent: A10 (same VRAM as L4, dearer), A100-40GB (less VRAM than L40S, dearer),
# H100 (same VRAM as A100-80GB, dearer — selection is capacity-only), RTX PRO 6000
# (96 GB but ~1.8 TB/s against H200's 4.8).
GPU_TIERS: list[dict[str, Any]] = [
    {"name": "L4", "vram_gb": 24, "enabled": True},
    {"name": "L40S", "vram_gb": 48, "enabled": True},
    {"name": "A100-80GB", "vram_gb": 80, "enabled": True},
    {"name": "H200", "vram_gb": 141, "enabled": True},
    {"name": "B200", "vram_gb": 192, "enabled": True},
    # Needs vLLM 0.19.0+: earlier builds hang on SM103 TRTLLM attention, where it
    # now falls back to FlashInfer.
    {"name": "B300", "vram_gb": 288, "enabled": True},
]


def _smallest_tier_holding_weights(model_cfg: dict[str, Any], tiers: list[dict[str, Any]]) -> str:
    """Only for when architecture constants are missing and the KV-aware calculation cannot run.
    Reserves a nominal one sequence of KV on top of the weights so the pick is not wrong by a
    whole tier; the true requirement may still be higher."""
    params_b = model_cfg.get("total_params_b") or 0
    weights_gb = params_b * (1 if model_cfg.get("fp8_supported", False) else 2)
    need = weights_gb + ACTIVATION_OVERHEAD_GB + FALLBACK_KV_RESERVE_GB
    for tier in tiers:
        if tier["vram_gb"] * GPU_MEMORY_UTILIZATION >= need:
            return tier["name"]
    return tiers[-1]["name"]


def select_gpu(
    model_cfg: dict[str, Any],
    max_model_len: int,
) -> tuple[str, int]:
    """Returns ``(gpu_type, max_concurrent_inputs)``, falling back to the model's static
    ``inference.gpu_type`` when architecture constants are missing.

    ``max_model_len`` includes both input and reserved output at inference time.
    """
    num_attn_layers = model_cfg.get("num_attn_layers")
    num_kv_heads = model_cfg.get("num_kv_heads")
    head_dim = model_cfg.get("head_dim")
    inference = model_cfg.get("inference") or {}
    minimum_vram = inference.get("min_vram_gb", 0)
    tiers = [t for t in GPU_TIERS if t["enabled"] and t["vram_gb"] >= minimum_vram]
    if not tiers:
        raise ValueError(f"No enabled GPU meets minimum VRAM {minimum_vram} GB")

    if not all([num_attn_layers, num_kv_heads, head_dim]):
        # The KV cache cannot be sized without them, but a flat default would pin a
        # 70B to a 24 GB card — at minimum refuse a GPU the weights alone overflow.
        fallback_gpu = inference.get("gpu_type")
        if not fallback_gpu or (minimum_vram and fallback_gpu not in {t["name"] for t in tiers}):
            fallback_gpu = _smallest_tier_holding_weights(model_cfg, tiers)
        logger.warning(
            "gpu_selector: missing arch constants for %s; falling back to gpu=%s",
            model_cfg.get("id"),
            fallback_gpu,
        )
        return fallback_gpu, MAX_CONCURRENT

    fp8 = model_cfg.get("fp8_supported", False)
    bytes_per_param = 1 if fp8 else 2
    weights_gb = model_cfg["total_params_b"] * bytes_per_param

    # KV cache bytes per token for full-attention layers only.
    kv_bytes_per_tok = 2 * num_attn_layers * num_kv_heads * head_dim * KV_DTYPE_BYTES
    min_kv_1seq_gb = (max_model_len * kv_bytes_per_tok) / (1024**3)

    for tier in tiers:
        usable = tier["vram_gb"] * GPU_MEMORY_UTILIZATION
        kv_budget = usable - weights_gb - ACTIVATION_OVERHEAD_GB
        if kv_budget < min_kv_1seq_gb:
            continue
        raw_concurrent = int(kv_budget / min_kv_1seq_gb)
        max_concurrent = max(1, min(MAX_CONCURRENT, raw_concurrent))
        logger.info(
            "gpu_selector: model=%s max_model_len=%d → gpu=%s concurrent=%d "
            "(weights=%.1fGB kv_budget=%.1fGB min_kv=%.2fGB)",
            model_cfg.get("id"),
            max_model_len,
            tier["name"],
            max_concurrent,
            weights_gb,
            kv_budget,
            min_kv_1seq_gb,
        )
        return tier["name"], max_concurrent

    raise InputValidationError(
        f"{model_cfg.get('id') or 'This model'} at {max_model_len:,} serving tokens "
        "exceeds available single-GPU capacity. Choose a smaller model or a shorter workload."
    )


if __name__ == "__main__":
    import json
    from pathlib import Path

    models_path = Path(__file__).parent / "models.json"
    all_models = json.loads(models_path.read_text())["models"]

    print(f"{'Model':<52} {'Backend':<8} {'Context':>8}  {'GPU':<12} {'Concurrent':>10}")
    print("-" * 100)
    for m in all_models:
        # Catalog-driven only: models without a finetuning context are skipped.
        ctx = (m.get("finetuning") or {}).get("context_length")
        if m.get("disabled") or not ctx:
            continue
        gpu, conc = select_gpu(m, ctx)
        static_gpu = (m.get("inference") or {}).get("gpu_type") or "?"
        changed = " ← was " + static_gpu if gpu != static_gpu else ""
        print(f"{m['id']:<52} {m['backend']:<8} {ctx:>8}  {gpu:<12} {conc:>10}{changed}")
