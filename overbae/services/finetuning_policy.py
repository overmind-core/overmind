"""Training policy: every fine-tuning heuristic in one module.

Shared by the recommender (pre-launch wizard) and the Baseten runner (submission
time) so the values the wizard shows are the values the training script receives —
no silent recommendation → job → config drops.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Epochs when unset: enough example-visits that tiny datasets get real optimizer-step
# counts (49 rows × 2 epochs still improved on every batch when the LR hit zero).
TARGET_EXAMPLE_VISITS = 300
MIN_EPOCHS = 3
MAX_EPOCHS = 10
# Large corpora see each example plenty in one pass — scale epochs DOWN to keep total
# steps sane (19,704 rows × 3 epochs at batch 1 was a 59k-step run).
LARGE_DATASET_ROWS = 10_000  # ≥ this → 2 epochs
HUGE_DATASET_ROWS = 50_000  # ≥ this → 1 epoch

# Fixed training seed so re-runs are reproducible (matches finetuning_split's
# deterministic corpus split).
TRAINING_SEED = 42

# Packing pays off when padding waste is large AND the dataset is big enough
# that fewer optimizer steps don't starve learning: rows must average ≤ 1/4 of
# the context window, on ≥1000 examples.
PACKING_MIN_EXAMPLES = 1000
PACKING_MAX_FILL = 0.25

# Baseten training GPUs (VRAM in GB) — hardware facts for the pre-submission
# memory guardrail, mirroring BasetenRunner's GPU table.
TRAINING_GPU_VRAM_GB: dict[str, float] = {"H100": 80.0, "H200": 141.0}
_BF16_BYTES_PER_PARAM = 2
# QLoRA 4-bit weight floor (Unsloth docs: 70B QLoRA ≈41 GB ≈0.59 B/param with
# adapters; 0.5 is the raw nf4 weight size used for the pre-submit guardrail).
_QLORA_BYTES_PER_PARAM = 0.5
# Full FT trains every parameter: bf16 weights (2) + bf16 grads (2) + paged
# 8-bit AdamW states (~2) per param — matches train.py's optim choice.
FULL_FT_BYTES_PER_PARAM = 6
# Headroom for activations/LoRA/optimizer on top of frozen weights: 0.95 left 35B MoE
# bf16 under 1×H100 with <10 GB free. 0.85 forces QLoRA from ~34B, 32B dense stays bf16.
_VRAM_UTIL_FRACTION = 0.85

# Activation cost per training token, per unit of hidden size, for the Unsloth
# LoRA path (FlashAttention + offloaded gradient checkpointing + fused CE +
# Tiled MLP). Re-fit via scripts/calibrate_activation_budget.py --fit after
# long-context engine changes. Llama-3.1-8B longctx slope ≈35–38 B/tok/hidden
# (2026-08); keep 50 until the worst-family slope (currently ~70 from 32B
# probes) is remeasured under the same stack.
ACTIVATION_BYTES_PER_TOKEN_PER_HIDDEN = 50
# Spend only 70% of computed headroom until our own calibration is trusted.
_ACTIVATION_SAFETY = 0.7
MAX_MICRO_BATCH = 8
_BYTES_PER_GB = 1024**3


def max_token_budget(
    params_b: float,
    hidden_size: int,
    *,
    bytes_per_param: float,
    gpu_type: str = "H100",
    gpu_count: int = 1,
) -> int:
    """Max tokens per micro-step (per_device_batch × context_length) that fit.

    Multi-GPU here is a ``device_map`` pipeline split, not data parallel:
    weights shard across GPUs but activations peak on whichever stage is
    computing, so the budget is derived from ONE GPU's free VRAM, not the sum.
    Returns 0 when the inputs aren't known well enough to size anything.
    """
    vram_gb = TRAINING_GPU_VRAM_GB.get(gpu_type)
    if not vram_gb or hidden_size <= 0 or params_b <= 0 or gpu_count <= 0:
        return 0
    static_gb_per_gpu = params_b * bytes_per_param / max(1, gpu_count)
    headroom_gb = _VRAM_UTIL_FRACTION * vram_gb - static_gb_per_gpu
    if headroom_gb <= 0:
        return 0
    usable_bytes = headroom_gb * _BYTES_PER_GB * _ACTIVATION_SAFETY
    return int(usable_bytes // (ACTIVATION_BYTES_PER_TOKEN_PER_HIDDEN * hidden_size))


def split_batch(batch_size: int, context_length: int, token_budget: int) -> tuple[int, int]:
    """Split an effective batch into (per_device_batch, grad_accum).

    Only divisors of ``batch_size`` are considered so the effective batch stays
    exact. ``token_budget == 0`` keeps micro-batch 1 (safe, just slow).
    """
    cap = 1
    if token_budget > 0 and context_length > 0:
        cap = max(1, min(MAX_MICRO_BATCH, token_budget // context_length, batch_size))
    per_device = next(d for d in range(cap, 0, -1) if batch_size % d == 0)
    return per_device, max(1, batch_size // per_device)


def default_epochs(num_examples: int) -> int:
    """Epochs when the user leaves them unset — scaled to dataset size."""
    if num_examples >= HUGE_DATASET_ROWS:
        return 1
    if num_examples >= LARGE_DATASET_ROWS:
        return 2
    visits_needed = math.ceil(TARGET_EXAMPLE_VISITS / max(1, num_examples))
    return max(MIN_EPOCHS, min(MAX_EPOCHS, visits_needed))


def qlora_learning_rate(params_b: float, num_examples: int, *, use_lora: bool = True) -> float:
    """Learning rate from the QLoRA paper (arXiv 2305.14314, Table 9) + NeMo LoRA guide.

    LoRA  ≤13B: 1e-4 (small, <2 000 examples) or 2e-4 (large dataset)
    LoRA  >13B: 5e-5 (small, <2 000 examples) or 1e-4 (large dataset)
    Full FT ≤13B: 1e-5
    Full FT >13B: 5e-6
    """
    full_ft_lr = 1e-5 if params_b <= 13 else 5e-6
    if not use_lora:
        return full_ft_lr
    is_small = num_examples < 2000
    if params_b <= 13:
        return 1e-4 if is_small else 2e-4
    return 5e-5 if is_small else 1e-4


def qlora_lora_params(params_b: float, num_examples: int) -> dict[str, Any]:
    """LoRA rank/alpha/dropout from the QLoRA rank sweep + Unsloth guide.

    Rank 16 on small datasets (lower overfitting risk), 32 otherwise; alpha 2×rank.
    Dropout 0.05 only for ≤13B on small data — QLoRA reports it useless above that.
    """
    if num_examples < 1000:
        lora_r = 16
        lora_dropout = 0.05 if params_b <= 13 else 0.0
    else:
        lora_r = 32
        lora_dropout = 0.0
    return {
        "type": "Lora",
        "lora_r": lora_r,
        "lora_alpha": lora_r * 2,
        "lora_dropout": lora_dropout,
        "lora_trainable_modules": "all-linear",
    }


def openai_batch_size(
    num_examples: int,
    *,
    min_batch: int = 1,
    max_batch: int = 8,
    use_lora: bool = True,
) -> int:
    """Batch size from OpenAI's documented ~0.2%-of-examples heuristic
    (CreateFineTuneRequest docs), capped at the catalog's ``max_batch``. Full
    fine-tuning holds all-parameter optimizer states, so its ceiling is halved.
    """
    raw = max(1, round(0.002 * num_examples))
    if not use_lora:
        max_batch = max(1, max_batch // 2)
    return min(max(min_batch, raw), max_batch)


def baseten_context_length(
    needed_tokens: int = 0,
    *,
    model_max: int | None = None,
    requested: int | None = None,
) -> int:
    """Smallest context bucket covering the dataset's longest row + request.

    Snaps *up*, never down — TRL silently truncates rows over MAX_LENGTH, corrupting
    training targets. Buckets and headroom come from models.json, never from code.
    Raises when the model cannot cover the dataset's longest row (plus headroom).
    """
    from overbae.modal.model_registry import get_training_context_policy  # noqa: PLC0415

    policy = get_training_context_policy("baseten")
    buckets: list[int] = sorted(int(b) for b in policy["context_buckets"])
    need = int(needed_tokens or 0)
    if need > 0:
        need += int(policy["context_headroom"])
    target = max(need, int(requested or 0), buckets[0])
    chosen = next((b for b in buckets if b >= target), buckets[-1])
    if model_max is not None and model_max > 0:
        if int(model_max) < need:
            raise TrainingPlanError(
                f"Longest dataset row needs ≈{need:,} tokens (incl. headroom) but the "
                f"model's max fine-tuning context is {int(model_max):,} — refusing to "
                "clamp down and truncate."
            )
        chosen = min(chosen, int(model_max))
    return chosen


def should_pack(num_examples: int, avg_row_tokens: int, context_length: int) -> bool:
    """Enable sequence packing when padding waste dominates.

    Packing (TRL bfd) wins on large short-row corpora, but on small datasets it
    shrinks the already-small optimizer-step count, so it stays off there.
    """
    if num_examples < PACKING_MIN_EXAMPLES or context_length <= 0:
        return False
    return avg_row_tokens <= context_length * PACKING_MAX_FILL


class TrainingPlanError(ValueError):
    """A derived parameter combination cannot work — fail before submission."""


@dataclass
class BasetenTrainingPlan:
    """Everything the Baseten training script is parameterised with — the single seam
    between job hyperparameters and the env vars pushed to Baseten. ``notes`` records
    every gap-fill and clamp.
    """

    context_length: int
    n_epochs: int
    batch_size: int  # effective optimizer batch (= per_device × grad_accum)
    per_device_batch: int
    grad_accum: int
    learning_rate: float
    warmup_ratio: float
    weight_decay: float
    lora_r: int
    lora_alpha: int
    lora_dropout: float
    lora_target_modules: str
    packing: bool
    training_type: str = "Lora"  # "Lora" | "Full"; LoRA fields are ignored for Full
    load_in_4bit: bool = False
    # Max per_device_batch × context_length that fits one GPU; 0 = not computed.
    token_budget: int = 0
    seed: int = TRAINING_SEED
    notes: list[str] = field(default_factory=list)


def _default_lora_target_modules(model_id: str | None) -> str | None:
    """Catalog/family override for LoRA targets when hyperparams omit them.

    LFM2 hybrid conv+attn needs explicit targets — Unsloth ``all-linear`` expands to
    Llama module names and misses out_proj/in_proj/w1/w2/w3.
    """
    if not model_id:
        return None
    from modal_shared.modelfam import resolve

    return resolve(model_id).lora_target_modules


def _resolve_memory_plan(
    *,
    params_b: float,
    hidden_size: int,
    context_length: int,
    use_lora: bool,
    gpu_type: str,
    gpu_count: int,
    notes: list[str],
) -> tuple[bool, int]:
    """Decide 4-bit vs bf16 and compute the activation token budget.

    Weights come first: bf16 is preferred, 4-bit is the fallback. But weights
    fitting is not enough — one sequence of ``context_length`` has to fit too,
    so bf16 also loses to 4-bit when it leaves no room for activations.
    Raises when even 4-bit cannot hold one sequence.
    """
    vram_gb = TRAINING_GPU_VRAM_GB.get(gpu_type)
    if vram_gb is None or params_b <= 0:
        return False, 0

    budget_gb = _VRAM_UTIL_FRACTION * vram_gb * gpu_count

    def budget_for(bytes_per_param: float) -> int:
        return max_token_budget(
            params_b,
            hidden_size,
            bytes_per_param=bytes_per_param,
            gpu_type=gpu_type,
            gpu_count=gpu_count,
        )

    if not use_lora:
        footprint_gb = params_b * FULL_FT_BYTES_PER_PARAM
        if footprint_gb > budget_gb:
            raise TrainingPlanError(
                f"{params_b:g}B full-FT weights+grads+optimizer (≈{footprint_gb:.0f} GB) "
                f"cannot fit {gpu_count}×{gpu_type} ({vram_gb * gpu_count:.0f} GB) even "
                "before activations — the GPU selection cannot train this model."
            )
        full_budget = budget_for(FULL_FT_BYTES_PER_PARAM)
        if hidden_size > 0 and full_budget < context_length:
            raise TrainingPlanError(
                f"{params_b:g}B full-FT at {context_length:,}-token context needs more "
                f"activation memory than {gpu_count}×{gpu_type} has (fits ≈"
                f"{full_budget:,} tokens) — shorten rows, switch to LoRA, or pick a "
                "smaller model."
            )
        return False, full_budget

    bf16_gb = params_b * _BF16_BYTES_PER_PARAM
    bf16_budget = budget_for(_BF16_BYTES_PER_PARAM)
    if bf16_gb <= budget_gb and (bf16_budget >= context_length or hidden_size <= 0):
        return False, bf16_budget

    qlora_gb = params_b * _QLORA_BYTES_PER_PARAM
    if qlora_gb > budget_gb:
        raise TrainingPlanError(
            f"{params_b:g}B QLoRA 4-bit weights (≈{qlora_gb:.0f} GB) cannot fit "
            f"{gpu_count}×{gpu_type} ({vram_gb * gpu_count:.0f} GB) even before "
            "activations — the GPU selection cannot train this model."
        )
    qlora_budget = budget_for(_QLORA_BYTES_PER_PARAM)
    if hidden_size > 0 and qlora_budget < context_length:
        raise TrainingPlanError(
            f"{params_b:g}B at {context_length:,}-token context needs more activation "
            f"memory than {gpu_count}×{gpu_type} has even in 4-bit (fits ≈"
            f"{qlora_budget:,} tokens) — shorten rows or pick a smaller model / "
            "larger GPU."
        )
    if bf16_gb > budget_gb:
        notes.append(
            f"QLoRA (LOAD_IN_4BIT): {params_b:g}B bf16 ≈{bf16_gb:.0f} GB "
            f"> {gpu_count}×{gpu_type} budget; using 4-bit ≈{qlora_gb:.0f} GB"
        )
    else:
        notes.append(
            f"QLoRA (LOAD_IN_4BIT): {params_b:g}B bf16 ≈{bf16_gb:.0f} GB leaves "
            f"too little for activations at ctx {context_length:,} on "
            f"{gpu_count}×{gpu_type}; using 4-bit ≈{qlora_gb:.0f} GB"
        )
    return True, qlora_budget


def derive_baseten_training_plan(
    *,
    hyperparameters: dict[str, Any] | None,
    num_train_examples: int,
    dataset_stats: dict[str, Any] | None,
    params_b: float,
    model_max_context: int | None,
    model_min_batch: int = 1,
    model_max_batch: int | None = None,
    gpu_type: str = "H100",
    gpu_count: int = 1,
    model_id: str | None = None,
    hidden_size: int = 0,
    use_unsloth: bool = True,
) -> BasetenTrainingPlan:
    """Derive the full Baseten training plan from data + catalog context.

    Explicit hyperparameters are honoured verbatim after safety clamps; anything absent
    is gap-filled from the same heuristics the recommender uses, never a hardcoded
    constant. Raises :class:`TrainingPlanError` when the combination cannot work, so a
    job fails pre-submission instead of mid-run on Baseten.

    ``hidden_size`` enables the activation token budget (0 = weight-only checks).
    ``use_unsloth`` is accepted for calibrate-harness compat; stock engine still
    passes 0 hidden size to opt out of activation budgeting.
    """
    del use_unsloth  # reserved; budget is gated on hidden_size alone
    hp = dict(hyperparameters or {})
    stats = dict(dataset_stats or {})
    notes: list[str] = []

    if num_train_examples <= 0:
        raise TrainingPlanError("Dataset produced 0 training examples.")

    training_type: dict[str, Any] = dict(hp.get("training_type") or {})
    tt_name = str(training_type.get("type") or "Lora")
    if tt_name not in ("Lora", "Full"):
        raise TrainingPlanError(
            f"Unknown training_type={tt_name!r}; the Baseten training script "
            "implements 'Lora' and 'Full' fine-tuning."
        )
    use_lora = tt_name == "Lora"

    # Refuse rows over the model's max: TRL would silently truncate them. Re-checked
    # here because the dataset may have drifted since the job-create serializer ran.
    try:
        max_row_tokens = int(stats.get("max_token_length") or 0)
    except (TypeError, ValueError):
        max_row_tokens = 0
    if model_max_context is not None and max_row_tokens > int(model_max_context):
        raise TrainingPlanError(
            f"Longest dataset row is ≈{max_row_tokens:,} tokens but the model's "
            f"max fine-tuning context is {int(model_max_context):,} tokens — rows "
            "would be truncated, corrupting training targets."
        )
    requested_ctx = int(hp.get("context_length") or 0) or None
    context_length = baseten_context_length(
        max_row_tokens, model_max=model_max_context, requested=requested_ctx
    )

    load_in_4bit, token_budget = _resolve_memory_plan(
        params_b=params_b,
        hidden_size=hidden_size,
        context_length=context_length,
        use_lora=use_lora,
        gpu_type=gpu_type,
        gpu_count=gpu_count,
        notes=notes,
    )

    # The wizard omits n_epochs to request derivation, never a fabricated constant.
    raw_epochs = hp.get("n_epochs", hp.get("epochs"))
    if raw_epochs:
        n_epochs = int(raw_epochs)
    else:
        n_epochs = default_epochs(num_train_examples)
        notes.append(f"n_epochs={n_epochs} derived from {num_train_examples} examples")

    raw_lr = hp.get("learning_rate", hp.get("lr"))
    if raw_lr:
        learning_rate = float(raw_lr)
    else:
        learning_rate = qlora_learning_rate(params_b or 7.0, num_train_examples, use_lora=use_lora)
        notes.append(
            f"learning_rate={learning_rate:g} from "
            f"{'QLoRA' if use_lora else 'full-FT'} heuristic ({params_b:g}B)"
        )

    warmup_ratio = float(hp.get("warmup_ratio") or 0.05)
    weight_decay = float(hp["weight_decay"]) if hp.get("weight_decay") is not None else 0.01

    # Never larger than the corpus — an over-sized batch degenerates to 1 step.
    hard_max = model_max_batch if model_max_batch else 256
    hard_max = min(hard_max, num_train_examples)
    hard_max = max(hard_max, model_min_batch)
    raw_batch = hp.get("batch_size")
    if raw_batch:
        batch_size = int(raw_batch)
        clamped = min(max(batch_size, model_min_batch), hard_max)
        if clamped != batch_size:
            notes.append(f"batch_size clamped {batch_size} → {clamped} (model/dataset bounds)")
            batch_size = clamped
    else:
        batch_size = openai_batch_size(
            num_train_examples, min_batch=model_min_batch, max_batch=hard_max, use_lora=use_lora
        )
        notes.append(f"batch_size={batch_size} from 0.2%-of-examples rule")

    # Training runs single-process (device_map pipeline split, not data
    # parallel) — effective batch is per_device × grad_accum regardless of GPU
    # count. Micro-batch grows to whatever the activation budget allows.
    per_device_batch, grad_accum = split_batch(batch_size, context_length, token_budget)
    if per_device_batch > 1:
        notes.append(
            f"micro-batch {per_device_batch}×{grad_accum} "
            f"(budget ≈{token_budget:,} tokens ≥ {per_device_batch}×{context_length:,})"
        )

    qlora = qlora_lora_params(params_b or 7.0, num_train_examples)
    lora_r = int(training_type.get("lora_r") or hp.get("lora_r") or qlora["lora_r"])
    lora_alpha = int(training_type.get("lora_alpha") or hp.get("lora_alpha") or lora_r * 2)
    if "lora_dropout" in training_type:
        lora_dropout = float(training_type["lora_dropout"])
    elif "lora_dropout" in hp:
        lora_dropout = float(hp["lora_dropout"])
    else:
        lora_dropout = float(qlora["lora_dropout"])
    # Gemma4 MoE (26B-A4B): PEFT ParamWrapper on expert params rejects dropout ≠ 0.
    if model_id and lora_dropout and "a4b" in model_id.lower():
        notes.append(f"lora_dropout forced to 0 for Gemma4 MoE (was {lora_dropout})")
        lora_dropout = 0.0
    lora_target_modules = str(
        training_type.get("lora_trainable_modules")
        or _default_lora_target_modules(model_id)
        or qlora["lora_trainable_modules"]
    )
    family_lora_targets = _default_lora_target_modules(model_id)
    if (
        family_lora_targets
        and not training_type.get("lora_trainable_modules")
        and lora_target_modules == family_lora_targets
    ):
        notes.append(f"Family LoRA targets ({model_id}): {family_lora_targets}")

    from overbae.services.datasets.text import approx_tokens_from_chars  # noqa: PLC0415

    avg_row_tokens = approx_tokens_from_chars(
        int(stats.get("avg_input_chars") or 0) + int(stats.get("avg_output_chars") or 0)
    )
    packing = should_pack(num_train_examples, avg_row_tokens, context_length)
    # Gemma4/Muse: Unsloth forces GC off + flex_attention. Nemotron 3.5: hybrid mamba.
    # Packing fills every step to MAX_LENGTH → OOM on 1×H100.
    if packing and model_id:
        from modal_shared.modelfam import family_key  # noqa: PLC0415

        fam = family_key(model_id)
        if fam in ("gemma4", "muse_glimmer", "nemotron35"):
            packing = False
            notes.append(
                "packing disabled for Gemma4/Muse/Nemotron 3.5 (hybrid / no gradient checkpointing)"
            )
    if packing:
        notes.append(f"packing enabled (avg row ≈{avg_row_tokens} tokens ≪ ctx {context_length})")

    return BasetenTrainingPlan(
        context_length=context_length,
        n_epochs=n_epochs,
        batch_size=batch_size,
        per_device_batch=per_device_batch,
        grad_accum=grad_accum,
        learning_rate=learning_rate,
        warmup_ratio=warmup_ratio,
        weight_decay=weight_decay,
        lora_r=lora_r,
        lora_alpha=lora_alpha,
        lora_dropout=lora_dropout,
        lora_target_modules=lora_target_modules,
        packing=packing,
        training_type=tt_name,
        load_in_4bit=load_in_4bit,
        token_budget=token_budget,
        notes=notes,
    )
