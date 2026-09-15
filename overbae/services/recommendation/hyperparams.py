"""Hyperparameter defaults for a (dataset, model) pair, and one line of why per knob.

Every number is delegated to finetuning_policy, the module the Baseten runner gap-fills
from at submission time: the wizard's numbers ARE the training script's numbers.
"""

from __future__ import annotations

from typing import Any

from overbae.services.finetuning_policy import (
    baseten_context_length,
    default_epochs,
    openai_batch_size,
    qlora_learning_rate,
    qlora_lora_params,
)

from .catalog import catalog_backend


def compute_hyperparams(
    num_examples: int,
    *,
    model_entry: dict[str, Any] | None = None,
    use_lora: bool = True,
    max_row_tokens: int = 0,
) -> dict[str, Any]:
    """Data-size-adaptive hyperparameters for a specific model.

    On Baseten, ``context_length`` snaps up from the dataset's longest row: it is the
    training MAX_LENGTH, and rows over it would be truncated by TRL.
    """
    if num_examples <= 0:
        num_examples = 1

    # min(3, default_epochs) scales large datasets down (10k+ → 2, 50k+ → 1).
    n_epochs = (
        max(1, 100 // num_examples) if num_examples <= 100 else min(3, default_epochs(num_examples))
    )

    entry = model_entry or {}
    params_b: float = entry.get("total_params_b", 7.0)

    context_length: int | None = None
    if catalog_backend() == "baseten":
        # Modal (Unsloth) shares this policy — both self-hosted scripts truncate at
        # MAX_LENGTH the same way.
        model_max = entry.get("context_length_sft")
        context_length = baseten_context_length(
            max_row_tokens,
            model_max=int(model_max) if model_max else None,
        )

    batch_size = openai_batch_size(
        num_examples,
        min_batch=entry.get("min_batch_size", 1),
        max_batch=entry.get("max_batch_size", 8),
        use_lora=use_lora,
    )
    lr = qlora_learning_rate(params_b, num_examples, use_lora=use_lora)

    params: dict[str, Any] = {
        "n_epochs": n_epochs,
        "learning_rate": lr,
        "batch_size": batch_size,
        "warmup_ratio": 0.05,
    }
    if context_length is not None:
        params["context_length"] = context_length
    if use_lora:
        params["training_type"] = qlora_lora_params(params_b, num_examples)

    return params


def hyperparam_provenance(
    hyperparams: dict[str, Any],
    *,
    num_examples: int,
    params_b: float,
    use_lora: bool,
    backend: str,
) -> dict[str, str]:
    """One-line "why" per stamped knob, keyed by knob name — same language as the
    runtime policy's ``reasons`` dict, so pre-launch provenance matches post-launch.
    """
    reasons: dict[str, str] = {}
    n_epochs = int(hyperparams["n_epochs"])
    epochs_word = f"{n_epochs} epoch{'s' if n_epochs != 1 else ''}"
    if num_examples >= 10_000:
        reasons["n_epochs"] = (
            f"{num_examples} examples → {epochs_word} "
            "(large dataset: each example is seen plenty in few passes)"
        )
    else:
        reasons["n_epochs"] = (
            f"{num_examples} examples → {epochs_word} (≈100 example-visits heuristic)"
        )

    lr = hyperparams["learning_rate"]
    if not use_lora:
        reasons["learning_rate"] = f"{lr:g} — full fine-tune rate for a {params_b:g}B model"
    else:
        reasons["learning_rate"] = (
            f"{lr:g} — QLoRA heuristic for a {params_b:g}B model on {num_examples} examples"
        )

    batch = int(hyperparams["batch_size"])
    approx_steps = max(1, num_examples // batch) * n_epochs
    reasons["batch_size"] = (
        f"batch {batch} for {num_examples} examples → ~{approx_steps:,} optimizer steps "
        f"(vs {num_examples * n_epochs:,} at batch 1); clamped to the model's bounds"
    )
    if hyperparams.get("warmup_ratio"):
        reasons["warmup_ratio"] = (
            f"{hyperparams['warmup_ratio']:g} of steps ramp the LR before decay"
        )
    lora_r = (hyperparams.get("training_type") or {}).get("lora_r")
    if lora_r:
        reasons["lora_r"] = f"rank {lora_r} — QLoRA rank sweep for {num_examples} examples"
    return reasons
