"""Fine-tuning cost and duration estimation.

    Modal:    cost = GPU count × estimated seconds × per-GPU-second rate
    trained_tokens = dataset_tokens × n_epochs

Where no published rate exists the cost is an honest ``None``, never a fabricated
number. Duration is first-principles: training FLOPs (6·N·D full, ≈4·N·D LoRA)
over sustained GPU throughput (peak BF16 × 35% MFU), plus provisioning overhead.
"""

from __future__ import annotations

import math
from typing import Any

# Source: https://modal.com/pricing (retrieved 2026-10-02). GPU compute only;
# CPU, host memory, storage and provider rate multipliers are additional.
_MODAL_H100_USD_PER_SECOND = 0.001097
# Mirrors ModalRunner._GPU_TABLE (finetuning_runner.py): LoRA SFT on
# (max_params_b, gpu_count) H100s. ≤72B is 1× via QLoRA.
_GPU_COUNT_TIERS: list[tuple[float, int]] = [
    (32.0, 1),
    (72.0, 1),
    (float("inf"), 4),
]


def _gpu_count(total_params_b: float) -> int:
    for max_params, count in _GPU_COUNT_TIERS:
        if total_params_b <= max_params:
            return count
    return 4


# Peak dense BF16 from the NVIDIA H100 SXM datasheet.
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
    if backend != "modal" or trained_tokens <= 0:
        return None
    gpu_count = _gpu_count(total_params_b)
    time_s = estimate_training_time_s(
        trained_tokens, total_params_b=total_params_b, use_lora=use_lora
    )
    return {
        "usd": round(time_s * _MODAL_H100_USD_PER_SECOND * gpu_count, 4),
        "trained_tokens": trained_tokens,
        "gpu_type": "H100",
        "gpu_count": gpu_count,
        "billed_minutes": time_s / 60,
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
