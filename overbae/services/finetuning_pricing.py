"""Fine-tuning cost and duration estimation.

    Together: cost = trained_tokens × price_per_million / 1e6
    Baseten:  cost = GPU count × billed minutes × per-GPU-minute rate
    trained_tokens = dataset_tokens × n_epochs

Where no published rate exists the cost is an honest ``None``, never a fabricated
number. Duration is first-principles: training FLOPs (6·N·D full, ≈4·N·D LoRA)
over sustained GPU throughput (peak BF16 × 35% MFU), plus provisioning overhead.
"""

from __future__ import annotations

import math
from typing import Any

# Source: https://www.together.ai/pricing (retrieved 2026-07-13).
# $/1M trained tokens, tiered by base-model total parameter count.
# (max_params_b, lora_price, full_price)
_TOGETHER_SFT_TIERS: list[tuple[float, float, float]] = [
    (16.0, 0.48, 0.54),
    (69.0, 1.50, 1.65),
    (100.0, 2.90, 3.20),
]
# Together applies a $4.00 minimum charge per fine-tuning job (same source).
_TOGETHER_MIN_CHARGE_USD = 4.00

# Source: https://www.baseten.co/pricing/ (retrieved 2026-07-17). Baseten bills
# training per GPU-minute of active compute (H100 80GB = $0.10833/min) — there is
# no per-token rate, so cost = GPU count × minutes × rate.
_BASETEN_H100_USD_PER_MIN = 0.10833
# Mirrors BasetenRunner._GPU_TABLE (finetuning_runner.py): LoRA SFT on
# (max_params_b, gpu_count) H100s. ≤72B is 1× via QLoRA.
_BASETEN_GPU_COUNT_TIERS: list[tuple[float, int]] = [
    (32.0, 1),
    (72.0, 1),
    (float("inf"), 4),
]


def _baseten_gpu_count(total_params_b: float) -> int:
    for max_params, count in _BASETEN_GPU_COUNT_TIERS:
        if total_params_b <= max_params:
            return count
    return 4


# Peak dense BF16 from the NVIDIA H100 SXM datasheet; Together's fine-tuning
# service runs on H100 clusters (docs.together.ai).
_H100_PEAK_BF16_TFLOPS = 989.0
# Typical model-FLOPs-utilisation for managed SFT pipelines (~30–40%).
_ASSUMED_MFU = 0.35
# Queueing + provisioning + upload before compute starts (assumed, matches the
# lead time observed on managed fine-tuning jobs).
_FIXED_OVERHEAD_S = 10 * 60

# Standard 6·N·D rule: forward 2 + backward 4. LoRA skips frozen-weight gradients,
# leaving forward 2 + activation-gradient backward 2 ≈ 4·N·D.
_FLOPS_PER_PARAM_TOKEN_FULL = 6.0
_FLOPS_PER_PARAM_TOKEN_LORA = 4.0


def training_price_per_million(
    total_params_b: float,
    *,
    use_lora: bool,
    backend: str,
) -> float | None:
    """$/1M trained tokens for SFT, or None when unpriced. Baseten bills per
    GPU-minute rather than per token — see estimate_training_cost.
    """
    if backend != "together":
        return None
    for max_params, lora_price, full_price in _TOGETHER_SFT_TIERS:
        if total_params_b <= max_params:
            return lora_price if use_lora else full_price
    return None  # >100B models are individually priced; none are in our catalog


def estimate_training_cost(
    trained_tokens: int,
    *,
    total_params_b: float,
    use_lora: bool,
    backend: str,
) -> dict[str, Any] | None:
    """Cost breakdown, or None when the backend is unpriced. ``trained_tokens`` is
    dataset tokens × epochs — the quantity providers bill.
    """
    if backend in ("baseten", "modal"):
        # Modal's per-second H100 rate differs slightly from Baseten's; this stays a
        # same-order-of-magnitude wizard estimate, not a billing reconciliation
        # (Modal has no cost-sync job, unlike Baseten's baseten_billing_sync).
        if trained_tokens <= 0:
            return None
        gpu_count = _baseten_gpu_count(total_params_b)
        time_s = estimate_training_time_s(
            trained_tokens, total_params_b=total_params_b, use_lora=use_lora
        )
        billed_minutes = math.ceil(time_s / 60)
        return {
            "usd": round(billed_minutes * gpu_count * _BASETEN_H100_USD_PER_MIN, 4),
            "price_per_million_usd": None,
            "trained_tokens": trained_tokens,
            "minimum_applied": False,
            "gpu_type": "H100",
            "gpu_count": gpu_count,
            "billed_minutes": billed_minutes,
        }
    price = training_price_per_million(
        total_params_b,
        use_lora=use_lora,
        backend=backend,
    )
    if price is None or trained_tokens <= 0:
        return None
    raw_usd = trained_tokens * price / 1_000_000
    minimum_applied = backend == "together" and raw_usd < _TOGETHER_MIN_CHARGE_USD
    usd = _TOGETHER_MIN_CHARGE_USD if minimum_applied else raw_usd
    return {
        # 4 dp so small datasets don't round to a misleading $0.00.
        "usd": round(usd, 4),
        "price_per_million_usd": price,
        "trained_tokens": trained_tokens,
        "minimum_applied": minimum_applied,
    }


def estimate_training_time_s(trained_tokens: int, *, total_params_b: float, use_lora: bool) -> int:
    if trained_tokens <= 0:
        return _FIXED_OVERHEAD_S
    flops_per_token = _FLOPS_PER_PARAM_TOKEN_LORA if use_lora else _FLOPS_PER_PARAM_TOKEN_FULL
    total_flops = flops_per_token * total_params_b * 1e9 * trained_tokens
    sustained_flops = _H100_PEAK_BF16_TFLOPS * 1e12 * _ASSUMED_MFU
    return _FIXED_OVERHEAD_S + math.ceil(total_flops / sustained_flops)


def humanize_duration(seconds: int) -> str:
    if seconds < 60:
        return "<1 min"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes:02d} min"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h"


def estimate_training_run(
    *,
    dataset_tokens: int,
    n_epochs: int,
    total_params_b: float,
    use_lora: bool,
    backend: str,
) -> dict[str, Any]:
    epochs = max(1, int(n_epochs))
    trained_tokens = max(0, int(dataset_tokens)) * epochs
    time_s = estimate_training_time_s(
        trained_tokens, total_params_b=total_params_b, use_lora=use_lora
    )
    return {
        "cost_estimate": estimate_training_cost(
            trained_tokens,
            total_params_b=total_params_b,
            use_lora=use_lora,
            backend=backend,
        ),
        "time_estimate": {
            "seconds": time_s,
            "human": humanize_duration(time_s),
        },
        "trained_tokens": trained_tokens,
    }
