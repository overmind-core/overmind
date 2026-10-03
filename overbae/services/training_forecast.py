import math
from types import SimpleNamespace

from overbae.models import FinetuningJob
from overbae.services.finetuning_runner import ModalRunner
from overbae.services.inference_pricing import gpu_usd_per_second

RECIPE_FIELDS = ("objective", "context_length", "batch_size", "training_type", "packing")


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
    rates, evidence = [], []
    for job in candidates(project_id, model):
        if any(job.hyperparameters.get(key) != recipe.get(key) for key in RECIPE_FIELDS):
            continue
        recorded = job.effective_configuration or {}
        if (recorded.get("gpu_type"), recorded.get("gpu_count")) != (gpu, count):
            continue
        prior = (job.cell.stats if job.cell else {}) or {}
        length, previous = stats.get("p95_token_length"), prior.get("p95_token_length")
        if not length or not previous or not 0.8 <= length / previous <= 1.25:
            continue
        rate = (job.progress or {}).get("tokens_per_second")
        if type(rate) in {int, float} and math.isfinite(rate) and rate > 0:
            rates.append(rate)
            evidence.append(str(job.id))
    # This is an explicit planning margin, not a statistical confidence interval.
    duration = (
        [tokens / max(rates) * 0.8, tokens / min(rates) * 1.3] if rates and tokens > 0 else None
    )
    price = gpu_usd_per_second(gpu)
    return {
        "basis": "matched_measurements" if duration else "unmeasured_recipe",
        "gpu_type": gpu,
        "gpu_count": count,
        "trained_tokens": tokens,
        "training_seconds": duration,
        "training_gpu_usd": [round(t * count * price, 4) for t in duration]
        if duration and price
        else None,
        "evidence_jobs": evidence,
        "planning_margin": {"lower_multiplier": 0.8, "upper_multiplier": 1.3},
        "all_in_usd": None,
        "budget_enforcement": "none",
        "unpriced_components": [
            "preparation",
            "training_cpu_memory",
            "validation",
            "native_final_calibration",
            "storage",
        ],
        "limitations": [
            "Matched model, objective, hardware, recipe and approximate length profile; observed sample range with planning margin, not a confidence interval.",
            "Queue time and future provider price changes are not bounded.",
        ]
        if duration
        else [
            "A completed matching recipe measurement is required before this objective has a calibrated duration or GPU quote."
        ],
    }
