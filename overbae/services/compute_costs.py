import math

from overbae.services import provider_pricing


def nonnegative(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def estimate_usage(usages, *, recorded_gpu=False):
    card = provider_pricing.current_rates()
    unique, missing = {}, set()
    for usage in usages:
        if (
            not isinstance(usage, dict)
            or not usage.get("usage_id")
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
