import math
from collections import Counter
from types import SimpleNamespace

from overbae.models import FinetuningJob
from overbae.services import provider_pricing, training_release
from overbae.services.finetuning_runner import ModalRunner
from overbae.services.recommendation.candidates import dataset_total_tokens

RECIPE_FIELDS = (
    "objective",
    "context_length",
    "batch_size",
    "training_type",
    "packing",
    "padded_token_budget",
    "load_in_4bit",
    "gradient_checkpointing",
    "lora_r",
    "lora_alpha",
    "lora_target_modules",
    "gradient_accumulation_steps",
    "checkpoint_policy",
    "pre_training_baseline",
)


def hardware(model, recipe):
    return ModalRunner()._select_training_gpu(
        SimpleNamespace(base_model=model, hyperparameters=recipe),
        context_length=int(recipe.get("context_length") or 0),
    )


def candidates(project_id, model):
    return (
        FinetuningJob.objects.filter(
            project_id=project_id, base_model=model, provider="modal", status="succeeded"
        )
        .select_related("cell")
        .order_by("-completed_at")[:30]
    )


def forecast(project_id, model, recipe, *, tokens, stats):
    gpu, count = hardware(model, recipe)
    durations, evidence = [], []
    rejected = Counter()
    release = training_release.current()
    for job in candidates(project_id, model):
        previous_runtime = (job.requested_configuration or {}).get("runtime", {})
        if any(
            previous_runtime.get(key) != release.get(key)
            for key in ("training", "processor", "data_format", "environment")
        ):
            rejected["runtime"] += 1
            continue
        if any(
            job.hyperparameters.get(key, True if key == "pre_training_baseline" else None)
            != recipe.get(key, True if key == "pre_training_baseline" else None)
            for key in RECIPE_FIELDS
        ):
            rejected["recipe"] += 1
            continue
        recorded = job.effective_configuration or {}
        if (recorded.get("gpu_type"), recorded.get("gpu_count")) != (gpu, count):
            rejected["hardware"] += 1
            continue
        prior = (job.cell.stats if job.cell else {}) or {}
        profile_fields = ("avg_input_chars", "avg_output_chars")
        if not prior.get("avg_input_chars") or any(
            not isinstance(stats.get(key), (int, float))
            or not isinstance(prior.get(key), (int, float))
            or not math.isfinite(stats[key])
            or not math.isfinite(prior[key])
            or (stats[key] != 0 if prior[key] == 0 else not 0.8 <= stats[key] / prior[key] <= 1.25)
            for key in profile_fields
        ):
            rejected["profile"] += 1
            continue
        progress = job.progress or {}
        if (
            not progress.get("total_steps")
            or progress.get("trained_steps") != progress["total_steps"]
        ):
            rejected["incomplete"] += 1
            continue
        start, end = getattr(job, "started_at", None), getattr(job, "completed_at", None)
        seconds = (end - start).total_seconds() if start and end else None
        if not seconds or not math.isfinite(seconds) or seconds <= 0:
            rejected["duration"] += 1
            continue
        previous_tokens = dataset_total_tokens(prior) * (job.hyperparameters.get("n_epochs") or 1)
        if not previous_tokens or not 0.5 <= tokens / previous_tokens <= 2:
            rejected["workload_scale"] += 1
            continue
        scaled_seconds = seconds * tokens / previous_tokens
        durations.append(scaled_seconds)
        evidence.append(
            {
                "job": str(job.id),
                "elapsed_seconds": seconds,
                "source_estimated_tokens": previous_tokens,
                "scaled_seconds": scaled_seconds,
            }
        )
    # This is an explicit planning margin, not a statistical confidence interval.
    lower, upper = (0.5, 1.5) if len(evidence) < 3 else (0.8, 1.3)
    duration = [min(durations) * lower, max(durations) * upper] if durations else None
    card = provider_pricing.current_rates()
    price = provider_pricing.gpu_rate(card, gpu)
    blockers = ([] if duration else ["duration_unmeasured"]) + (
        [] if price is not None else ["gpu_rate_unavailable"]
    )
    return {
        "basis": "matched_measurements" if duration else "unmeasured_recipe",
        "gpu_type": gpu,
        "gpu_count": count,
        "rate_card": card,
        "price_status": card["status"] if price is not None else "unavailable",
        "gpu_hour_usd": price * 3600 * count if price is not None else None,
        "duration_status": "estimated" if duration else "unmeasured",
        "estimate_blockers": blockers,
        "trained_tokens": tokens,
        "training_seconds": duration,
        "training_gpu_usd": [round(t * count * price, 4) for t in duration]
        if duration and price is not None
        else None,
        "evidence_jobs": [item["job"] for item in evidence],
        "evidence": evidence,
        "rejected_measurements": dict(rejected),
        "confidence": "unmeasured" if not evidence else "low" if len(evidence) < 3 else "moderate",
        "planning_margin": {"lower_multiplier": lower, "upper_multiplier": upper},
        "all_in_usd": None,
        "runtime": release,
        "measurement_window": "completed_gpu_execution_including_load_validation_and_reload",
        "token_basis": "source_character_estimate_for_both_requested_and_evidence_workloads",
        "budget_enforcement": "none",
        "unpriced_components": [
            "preparation",
            "training_cpu_memory",
            "native_final_calibration",
            "storage",
        ],
        "limitations": [
            "Matched model, training/processor identity, hardware, recipe and character-length profile. Completed execution is scaled within 0.5–2x source-estimated workload; validation mix and length tails can still differ.",
            "Observed range with an explicit planning margin, not a statistical confidence interval or spend cap.",
            "Queue time and future provider price changes are not bounded.",
        ]
        if duration
        else [
            "A completed matching recipe measurement is required before this objective has a calibrated duration or GPU quote."
        ],
    }
