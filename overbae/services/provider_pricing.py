import asyncio
import math
from contextlib import suppress
from datetime import UTC, datetime

import modal
from asgiref.sync import async_to_sync
from django.core.cache import cache

CACHE_KEY = "modal-workspace-rates"
GPU_KEYS = {
    "A100": "a100_80gb",
    "A100-80GB": "a100_80gb",
    "A100-40GB": "a100_40gb",
    "A10": "a10g",
    "RTX-PRO-6000": "rtx6000",
}


def fetch_rates():
    async def fetch():
        return await asyncio.wait_for(modal.Workspace.from_context().billing.rates.aio(), timeout=8)

    return async_to_sync(fetch)()


def refresh_rates():
    try:
        rates = {
            key: float(value)
            for key, value in fetch_rates().items()
            if math.isfinite(float(value)) and float(value) >= 0
        }
        value = {"status": "current" if rates else "unavailable", "rates": rates}
    except Exception as exc:
        value = {"status": "unavailable", "rates": {}, "error_code": type(exc).__name__}
    value.update(
        source="modal.Workspace.billing.rates",
        fetched_at=datetime.now(UTC).isoformat(),
        cache_seconds=300,
    )
    # Rate discovery remains usable when the optional cache is unavailable.
    with suppress(Exception):
        cache.set(CACHE_KEY, value, timeout=300 if value["status"] == "current" else 30)
    return value


def current_rates():
    try:
        cached = cache.get(CACHE_KEY)
    except Exception:
        cached = None
    return cached or refresh_rates()


def gpu_rate(card, gpu_type):
    name = (gpu_type or "").strip()
    price = card["rates"].get("gpu_hour_cost_" + GPU_KEYS.get(name, name.lower()))
    return price / 3600 if price is not None else None
