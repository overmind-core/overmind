"""Stock TRL SFTTrainer engine (USE_UNSLOTH=false) — plain transformers + peft.

The dataset stays on TRL's native conversational path (`messages` column +
`assistant_only_loss`); pretok.py is Unsloth-engine-only.

Only the FINAL model is checkpointed — one save_model() after train(). Eval,
when a validation set exists, runs once per epoch purely for the loss curve and
never gates what is persisted.
"""

from __future__ import annotations

import json
import os

import torch
from basepath import base_weights_for
from common import (
    CHECKPOINT_DIR,
    GRAD_ACCUM,
    LEARNING_RATE,
    LOGGING_EVERY,
    LORA_DROPOUT,
    LORA_R,
    MAX_LENGTH,
    MAX_STEPS,
    MODEL_ID,
    N_EPOCHS,
    PER_DEVICE_BATCH,
    SEED,
    USE_LORA,
    WARMUP_RATIO,
    WEIGHT_DECAY,
    ProgressCallback,
    apply_shared_patches,
    emit_stage,
    is_llama31_family,
    load_jsonl,
    needs_trust_remote,
    prefetch_base_model,
    rewrite_adapter_base_model,
)
from datasets import Dataset
from peft import LoraConfig
from training_chat_template import patch_known_training_template as _patch_known_training_template
from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer

apply_shared_patches()

DATASET_TYPE = os.getenv("DATASET_TYPE", "chat")
LORA_ALPHA = int(os.getenv("LORA_ALPHA", "32"))
# Explicit names beat "all-linear": peft 0.19 on MoE (Qwen3-Coder) treats that
# magic string as a char iterable and crashes. Dropout defaults to 0 because the
# MoE ParamWrapper rejects lora_dropout != 0.
_lora_targets_raw = os.getenv(
    "LORA_TARGET_MODULES",
    # gate/up/down = Llama/Qwen MLP; input/output_linear = Granite-4 shared MLP
    "q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj,input_linear,output_linear",
)
LORA_TARGET_MODULES: str | list[str] = (
    "all-linear"
    if _lora_targets_raw.strip() == "all-linear"
    else [m.strip() for m in _lora_targets_raw.split(",") if m.strip()]
)
PACKING = os.getenv("PACKING", "0") == "1"
ASSISTANT_ONLY_LOSS = os.getenv("ASSISTANT_ONLY_LOSS", "1") == "1"
LOAD_IN_4BIT = os.getenv("LOAD_IN_4BIT", "0") == "1" and USE_LORA

LORA_CONFIG = LoraConfig(
    r=LORA_R,
    lora_alpha=LORA_ALPHA,
    lora_dropout=LORA_DROPOUT,
    target_modules=LORA_TARGET_MODULES,
    task_type="CAUSAL_LM",
)


