"""Unsloth SFT engine (USE_UNSLOTH=true) — FastLanguageModel, not stock TRL.

Labels come from pretok.py's assistant-only masking rather than TRL's
`assistant_only_loss`, so every family goes through one conversational path with
no DATASET_TYPE / Llama-3.1 text-column special case.

CRITICAL IMPORT ORDER: unsloth MUST be the first heavyweight import in the
process — before torch, transformers, trl, peft, datasets. Anything imported
first caches unpatched copies of the classes unsloth monkeypatches, silently
disabling optimizations or baking in stale defaults (concretely trl's SFTConfig
eos_token="<EOS_TOKEN>" sentinel). Do not reorder, not even for one more
env-var-setup import.

Only the FINAL model is checkpointed. Validation is optional: an empty or missing
val.jsonl skips the eval loop entirely.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# MoE only. Unsloth's grouped-gemm autodetect picks a Triton TMA path that is
# incompatible with its own permute_x/permute_y GEMM on some Triton builds and
# crashes mid-training on `tl.make_tensor_descriptor`. torch._grouped_mm has
# broad T4->B200 support and no TMA. Must be set before the unsloth/torch import.
os.environ.setdefault("UNSLOTH_MOE_BACKEND", "grouped_mm")
# Chunked/fused CE — auto-sizes CE chunks to free VRAM at long context. Must be
# set before `import unsloth`. No-op while UNSLOTH_RETURN_LOGITS forces full logits.
os.environ.setdefault("UNSLOTH_ENABLE_CCE", "1")
os.environ.setdefault("UNSLOTH_CE_LOSS_TARGET_GB", "4")

# Family env (COMPILE_DISABLE, LOAD_IN_4BIT, …) must land before `import unsloth`.
# families/* and catalog are stdlib — they do not import torch/transformers/trl.
from catalog import resolve as _resolve_early  # noqa: E402
from families import load_hooks as _load_hooks_early  # noqa: E402

_training_type = (os.environ.get("TRAINING_TYPE") or "Lora").strip().lower()
_model_early = os.environ.get("MODEL_ID") or ""
_hooks_early = _load_hooks_early(_resolve_early(_model_early).hooks_module)
for _k, _v in _hooks_early.env_overrides(use_lora=_training_type != "full").items():
    os.environ[_k] = _v

# DO NOT MOVE: `import unsloth` (via FastLanguageModel) must be the first
# heavyweight import here — see the module docstring. It is what lets Unsloth's
# eos_token patch reach `trl.SFTConfig`/`SFTTrainer` before trl's lazy module
# realizes them with the unpatched "<EOS_TOKEN>" default.
import torch  # noqa: E402
from unsloth import FastLanguageModel  # noqa: E402

try:
    from unsloth import FastModel as _FastModel  # noqa: E402
except ImportError:  # older Unsloth images without FastModel
    _FastModel = None

from catalog import resolve  # noqa: E402
from families import load_hooks  # noqa: E402

if torch.cuda.is_available() and torch.cuda.device_count() > 1:
    # Unsloth's checkpoint-offload double buffering allocates a second GPU
    # buffer when memory allows; on a pipeline-split stage that is pure overhead.
    os.environ.setdefault("UNSLOTH_DISABLE_DOUBLE_BUFFER", "1")

# `from trl import ...` MUST come AFTER `from pretok import ...`: pretok's
# module-level `_bootstrap_trl_src()` DELETES the "trl"/"trl.*" sys.modules
# entries unsloth just patched and re-imports a fresh, never-patched trl from
# the TRL_SRC checkout. Binding SFTConfig/SFTTrainer after pretok therefore
# gets the TRL_SRC (pinned) classes; binding first would lock them to the
# unsloth-patched copies, whose defaults the rest of this module works around.
from basepath import base_weights_for  # noqa: E402
from common import (  # noqa: E402
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
    RUN_DIR,
    SEED,
    USE_LORA,
    WARMUP_RATIO,
    WEIGHT_DECAY,
    ProgressCallback,
    apply_shared_patches,
    load_jsonl,
    rewrite_adapter_base_model,
)
from datasets import Dataset  # noqa: E402
from pretok import pretok_row  # noqa: E402
from trl import SFTConfig, SFTTrainer  # noqa: E402
from truncation import refuse_truncation  # noqa: E402

apply_shared_patches()

# unsloth_zoo force-injects `push_to_hub_token` into TrainingArguments.to_dict()
# on transformers>=5.0, guarding a pop that TRL main now only does for
# transformers<5.0 — so the key leaks into `SFTConfig(**dict_args)`, which
# rejects it. Undoing the injection is a safe no-op if TRL ever pops again.
from transformers import TrainingArguments as _TrainingArguments  # noqa: E402

_patched_to_dict = _TrainingArguments.to_dict


def _to_dict_no_push_token(self):
    d = _patched_to_dict(self)
    d.pop("push_to_hub_token", None)
    return d


_TrainingArguments.to_dict = _to_dict_no_push_token

# Importing unsloth process-wide strips mean_token_accuracy out of
# trl.SFTTrainer.compute_loss, because its fused cross-entropy path returns a
# sentinel EMPTY_LOGITS object instead of real logits. Forcing real logits back
# on restores token_accuracy on BT_PROGRESS — but a full (batch, seq, vocab)
# tensor then materializes every step and caps context (~10 GB at 32k for Qwen3).
# Gate on context length: keep the metric below LOGITS_METRIC_MAX_CTX, drop it
# above so fused/chunked CE can reclaim VRAM for long-context jobs.
LOGITS_METRIC_MAX_CTX = int(os.getenv("LOGITS_METRIC_MAX_CTX", "16384"))
_ENABLE_LOGITS_METRIC = (PER_DEVICE_BATCH * MAX_LENGTH) <= LOGITS_METRIC_MAX_CTX
if _ENABLE_LOGITS_METRIC:
    os.environ["UNSLOTH_RETURN_LOGITS"] = "1"
else:
    os.environ.pop("UNSLOTH_RETURN_LOGITS", None)
    print(
        f"ctx={MAX_LENGTH} > LOGITS_METRIC_MAX_CTX={LOGITS_METRIC_MAX_CTX}: "
        "UNSLOTH_RETURN_LOGITS off (no token_accuracy; fused/chunked CE free)",
        flush=True,
    )

from transformers import Trainer as _HFTrainer  # noqa: E402


class _AccurateSFTTrainer(SFTTrainer):
    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
        # Calls _HFTrainer.compute_loss directly, not super(): unsloth mutates
        # SFTTrainer's class attribute in place, so the MRO still resolves to its
        # stripped method.
        if not _ENABLE_LOGITS_METRIC:
            return _HFTrainer.compute_loss(
                self,
                model,
                inputs,
                return_outputs=return_outputs,
                num_items_in_batch=num_items_in_batch,
            )
        mode = "train" if self.model.training else "eval"
        labels = inputs["labels"]
        inputs["use_cache"] = False
        loss, outputs = _HFTrainer.compute_loss(
            self, model, inputs, return_outputs=True, num_items_in_batch=num_items_in_batch
        )

        logits = getattr(outputs, "logits", None)
        if logits is None or not torch.is_tensor(logits) or logits.numel() == 0:
            return (loss, outputs) if return_outputs else loss

        with torch.no_grad():
            if mode == "train":
                if "attention_mask" in inputs:
                    num_tokens_in_batch = (
                        self.accelerator.gather_for_metrics(inputs["attention_mask"].sum())
                        .sum()
                        .item()
                    )
                elif "position_ids" in inputs:
                    local_num_tokens = torch.tensor(
                        inputs["position_ids"].size(1), device=inputs["position_ids"].device
                    )
                    num_tokens_in_batch = (
                        self.accelerator.gather_for_metrics(local_num_tokens).sum().item()
                    )
                else:
                    num_tokens_in_batch = None
                if num_tokens_in_batch is not None:
                    self._total_train_tokens += num_tokens_in_batch
                    self._metrics[mode]["num_tokens"] = [self._total_train_tokens]

            shift_logits = logits[..., :-1, :].contiguous()
            shift_labels = labels[..., 1:].contiguous()
            predictions = shift_logits.argmax(dim=-1)
            mask = shift_labels != -100
            correct_tokens = self.accelerator.gather_for_metrics(
                (predictions == shift_labels) & mask
            )
            total_tokens = self.accelerator.gather_for_metrics(mask.sum())
            total_sum = total_tokens.sum()
            accuracy = (correct_tokens.sum() / total_sum).item() if total_sum > 0 else 0.0
            self._metrics[mode]["mean_token_accuracy"].append(accuracy)

        return (loss, outputs) if return_outputs else loss


SFTTrainer = _AccurateSFTTrainer

LORA_ALPHA = int(os.getenv("LORA_ALPHA", str(LORA_R * 2)))
_lora_targets_raw = os.getenv(
    "LORA_TARGET_MODULES",
    "q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj",
)
# Unsloth's get_peft_model has no "all-linear" sentinel — it iterates whatever it
# is given, so the raw string becomes one target "module" per character. The
# shared recommender defaults to "all-linear", so expand it here.
_ALL_LINEAR_MODULES = [
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
]
LORA_TARGET_MODULES: list[str] = (
    _ALL_LINEAR_MODULES
    if _lora_targets_raw.strip() == "all-linear"
    else [m.strip() for m in _lora_targets_raw.split(",") if m.strip()]
)
LOAD_IN_4BIT = os.getenv("LOAD_IN_4BIT", "0") == "1" and USE_LORA
# PER_DEVICE_BATCH is always 1, so the stock padding-free collator has nothing to
# flatten across; the waste is short rows sitting alone in a MAX_LENGTH step.
# PACK_ROWS concatenates several pretokenized rows per example, carrying per-row
# seq_lengths so the collator resets position_ids at each row boundary.
# Default off: Modal omitted this env and the old default ("1") packed every job
# to MAX_LENGTH, OOMing Gemma4 12B/26B on flex_attention (no FA2, GC forced off).
# Honor PACKING too — BasetenRunner sets that name for the stock engine.
PACK_ROWS = os.getenv("PACK_ROWS", os.getenv("PACKING", "0")) == "1"


def _pack_rows(rows: list[dict], max_length: int) -> list[dict]:
    """Greedily concat pretokenized rows into <= max_length packed examples.

    The per-example "seq_lengths" is what stops packed rows attending into each
    other: the padding-free collator resets position_ids at each boundary.
    Rows longer than max_length raise — never truncate user data.
    """
    packed: list[dict] = []
    cur_ids: list[int] = []
    cur_labels: list[int] = []
    cur_lengths: list[int] = []

    def _flush() -> None:
        if cur_ids:
            packed.append(
                {
                    "input_ids": list(cur_ids),
                    "labels": list(cur_labels),
                    "seq_lengths": list(cur_lengths),
                }
            )
        cur_ids.clear()
        cur_labels.clear()
        cur_lengths.clear()

    for i, row in enumerate(rows):
        ids, labels = row["input_ids"], row["labels"]
        n = len(ids)
        refuse_truncation(n, max_length, row_index=i)
        if cur_ids and len(cur_ids) + n > max_length:
            _flush()
        cur_ids.extend(ids)
        cur_labels.extend(labels)
        cur_lengths.append(n)
    _flush()
    return packed


def _build_dataset(tok, rows: list[dict]) -> Dataset | None:
    """Pretok each row into input_ids/attention_mask/labels; drop unparseable rows."""
    out: list[dict] = []
    path_counts: dict[str, int] = {}
    _printed_full_traceback = False
    for i, row in enumerate(rows):
        messages = row.get("messages")
        if not messages:
            continue
        tools = row.get("tools")
        try:
            pretok = pretok_row(tok, MODEL_ID, messages, tools)
        except Exception as e:  # noqa: BLE001 — best-effort row skip, not fatal
            print(f"warn: skipping row (pretok failed: {e})", flush=True)
            if not _printed_full_traceback:
                import traceback as _tb

                print("warn: full traceback for first failure:\n" + _tb.format_exc(), flush=True)
                _printed_full_traceback = True
            continue
        n = len(pretok["input_ids"])
        refuse_truncation(n, MAX_LENGTH, row_index=i)
        path = str(pretok.get("path") or "unknown")
        path_counts[path] = path_counts.get(path, 0) + 1
        out.append({k: pretok[k] for k in ("input_ids", "labels")})
    if not out:
        return None
    print(f"pretok paths: {path_counts}", flush=True)
    if PACK_ROWS:
        packed = _pack_rows(out, MAX_LENGTH)
        print(
            f"Packed {len(out)} rows → {len(packed)} sequences (max_length={MAX_LENGTH})",
            flush=True,
        )
        out = packed
    return Dataset.from_list(out)


def main() -> None:
    eff = PER_DEVICE_BATCH * GRAD_ACCUM
    method = f"LoRA r={LORA_R} alpha={LORA_ALPHA}" if USE_LORA else "Full FT"
    print(
        f"Model={MODEL_ID}  {method}  batch={PER_DEVICE_BATCH}x{GRAD_ACCUM}={eff}  "
        f"ctx={MAX_LENGTH}  epochs={N_EPOCHS}  seed={SEED}",
        flush=True,
    )

    # Without an explicit device_map on multi-GPU, Unsloth/transformers loads the
    # ENTIRE model onto GPU 0; "balanced" splits layers across GPUs in one
    # process (no torchrun/DDP). "sequential" matches Unsloth's own default.
    _n_gpus = torch.cuda.device_count() if torch.cuda.is_available() else 1
    _spec = resolve(MODEL_ID)
    _hooks = load_hooks(_spec.hooks_module)
    _forced_map = _hooks.device_map(_n_gpus)
    if _forced_map is not None:
        _device_map: str | dict = _forced_map
        if _n_gpus > 1:
            print(
                f"{_spec.key}: {_n_gpus} GPUs visible but family pins device_map={_forced_map!r}",
                flush=True,
            )
    elif _n_gpus > 1:
        _device_map = "balanced"
        print(f"Multi-GPU: {_n_gpus} GPUs visible, using device_map='balanced'", flush=True)
        # xFormers builds its attention bias once and never relocates it, so a
        # decoder layer on another GPU raises "Attention bias and
        # Query/Key/Value should be on the same device". SDPA rebuilds the mask
        # per call from the current Q's device. Multi-GPU only — single-GPU runs
        # keep the faster backend Unsloth would pick.
        import unsloth.utils.attention_dispatch as _attn_dispatch

        _attn_dispatch.HAS_FLASH_ATTENTION = False
        _attn_dispatch.HAS_XFORMERS = False
    else:
        _device_map = "sequential"
    if LOAD_IN_4BIT:
        print("LOAD_IN_4BIT=1 — QLoRA on single-GPU path preferred for ≤72B", flush=True)

    # Full FT: full_finetuning=True, load_in_4bit=False (only one method active).
    # LoRA: get_peft_model after from_pretrained; 4bit optional.
    for _k, _v in _hooks.env_overrides(use_lora=USE_LORA).items():
        os.environ[_k] = _v
        print(f"Family {_spec.key} env: {_k}={_v}", flush=True)

    # env_overrides may set ADAPTER_BASE_MODEL (gpt-oss merge uses BF16).
    # common.MODEL_ID was bound at import — re-read after overrides.
    model_id = os.environ.get("MODEL_ID") or MODEL_ID
    adapter_base_model = os.environ.get("ADAPTER_BASE_MODEL") or model_id

    _fast_cls = _hooks.fast_model_cls(FastLanguageModel, _FastModel)
    if _fast_cls is not FastLanguageModel:
        print(f"Using {_fast_cls.__name__} for family={_spec.key}", flush=True)

    _load_kwargs: dict = _hooks.load_kwargs(use_lora=USE_LORA)
    if not USE_LORA:
        # Unsloth fused full FT + flex_attention: Q/K stay fp32, V is bf16.
        os.environ.setdefault("UNSLOTH_ENABLE_FLEX_ATTENTION", "0")
        _load_kwargs.setdefault("attn_implementation", "sdpa")

    # Tiled MLP (~2× context, ~1.3× step time) only pays when seq_len > hidden.
    # UNSLOTH_TILED_MLP=0 disables for attribution probes.
    _tiled_off = os.getenv("UNSLOTH_TILED_MLP", "").strip() in ("0", "false", "off")
    _hidden = int(os.getenv("HIDDEN_SIZE", "0") or 0)
    if not _tiled_off and _hidden > 0 and _hidden < MAX_LENGTH:
        _load_kwargs.setdefault("unsloth_tiled_mlp", True)
        print(f"Tiled MLP on (ctx={MAX_LENGTH} > hidden={_hidden})", flush=True)
    elif not _tiled_off and _hidden <= 0 and MAX_LENGTH > 8192:
        # Catalog hidden unknown — 8k exceeds every dense hidden we train.
        _load_kwargs.setdefault("unsloth_tiled_mlp", True)
        print(f"Tiled MLP on (ctx={MAX_LENGTH} > 8192 fallback)", flush=True)
    elif _tiled_off:
        print("Tiled MLP off (UNSLOTH_TILED_MLP=0)", flush=True)

    model, tokenizer = _fast_cls.from_pretrained(
        model_name=base_weights_for(model_id),
        max_seq_length=MAX_LENGTH,
        dtype=None,
        load_in_4bit=LOAD_IN_4BIT,
        load_in_8bit=False,
        full_finetuning=not USE_LORA,
        trust_remote_code=True,
        device_map=_device_map,
        **_load_kwargs,
    )

    # Resolve Unsloth's placeholder eos. Multimodal models hand back a
    # ProcessorMixin wrapping the real tokenizer as `.tokenizer`, and TRL
    # validates eos_token against that INNER vocab — so both objects must be
    # fixed or TRL still raises on the untouched inner `<EOS_TOKEN>`.
    _inner_tok = getattr(tokenizer, "tokenizer", tokenizer)
    eos = getattr(_inner_tok, "eos_token", None)
    if not eos or eos == "<EOS_TOKEN>":
        for cand in ("<|im_end|>", "<|eot_id|>", "</s>", "<|endoftext|>", "<|return|>"):
            tid = _inner_tok.convert_tokens_to_ids(cand)
            if tid is not None and tid != _inner_tok.unk_token_id:
                _inner_tok.eos_token = cand
                if _inner_tok is not tokenizer:
                    tokenizer.eos_token = cand
                break
    if _inner_tok.pad_token is None:
        _inner_tok.pad_token = _inner_tok.eos_token
        if _inner_tok is not tokenizer:
            tokenizer.pad_token = _inner_tok.eos_token
    print(
        f"eos_token={_inner_tok.eos_token!r} id={_inner_tok.eos_token_id}"
        f" (processor={_inner_tok is not tokenizer})",
        flush=True,
    )
    from modal_shared.modelfam import restore_serve_chat_template

    _serve_chat_template = getattr(_inner_tok, "chat_template", None)

    _gc = _hooks.peft_gradient_checkpointing()
    if USE_LORA:
        _lora_dropout = _hooks.peft_lora_dropout(LORA_DROPOUT, model_id=MODEL_ID)
        if _lora_dropout != LORA_DROPOUT:
            print(
                f"{_spec.key}: forcing lora_dropout={_lora_dropout} (was {LORA_DROPOUT})",
                flush=True,
            )
        model = _fast_cls.get_peft_model(
            model,
            r=LORA_R,
            lora_alpha=LORA_ALPHA,
            lora_dropout=_lora_dropout,
            target_modules=LORA_TARGET_MODULES,
            bias="none",
            use_gradient_checkpointing=_gc,
            random_state=SEED,
        )
    _hooks.post_load(model, tokenizer, use_lora=USE_LORA)

    # gpt-oss QLoRA loads the catalog unsloth-bnb-4bit id (families/gpt_oss.py).

    # torch._grouped_mm's fallback is not autocast-aware
    # (https://github.com/pytorch/pytorch/issues/174763): transformers casts
    # `input` to `weight.dtype` only before the FAST path, so under autocast the
    # fallback's plain torch.mm sees float32 input against bf16 weights. Add the
    # cast the fast path already gets.
    try:
        import transformers.integrations.moe as _moe_integrations

        _orig_grouped_mm = _moe_integrations._grouped_mm

        def _grouped_mm_dtype_safe(input, weight, offs):  # noqa: A002
            return _orig_grouped_mm(input.to(weight.dtype), weight, offs)

        _moe_integrations._grouped_mm = _grouped_mm_dtype_safe
    except (ImportError, AttributeError):
        pass  # transformers.integrations.moe not present/shaped this way — no-op

    train_rows = load_jsonl("data.jsonl")
    val_rows = load_jsonl("val.jsonl")
    print(f"Loaded: {len(train_rows)} train / {len(val_rows)} val", flush=True)
    if not train_rows:
        raise RuntimeError("data.jsonl is empty — nothing to train on")

    train_ds = _build_dataset(tokenizer, train_rows)
    if train_ds is None:
        raise RuntimeError("pretok produced zero usable training rows")
    val_ds = _build_dataset(tokenizer, val_rows) if val_rows else None
    has_val = val_ds is not None

    # Mean row length × micro-batch — the real denominator for activation C.
    _row_lens = [len(row["input_ids"]) for row in train_ds]
    _mean_row = sum(_row_lens) / len(_row_lens)
    _tokens_per_step = int(round(_mean_row * PER_DEVICE_BATCH))
    print(
        f"tokens/step measured={_tokens_per_step} "
        f"(mean_row={_mean_row:.1f} × batch={PER_DEVICE_BATCH}; "
        f"min={min(_row_lens)} max={max(_row_lens)}; claimed={PER_DEVICE_BATCH * MAX_LENGTH})",
        flush=True,
    )

    sft_kwargs: dict = {}
    if has_val:
        sft_kwargs.update(eval_strategy="epoch")
    if MAX_STEPS > 0:
        sft_kwargs["max_steps"] = MAX_STEPS

    _sft_overrides = _hooks.sft_config_overrides(use_lora=USE_LORA)

    training_args = SFTConfig(
        output_dir=CHECKPOINT_DIR,
        num_train_epochs=N_EPOCHS,
        per_device_train_batch_size=PER_DEVICE_BATCH,
        per_device_eval_batch_size=PER_DEVICE_BATCH,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LEARNING_RATE,
        # transformers≥5.15 dropped warmup_ratio; float warmup_steps in [0,1) is a ratio.
        warmup_steps=WARMUP_RATIO,
        weight_decay=WEIGHT_DECAY,
        max_grad_norm=1.0,
        lr_scheduler_type="cosine",
        logging_steps=LOGGING_EVERY,
        max_length=MAX_LENGTH,
        # Only satisfies TRL's guard that padding_free + numeric max_length needs
        # packing; _pack_rows does the actual packing, and skip_prepare_dataset
        # below means TRL's packing code never runs.
        packing=PACK_ROWS,
        # Flattens each micro-batch into one sequence via position_ids instead of
        # padding, resetting at every row boundary from "seq_lengths". Requires
        # the stock TRL collator — a custom one is rejected when padding_free.
        padding_free=PACK_ROWS,
        report_to=[],
        seed=SEED,
        save_strategy="no",  # final-only, matching engine_stock.py
        bf16=torch.cuda.is_available() and torch.cuda.is_bf16_supported(),
        fp16=torch.cuda.is_available() and not torch.cuda.is_bf16_supported(),
        eos_token=_inner_tok.eos_token,
        # Rows are already pretokenized — skip TRL's dataset prep entirely.
        dataset_kwargs={"skip_prepare_dataset": True},
        remove_unused_columns=False,
        **_sft_overrides,
        **sft_kwargs,
    )

    callback = ProgressCallback(RUN_DIR)
    callback.set_measured_tokens_per_step(_tokens_per_step)

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        # Must be the INNER plain tokenizer, not the ProcessorMixin Unsloth wraps
        # it in for multimodal families (Qwen3.5, Qwen-VL, ...): TRL's __init__
        # rejects packing/padding_free when processing_class is a ProcessorMixin
        # even for text-only data (pre-v1.10 guards). The inner tokenizer is what
        # pretok tokenized with and what eos/pad were fixed on above, so nothing
        # downstream changes.
        processing_class=_inner_tok,
        # None → TRL's own DataCollatorForLanguageModeling, which turns each
        # example's "seq_lengths" into position_ids. A custom collator can't do
        # that reset and TRL rejects one outright when padding_free=True.
        data_collator=None,
        callbacks=[callback],
    )

    print(f"Training — {len(train_ds)} train / {len(val_ds) if has_val else 0} val …", flush=True)
    trainer.train()
    restore_serve_chat_template(_inner_tok, _serve_chat_template)
    if _inner_tok is not tokenizer and hasattr(tokenizer, "chat_template"):
        restore_serve_chat_template(tokenizer, _serve_chat_template)
    trainer.save_model(CHECKPOINT_DIR)
    if USE_LORA:
        # Prefer ADAPTER_BASE_MODEL (BF16 catalog id) so register merges into a
        # serveable base, not the bnb-4bit / MXFP4 train id.
        rewrite_adapter_base_model(CHECKPOINT_DIR, adapter_base_model)
    if not USE_LORA:
        # Full checkpoints carry no adapter_config.json; the tokenizer must ride
        # along so register_model can serve the artifact standalone.
        tokenizer.save_pretrained(CHECKPOINT_DIR)
    print(f"{'Adapter' if USE_LORA else 'Full checkpoint'} saved → {CHECKPOINT_DIR}", flush=True)

    callback.emit_final_checkpoint(trainer.state, path="checkpoint-final")


if __name__ == "__main__":
    main()
