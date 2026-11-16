"""Demo projects finish a training run without a provider, a judge or a charge.

Numbers here are generated, never measured, so every run says so in its name and its provider,
and the Console shows it. A project opts in with ``settings["simulated"] = True``; only
``seed_observability`` sets it. Real projects never reach this module.
"""

from __future__ import annotations

import hashlib
import random
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from overbae.models import FinetuningJob, FinetuningJobEval

PROVIDER = "simulated"
STEPS = 200
SAMPLE_EVERY = 10
BASELINE_SCORE = (0.62, 0.74)
IMPROVEMENT = (0.1, 0.18)


def is_simulated(project) -> bool:
    return bool((project.settings or {}).get("simulated"))


def refuse_paid_work(project) -> None:
    """Evaluations and optimiser runs call real models, so a demo project doesn't start them."""
    if is_simulated(project):
        raise ValidationError(
            {"detail": "This demo project uses simulated results. Start this in a real project."}
        )


def finish_simulated(job: FinetuningJob) -> None:
    rng = random.Random(hashlib.sha256(str(job.pk).encode()).hexdigest())
    now = timezone.now()
    history, evals = [], []
    start_loss = rng.uniform(1.7, 2.2)
    end_loss = rng.uniform(0.35, 0.55)
    for step in range(SAMPLE_EVERY, STEPS + 1, SAMPLE_EVERY):
        fraction = step / STEPS
        loss = round(end_loss + (start_loss - end_loss) * (1 - fraction) ** 2, 4)
        accuracy = round(0.55 + 0.38 * fraction, 4)
        history.append(
            {
                "step": step,
                "epoch": round(fraction * 3, 2),
                "train_loss": loss,
                "token_accuracy": accuracy,
                "lr": round(2e-4 * (1 - fraction) + 1e-6, 8),
                "grad_norm": round(1.4 * (1.2 - fraction), 3),
            }
        )
        evals.append(
            {
                "step": step,
                "epoch": round(fraction * 3, 2),
                "eval_loss": round(loss + 0.08, 4),
                "eval_token_accuracy": round(accuracy - 0.02, 4),
            }
        )
    metrics = {
        "loss": [
            {"step": row["step"], "train_loss": row["train_loss"], "eval_loss": check["eval_loss"]}
            for row, check in zip(history, evals, strict=True)
        ],
        "learning_rate": [{"step": row["step"], "value": row["lr"]} for row in history],
        "grad_norm": [{"step": row["step"], "value": row["grad_norm"]} for row in history],
        "token_accuracy": [
            {
                "step": row["step"],
                "train": row["token_accuracy"],
                "eval": check["eval_token_accuracy"],
            }
            for row, check in zip(history, evals, strict=True)
        ],
    }
    baseline = round(rng.uniform(*BASELINE_SCORE), 3)
    final = round(baseline + rng.uniform(*IMPROVEMENT), 3)
    output = f"simulated-{str(job.pk)[:8]}"
    rows = [
        FinetuningJobEval.objects.create(
            job=job,
            kind=kind,
            status=FinetuningJobEval.Status.COMPLETED,
            model_id=model,
            aggregate_score=score,
            baseline_delta=delta,
        )
        for kind, model, score, delta in (
            (FinetuningJobEval.Kind.BASELINE, job.baseline_model or job.base_model, baseline, None),
            (FinetuningJobEval.Kind.FINAL, output, final, round(final - baseline, 3)),
        )
        if job.eval_set_id
    ]
    last, last_eval = history[-1], evals[-1]
    FinetuningJob.objects.filter(pk=job.pk).update(
        name=f"{job.name} (simulated)"[:255],
        status=FinetuningJob.Status.SUCCEEDED,
        provider=PROVIDER,
        output_model_name=output,
        cost_usd=Decimal("0"),
        billed_minutes=0,
        started_at=now - timedelta(minutes=18),
        completed_at=now,
        progress={
            "percent": 100,
            "phase": "finalizing",
            "provider_status": "succeeded",
            "trained_steps": STEPS,
            "total_steps": STEPS,
            "epochs_completed": 3,
            "elapsed_seconds": 18 * 60,
            "eta_seconds": None,
            "train_loss": last["train_loss"],
            "eval_loss": last_eval["eval_loss"],
            "latest_train_loss": last["train_loss"],
            "latest_eval_loss": last_eval["eval_loss"],
            "token_accuracy": last["token_accuracy"],
            "eval_token_accuracy": last_eval["eval_token_accuracy"],
            "learning_rate": last["lr"],
            "metrics_history": history,
            "eval_history": evals,
            "activity": [],
            "metrics": metrics,
            "checkpoints": [],
            "judge_evals": [
                {
                    "id": str(row.pk),
                    "kind": row.kind,
                    "status": row.status,
                    "checkpoint_id": None,
                    "checkpoint_step": None,
                    "model_id": row.model_id,
                    "aggregate_score": row.aggregate_score,
                    "baseline_delta": row.baseline_delta,
                }
                for row in rows
            ],
        },
        result={"model": output, "metrics": metrics, "checkpoints": [], "epoch_losses": []},
    )
