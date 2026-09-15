"""Crude GPU-time cost estimate for self-hosted inference on Modal.


Unknown gpu_type -> None (honest absence, not 0).
"""

from __future__ import annotations

# Modal GPU list price, USD per hour. Source: https://modal.com/pricing
_GPU_USD_PER_HOUR: dict[str, float] = {
    "L4": 0.80,
    "L40S": 1.95,
    "A100": 2.50,
    "A100-80GB": 2.50,
    "H100": 3.95,
    "H200": 4.54,
    "B200": 6.25,
    "B300": 7.50,
}


def gpu_usd_per_second(gpu_type: str) -> float | None:
    per_hour = _GPU_USD_PER_HOUR.get((gpu_type or "").strip())
    if per_hour is None:
        return None
    return per_hour / 3600.0


def estimate_call_cost(gpu_type: str, generation_time_ms: float | None) -> float | None:
    rate = gpu_usd_per_second(gpu_type)
    if rate is None or not generation_time_ms:
        return None
    return round((generation_time_ms / 1000.0) * rate, 6)
