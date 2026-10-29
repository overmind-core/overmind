"""GPUs a ready model can dedicate, with a per-GPU hourly price and any measured speed."""

from __future__ import annotations

from decimal import Decimal

from modal_shared.stacks import WORKER_CPU, WORKER_MEMORY_MIB
from overbae.modal.gpu_selector import DEDICATED_TIERS, dedicated_gpu_names, gpu_vram_gb
from overbae.modal.model_registry import get_model_config_any_backend
from overbae.services.billing_ledger import CREDITS_PER_USD
from overbae.services.provider_pricing import gpu_rate
from overbae.services.serving_benchmarks import measurement

SHAPES = ("lora_bf16", "merged_fp8", "decision")


def serving_shape(deployed) -> str:
    if deployed.inference_contract == "decision":
        return "decision"
    if deployed.is_lora:
        return "lora_bf16"
    return "merged_fp8"


def gpu_names_for(deployed) -> list[str]:
    cfg = get_model_config_any_backend(deployed.base_model_id) or {}
    fp8 = serving_shape(deployed) == "merged_fp8" and bool(cfg.get("fp8_supported", False))
    if cfg:
        return dedicated_gpu_names(cfg, deployed.max_model_len, fp8=fp8)
    floor = gpu_vram_gb(deployed.gpu_type) or gpu_vram_gb("L4")
    return [tier["name"] for tier in DEDICATED_TIERS if tier["vram_gb"] >= floor]


def hourly_usd(card: dict, gpu_type: str) -> Decimal | None:
    """GPU plus the CPU and memory this worker reserves. None when any rate is missing."""
    if card.get("status") != "current":
        return None
    rates = card.get("rates") or {}
    per_second = gpu_rate(card, gpu_type)
    cpu = rates.get("cpu_hour_cost")
    memory = rates.get("mem_gib_hour_cost")
    mebibytes = WORKER_MEMORY_MIB.get(gpu_type)
    if per_second is None or cpu is None or memory is None or mebibytes is None:
        return None
    gpu_hour = Decimal(str(per_second)) * Decimal(3600)
    cpu_hour = Decimal(str(cpu)) * Decimal(WORKER_CPU)
    mem_hour = Decimal(str(memory)) * (Decimal(mebibytes) / Decimal(1024))
    return gpu_hour + cpu_hour + mem_hour


def credits_per_hour(amount_usd: Decimal | None) -> Decimal | None:
    if amount_usd is None:
        return None
    return (amount_usd * CREDITS_PER_USD).quantize(Decimal("0.01"))


def offers(deployed, card: dict) -> list[dict]:
    shape = serving_shape(deployed)
    rows = []
    for gpu_type in gpu_names_for(deployed):
        measured = measurement(deployed.base_model_id, shape, gpu_type) or {}
        price = hourly_usd(card, gpu_type)
        rows.append(
            {
                "gpu_type": gpu_type,
                "credits_per_gpu_hour": credits_per_hour(price),
                "rates_fetched_at": card.get("fetched_at"),
                "ttft_ms_p50": measured.get("ttft_ms_p50") if shape != "decision" else None,
                "tokens_per_second_p50": measured.get("tokens_per_second_p50")
                if shape != "decision"
                else None,
                "latency_ms_p50": measured.get("latency_ms_p50"),
            }
        )
    return rows
