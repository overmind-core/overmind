from __future__ import annotations

from overbae.services import provider_pricing


def gpu_usd_per_second(gpu_type: str) -> float | None:
    return provider_pricing.gpu_rate(provider_pricing.current_rates(), gpu_type)


def estimate_call_cost(gpu_type: str, generation_time_ms: float | None) -> float | None:
    rate = gpu_usd_per_second(gpu_type)
    if rate is None or not generation_time_ms:
        return None
    return round((generation_time_ms / 1000.0) * rate, 6)
