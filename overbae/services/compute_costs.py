import math
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from overbae.models import BillingService, FinetuningJob
from overbae.services import provider_pricing
from overbae.services.billing_ledger import charge_credits


def nonnegative(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def unique_usage(usages):
    unique, missing = {}, set()
    if not isinstance(usages, list):
        return {}, {"worker_usage"}
    for usage in usages:
        if (
            not isinstance(usage, dict)
            or not isinstance(usage.get("usage_id"), str)
            or not usage["usage_id"]
            or not nonnegative(usage.get("elapsed_seconds"))
        ):
            missing.add("worker_usage")
            continue
        key = usage["usage_id"]
        previous = unique.get(key)
        if previous and any(previous.get(k) != usage.get(k) for k in ("gpu_type", "gpu_count")):
            missing.add("conflicting_worker_identity")
        if previous is None or usage["elapsed_seconds"] >= previous["elapsed_seconds"]:
            unique[key] = usage
    if not unique:
        missing.add("worker_usage")
    return unique, missing


def estimate_usage(usages, *, recorded_gpu=False):
    card = provider_pricing.current_rates()
    unique, missing = unique_usage(usages)
    amounts = {"gpu": 0.0, "cpu": 0.0, "memory": 0.0}
    for usage in unique.values():
        count = usage.get("gpu_count")
        rate = provider_pricing.gpu_rate(card, usage.get("gpu_type"))
        if not recorded_gpu:
            if nonnegative(count) and (count == 0 or rate is not None):
                amounts["gpu"] += usage["elapsed_seconds"] * count * (rate or 0)
            else:
                missing.add("gpu")
        for component, key, price in (
            ("cpu", "cpu_core_seconds", card["rates"].get("cpu_hour_cost")),
            ("memory", "memory_gib_seconds", card["rates"].get("mem_gib_hour_cost")),
        ):
            if nonnegative(usage.get(key)) and nonnegative(price):
                amounts[component] += usage[key] * price / 3600
            else:
                missing.add(component)
    known = sum(amounts.values())
    return {
        "estimated_usd": None if missing else known,
        "known_estimate_usd": known,
        "components_usd": amounts,
        "unique_measurements": len(unique),
        "unmeasured_components": sorted(missing),
        "gpu_covered_by_ledger": recorded_gpu,
        "all_in_actual_usd": None,
        "rate_card": card,
        "basis": "worker wall time, reserved/observed CPU and peak memory proxies; cumulative worker observations counted once",
        "exclusions": [
            "image_boot",
            "post_measurement_idle",
            "storage",
            "network",
            "local_orchestration",
            "provider_discounts_or_premiums",
            "attempts_without_receipts",
        ],
    }


def gpu_charge(usages):
    unique, missing = unique_usage(usages)
    if missing:
        return None
    card = provider_pricing.current_rates()
    measurements = []
    for usage in unique.values():
        count = usage.get("gpu_count")
        if type(count) is not int or count < 0:
            return None
        if count == 0:
            continue
        rate = provider_pricing.gpu_rate(card, usage.get("gpu_type"))
        if not nonnegative(rate):
            return None
        measurements.append(
            {
                "usage_id": usage["usage_id"],
                "gpu_type": usage["gpu_type"],
                "gpu_count": count,
                "seconds": usage["elapsed_seconds"],
                "usd_per_second": rate,
                "usd": usage["elapsed_seconds"] * count * rate,
            }
        )
    if not measurements:
        return None
    return {
        "basis": "recorded_worker_gpu_seconds",
        "measurements": measurements,
        "cost_usd": round(sum(item["usd"] for item in measurements), 4),
        "rate_source": card["source"],
        "rates_fetched_at": card["fetched_at"],
        "exclusions": [
            "unreported_worker_time",
            "image_boot",
            "idle",
            "cpu",
            "memory",
            "storage",
            "network",
        ],
    }


def record_training_charge(job):
    if job.provider != "modal" or job.cost_synced_at is not None:
        return
    receipt = gpu_charge((job.progress or {}).get("compute_usage", []))
    if receipt is None or receipt["cost_usd"] <= 0:
        return
    cost = Decimal(str(receipt["cost_usd"]))
    with transaction.atomic():
        current = (
            FinetuningJob.objects.select_related("triggered_by")
            .select_for_update(of=("self",))
            .get(pk=job.pk)
        )
        if current.cost_synced_at is not None:
            return
        if current.triggered_by_id:
            charge_credits(
                current.triggered_by,
                cost,
                BillingService.FINETUNING_JOB,
                project_id=current.project_id,
                idempotency_key=f"finetuning-job:{current.pk}",
                metadata={"job_id": str(current.pk), "provider": "modal", **receipt},
            )
        current.cost_usd, current.cost_synced_at = cost, timezone.now()
        current.result = {**(current.result or {}), "compute_charge": receipt}
        current.save(update_fields=["cost_usd", "cost_synced_at", "result", "updated_at"])
    job.refresh_from_db()


def training_cost(job):
    progress = job.progress or {}
    recorded = float(job.cost_usd) if job.cost_usd is not None else None
    training = estimate_usage(progress.get("compute_usage", []), recorded_gpu=recorded is not None)
    preparation = estimate_usage(
        [(progress.get("preparation", {}).get("report") or {}).get("compute_usage", {})]
    )
    estimates = [training["estimated_usd"], preparation["estimated_usd"]]
    return {
        "recorded_training_usd": recorded,
        "recorded_at": job.cost_synced_at.isoformat() if job.cost_synced_at else None,
        "recorded_basis": (job.result or {}).get("compute_charge") or None,
        "coverage": "training_gpu" if job.provider == "modal" else "provider_training",
        "training_compute": training,
        "preparation_compute": preparation,
        "recorded_plus_estimated_usd": recorded + sum(estimates)
        if recorded is not None and all(value is not None for value in estimates)
        else None,
        "unreported_components": [
            "evaluation_and_inference_reported_separately",
            "storage",
            "network",
            "local_orchestration",
            "provider_billing_adjustments",
        ],
        "all_in_actual_usd": None,
        "budget_enforcement": "none",
    }