def load_model(model_id: str):
    """Load CausalLM, or fall back for multimodal Qwen3.5 / Gemma4 / Ministral."""
    common: dict = dict(
        torch_dtype=torch.bfloat16,
        device_map="auto",
    )
    if LOAD_IN_4BIT:
        from transformers import BitsAndBytesConfig

        # Forced by finetuning_runner when bf16 weights won't fit the GPUs.
        common["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )
        print("LOAD_IN_4BIT=1 — QLoRA BitsAndBytesConfig(nf4)", flush=True)
    trust = needs_trust_remote(model_id)

    def _disable_cache(model):
        if hasattr(model, "config"):
            model.config.use_cache = False
        return model

    def _prepare(model):
        model = _disable_cache(model)
        from catalog import resolve
        from families import load_hooks

        hooks = load_hooks(resolve(model_id).hooks_module)
        hooks.post_load(model, None, use_lora=USE_LORA)
        return model

    try:
        return _prepare(
            AutoModelForCausalLM.from_pretrained(model_id, trust_remote_code=trust, **common)
        )
    except Exception as exc:
        print(f"CausalLM load failed ({exc!r}); trying multimodal loaders")

    try:
        from transformers import AutoModelForImageTextToText

        return _prepare(
            AutoModelForImageTextToText.from_pretrained(model_id, trust_remote_code=True, **common)
        )
    except Exception as exc:
        print(f"AutoModelForImageTextToText failed ({exc!r})")

    import transformers

    config = AutoConfig.from_pretrained(model_id, trust_remote_code=True)
    arch = (getattr(config, "architectures", None) or [None])[0]
    if not arch:
        raise RuntimeError(f"No architectures listed for {model_id}")
    cls = getattr(transformers, arch, None)
    if cls is None:
        raise RuntimeError(f"transformers has no class {arch}")
    return _prepare(cls.from_pretrained(model_id, trust_remote_code=True, **common))


def _llama31_format(messages: list, tools: list | None) -> str:
    """Manually render Llama-3.1 chat format, bypassing apply_chat_template.

    LLAMA-3.x ONLY. Its hardcoded tokens (<|begin_of_text|>, <|start_header_id|>,
    <|eot_id|>) are special tokens on no other family, where they shred into
    garbage sub-word fragments and train the model to predict noise instead of
    its own turn markers — measured as -15.8% name-acc for Qwen3-8B vs +5.3%
    for Llama-3.1-8B.
    """
    bos = "<|begin_of_text|>"
    soh = "<|start_header_id|>"
    eoh = "<|end_header_id|>"
    eot = "<|eot_id|>"

    msgs = [dict(m) for m in messages]
    sys_content = "Cutting Knowledge Date: December 2023\nToday Date: 26 Jul 2024\n\n"
    if msgs and msgs[0]["role"] == "system":
        sys_content += msgs[0].get("content") or ""
        msgs = msgs[1:]

    parts = [bos, f"{soh}system{eoh}\n\n{sys_content}{eot}"]

    if tools and msgs:
        first_user_content = msgs[0].get("content") or ""
        msgs = msgs[1:]
        tools_block = (
            "Given the following functions, please respond with a JSON for a "
            "function call with its proper arguments that best answers the given prompt.\n\n"
            'Respond in the format {"name": function name, "parameters": dictionary of argument name and its value}.'
            "\n\nDo not use variables.\n\n"
        )
        tools_block += "\n\n".join(json.dumps(t, indent=4) for t in tools)
        parts.append(f"{soh}user{eoh}\n\n{tools_block}\n\n{first_user_content}{eot}")

    for m in msgs:
        role = m["role"]
        content = m.get("content") or ""
        tool_calls = m.get("tool_calls") or []
        if role == "user":
            parts.append(f"{soh}user{eoh}\n\n{content}{eot}")
        elif role == "assistant" and not tool_calls:
            parts.append(f"{soh}assistant{eoh}\n\n{content}{eot}")
        elif role == "assistant" and tool_calls:
            tc_fn = tool_calls[0]["function"]
            args = tc_fn.get("arguments", "{}")
            try:
                args_obj = json.loads(args) if isinstance(args, str) else args
            except Exception:
                args_obj = {}
            call_str = json.dumps({"name": tc_fn["name"], "parameters": args_obj})
            parts.append(f"{soh}assistant{eoh}\n\n{call_str}{eot}")
        elif role in ("tool", "ipython"):
            parts.append(f"{soh}ipython{eoh}\n\n{content}{eot}")

    return "".join(parts)


def _assistant_only_loss_supported(tokenizer) -> bool:
    """Can TRL patch/verify this tokenizer's chat template for assistant_only_loss?

    An unrecognized template raises ValueError from deep inside SFTTrainer's
    dataset prep and kills the run, so this checks up front with TRL's own
    helpers and the caller falls back to full-sequence loss.
    """
    if _patch_known_training_template(tokenizer):
        return True
    try:
        from trl.chat_template_utils import get_training_chat_template, has_generation_markers
    except ImportError:
        return True  # trl too old for this helper — assistant_only_loss handles it internally

    try:
        tmpl = get_training_chat_template(tokenizer)
    except Exception as exc:  # noqa: BLE001 — any patch failure means "not supported"
        print(f"assistant_only_loss unsupported for this chat template ({exc!r}) — falling back")
        return False
    if tmpl is not None:
        return True
    if tokenizer.chat_template and not has_generation_markers(tokenizer.chat_template):
        print("assistant_only_loss unsupported (no generation markers) — falling back")
        return False
    return True


def load_datasets(tokenizer) -> tuple[Dataset, Dataset | None, str]:
    """Return ``(train_ds, val_ds | None, text_field)``.

    ``val_ds`` is None when no validation rows were shipped — the trainer
    then runs without an eval loop instead of evaluating an empty dataset.
    """
    train_rows = load_jsonl("data.jsonl")
    val_rows = load_jsonl("val.jsonl")
    print(f"Loaded: {len(train_rows)} train / {len(val_rows)} val  (type={DATASET_TYPE})")
    if not train_rows:
        raise RuntimeError("data.jsonl is empty — nothing to train on")

    if DATASET_TYPE == "chat":
        return (
            Dataset.from_list(train_rows),
            Dataset.from_list(val_rows) if val_rows else None,
            "messages",
        )

    # Llama-3.x pre-renders into a flat "text" column, so TRL can't tell
    # assistant turns apart and trains full-sequence loss. Every other family
    # keeps native "messages"/"tools" so TRL applies the model's OWN template —
    # training tokens then match what vLLM feeds it at serve time, and
    # assistant_only_loss can mask the system/user/tool-schema tokens.
    use_llama31_renderer = is_llama31_family(MODEL_ID)

    if not use_llama31_renderer:
        return (
            Dataset.from_list(train_rows),
            Dataset.from_list(val_rows) if val_rows else None,
            "messages",
        )

    def apply_template(row: dict) -> dict:
        import json as _json

        messages = _json.loads(_json.dumps(list(row["messages"])))
        tools_raw = row.get("tools")
        tools = _json.loads(_json.dumps(tools_raw)) if tools_raw else None
        text = _llama31_format(messages, tools)
        return {"text": text}

    remove_cols = list(train_rows[0].keys())
    train_ds = Dataset.from_list(train_rows).map(apply_template, remove_columns=remove_cols)
    val_ds = None
    if val_rows:
        val_ds = Dataset.from_list(val_rows).map(
            apply_template, remove_columns=list(val_rows[0].keys())
        )
    return train_ds, val_ds, "text"


def main() -> None:
    eff = PER_DEVICE_BATCH * GRAD_ACCUM
    print(f"Model={MODEL_ID}  type={DATASET_TYPE}  lr={LEARNING_RATE}  seed={SEED}")
    method = f"LoRA r={LORA_R} alpha={LORA_ALPHA}" if USE_LORA else "Full FT"
    print(
        f"{method}  batch={PER_DEVICE_BATCH}×{GRAD_ACCUM}={eff}  "
        f"ctx={MAX_LENGTH}  epochs={N_EPOCHS}  packing={PACKING}"
    )

    base_weights = base_weights_for(MODEL_ID)
    if base_weights == MODEL_ID:
        # No shared snapshot staged — pull WITH progress so the monitor isn't silent for the
        # many minutes a large base model takes to download.
        prefetch_base_model(MODEL_ID)

    emit_stage("loading_model", model=MODEL_ID)
    trust = needs_trust_remote(MODEL_ID)
    tokenizer = AutoTokenizer.from_pretrained(base_weights, trust_remote_code=trust or True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    tokenizer.model_max_length = MAX_LENGTH
    # Snapshot before assistant_only_loss may swap in {% generation %} markers.
    from catalog import _ensure_modelfam

    _ensure_modelfam()
    from modal_shared.modelfam import restore_serve_chat_template

    _serve_chat_template = tokenizer.chat_template

    model = load_model(base_weights)
    emit_stage("model_loaded")

    train_ds, val_ds, text_field = load_datasets(tokenizer)
    has_val = val_ds is not None
    # Assistant-only loss needs TRL's chat templating (conversational path);
    # the pre-rendered "text" path has no turn structure to mask against.
    assistant_only = ASSISTANT_ONLY_LOSS and text_field == "messages"
    if assistant_only and not _assistant_only_loss_supported(tokenizer):
        assistant_only = False
    print(f"Loss masking: {'assistant turns only' if assistant_only else 'full sequence'}")

    sft_kwargs: dict = {}
    if text_field == "text":
        sft_kwargs["dataset_text_field"] = "text"
    if has_val:
        # No saving is tied to eval, so load_best_model_at_end does not apply
        # (it needs a save at every eval point). The final model always ships.
        sft_kwargs.update(eval_strategy="epoch")

    if not USE_LORA:
        # ~2 bytes/param of optimizer state instead of 8, which is what the
        # policy's FULL_FT_BYTES_PER_PARAM guardrail assumes.
        sft_kwargs["optim"] = "paged_adamw_8bit"
    if MAX_STEPS > 0:
        sft_kwargs["max_steps"] = MAX_STEPS

    training_args = SFTConfig(
        output_dir=CHECKPOINT_DIR,
        num_train_epochs=N_EPOCHS,
        per_device_train_batch_size=PER_DEVICE_BATCH,
        # Left unset, HF Trainer defaults eval batch to 8 regardless of the train
        # batch — an 8x memory spike at the first eval, long after training
        # itself looked fine. It OOMs Mamba hybrids like Nemotron-H.
        per_device_eval_batch_size=PER_DEVICE_BATCH,
        gradient_accumulation_steps=GRAD_ACCUM,
        gradient_checkpointing=True,
        learning_rate=LEARNING_RATE,
        # transformers≥5.15 dropped warmup_ratio; float warmup_steps in [0,1) is a ratio.
        warmup_steps=WARMUP_RATIO,
        weight_decay=WEIGHT_DECAY,
        max_grad_norm=1.0,
        lr_scheduler_type="cosine",
        logging_steps=LOGGING_EVERY,
        # TRL's truncation window defaults to 1024, silently truncating rows
        # unless it matches the catalog-derived context.
        max_length=MAX_LENGTH,
        packing=PACKING,
        assistant_only_loss=assistant_only,
        seed=SEED,
        # Final-only: trainer.save_model() after train() writes the one artifact.
        save_strategy="no",
        bf16=True,
        # TRL 1.8 defaults to chunked_nll, which breaks when device_map="auto"
        # wraps forward as functools.partial (multi-GPU LoRA on 70B+).
        loss_type="nll",
        report_to=[],
        **sft_kwargs,
    )

    callback = ProgressCallback()

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        processing_class=tokenizer,
        # peft_config=None → TRL trains the full model (all parameters).
        peft_config=LORA_CONFIG if USE_LORA else None,
        callbacks=[callback],
    )

    print(f"Training — {len(train_ds)} train / {len(val_ds) if has_val else 0} val …")
    trainer.train()
    restore_serve_chat_template(tokenizer, _serve_chat_template)
    trainer.save_model(CHECKPOINT_DIR)
    if USE_LORA:
        rewrite_adapter_base_model(CHECKPOINT_DIR, MODEL_ID)
    if not USE_LORA:
        # Full checkpoints carry no adapter_config.json; the tokenizer must ride
        # along so register_model can serve the artifact standalone.
        tokenizer.save_pretrained(CHECKPOINT_DIR)
    print(f"{'Adapter' if USE_LORA else 'Full checkpoint'} saved → {CHECKPOINT_DIR}")
    # save_strategy="no" means transformers never calls the callback's on_save.
    callback.emit_final_checkpoint(trainer.state, path="checkpoint-final")

    state_path = os.path.join(CHECKPOINT_DIR, "trainer_state.json")
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
        print("=== log_history ===")
        print(json.dumps(state.get("log_history", []), indent=2))


if __name__ == "__main__":
    main()
