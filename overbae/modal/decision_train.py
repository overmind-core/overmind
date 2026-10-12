"""Decision-model training and serving. Imported inside the Modal image, not by Django."""

from __future__ import annotations

import random
import shutil
import sys
import time
from pathlib import Path

SEED = 3407
_LOADED: dict = {}
_FIT_ERROR = "No records fit the context cap."


def train(payload: dict) -> dict:
    family = payload["family"]
    if family == "clef":
        return _train_clef(payload)
    if family == "d3":
        return _train_d3(payload)
    return {"error": "Decision training failed."}


def predict(payload: dict) -> dict:
    family = payload["family"]
    if family == "clef":
        return _predict_clef(payload)
    if family == "d3":
        return _predict_d3(payload)
    return {"error": "Decision inference is not available."}


def _train_clef(payload: dict) -> dict:
    import math

    import torch
    from transformers import TrainingArguments
    from unsloth import DecisionTrainer, FastDecisionModel

    repo = payload["repo"]
    max_context = int(payload["max_context_length"])
    out = Path("/artifacts") / payload["job_id"]
    model, processor = FastDecisionModel.from_pretrained(
        repo,
        max_seq_length=max_context,
        load_in_4bit=False,
        random_state=SEED,
    )
    items, report = FastDecisionModel.build_dataset(payload["records"], processor, model)
    train_items = [
        item
        for item in items
        if not (isinstance(item, dict) and len(item.get("input_ids") or []) >= max_context)
    ]
    if not train_items:
        return {"error": _FIT_ERROR}
    train_items, holdout = FastDecisionModel.split_holdout(train_items, SEED)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    model = FastDecisionModel.get_peft_model(model, r=64, lora_alpha=64, random_state=SEED)
    steps = math.ceil(len(train_items) / (8 * 2))
    trainer = DecisionTrainer(
        model=model,
        tokenizer=processor,
        train_dataset=train_items,
        head_learning_rate=3e-4,
        args=TrainingArguments(
            output_dir=str(out / "trainer"),
            per_device_train_batch_size=8,
            gradient_accumulation_steps=2,
            learning_rate=1e-4,
            lr_scheduler_type="cosine",
            warmup_steps=max(1, round(0.05 * steps)),
            weight_decay=0.01,
            num_train_epochs=1,
            bf16=torch.cuda.is_available() and torch.cuda.is_bf16_supported(),
            logging_steps=10,
            report_to="none",
            save_strategy="no",
            seed=SEED,
        ),
    )
    trained = trainer.train()
    losses = [row["loss"] for row in trainer.state.log_history if "loss" in row]
    calibration = FastDecisionModel.calibrate(model, processor, holdout) if holdout else {}
    saved = out / "merged"
    model.save_pretrained_merged(str(saved))
    runtime = float(trained.metrics["train_runtime"])
    return {
        "model": repo,
        "family": "clef",
        "train_records": len(train_items),
        "holdout_records": len(holdout),
        "skipped": report.get("skipped"),
        "steps": trainer.state.global_step,
        "rank": 64,
        "batch": 8,
        "grad_accum": 2,
        "max_context_length": max_context,
        "train_runtime_s": runtime,
        "peak_allocated_gb": _peak_allocated(),
        "peak_reserved_gb": _peak_reserved(),
        "loss_first_last": [round(losses[0], 4), round(losses[-1], 4)] if losses else [],
        "calibration": calibration,
        "saved": str(saved),
    }


def _train_d3(payload: dict) -> dict:
    # Before transformers binds Qwen3.5's Gated DeltaNet to the PyTorch reference.
    import torch
    import unsloth  # noqa: F401
    from huggingface_hub import snapshot_download
    from peft import get_peft_model

    repo = payload["repo"]
    max_context = int(payload["max_context_length"])
    folder = Path(snapshot_download(repo))
    sys.path.insert(0, str(folder))
    from d3_runtime import D3, sdpa_backends

    runtime = D3.from_pretrained(str(folder), max_length=max_context, verify="none")
    runtime.fast = None
    examples, skipped = _d3_examples(runtime, payload["records"], max_context)
    if not examples:
        return {"error": _FIT_ERROR}
    backbone = get_peft_model(runtime.backbone, _d3_lora())
    for name, param in backbone.named_parameters():
        if "visual" in name:
            param.requires_grad_(False)
    readout = torch.nn.Parameter(runtime.readout.detach().float().clone())
    trainable = [param for param in backbone.parameters() if param.requires_grad]
    optimizer = torch.optim.AdamW(
        [
            {"params": trainable, "lr": 1e-4, "weight_decay": 0.01},
            {"params": [readout], "lr": 3e-4, "weight_decay": 0.0},
        ]
    )
    rng = random.Random(SEED)
    rng.shuffle(examples)
    batch, accum = 2, 8
    steps = (len(examples) + batch * accum - 1) // (batch * accum)
    backbone.train()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    losses = []
    optimizer.zero_grad(set_to_none=True)
    cursor = 0
    for _ in range(steps):
        accumulated = 0.0
        for _ in range(accum):
            chunk = [examples[(cursor + offset) % len(examples)] for offset in range(batch)]
            cursor += batch
            loss = _d3_loss(
                _d3_logits(
                    backbone,
                    readout,
                    [item[0] for item in chunk],
                    runtime.pad_id,
                    sdpa_backends,
                ),
                [item[1] for item in chunk],
            )
            (loss / accum).backward()
            accumulated += float(loss.detach())
        torch.nn.utils.clip_grad_norm_(trainable + [readout], 1.0)
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        losses.append(accumulated / accum)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    merged = backbone.merge_and_unload()
    saved = Path("/artifacts") / payload["job_id"]
    _export_d3(folder, saved, merged, readout)
    return {
        "model": repo,
        "family": "d3",
        "kernels": runtime.kernels,
        "train_questions": len(examples),
        "skipped_questions": skipped,
        "steps": steps,
        "rank": 16,
        "batch": batch,
        "grad_accum": accum,
        "max_context_length": max_context,
        "trainable_params": sum(param.numel() for param in trainable) + readout.numel(),
        "train_runtime_s": round(time.perf_counter() - started, 1),
        "peak_allocated_gb": _peak_allocated(),
        "peak_reserved_gb": _peak_reserved(),
        "loss_first_last": [round(losses[0], 4), round(losses[-1], 4)],
        "saved": str(saved),
    }


def _d3_lora():
    from peft import LoraConfig

    # ponytail: Gated DeltaNet conv/A_log/dt_bias stay frozen; LoRA covers the linear projections only.
    return LoraConfig(
        r=16,
        lora_alpha=16,
        lora_dropout=0.0,
        bias="none",
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
            "in_proj_qkv",
            "in_proj_a",
            "in_proj_b",
            "in_proj_z",
            "out_proj",
        ],
    )


def _d3_examples(runtime, records, max_context):
    examples = []
    skipped = 0
    for row in records:
        prepared = runtime.prepare(row["state"], row["questions"])
        gold = row.get("gold") or {}
        for key, sequence in prepared.sequences.items():
            if len(sequence) > max_context:
                skipped += 1
                continue
            target = _target_vector(prepared.questions[key], gold.get(key))
            if target is None:
                skipped += 1
                continue
            examples.append((sequence, target))
    return examples, skipped


def _target_vector(question, gold):
    if isinstance(gold, str):
        gold = {"label": gold}
    if not isinstance(gold, dict):
        return None
    keys = question.keys
    probs = gold.get("probabilities")
    if isinstance(probs, dict):
        values = [float(probs.get(key, 0.0)) for key in keys]
    elif question.kind == "noul" and "noul" in gold:
        true = float(gold["noul"])
        values = [1.0 - true, true]
    elif "label" in gold:
        label = str(gold["label"])
        values = [1.0 if key == label else 0.0 for key in keys]
    else:
        return None
    total = sum(values)
    if total <= 0 or len(values) != len(keys):
        return None
    return [value / total for value in values]


def _d3_logits(backbone, readout, sequences, pad_id, sdpa_backends):
    import torch

    width = max(len(sequence) for sequence in sequences)
    device = readout.device
    ids = torch.full((len(sequences), width), pad_id, dtype=torch.long, device=device)
    mask = torch.zeros((len(sequences), width), dtype=torch.long, device=device)
    for index, sequence in enumerate(sequences):
        ids[index, width - len(sequence) :] = torch.as_tensor(
            sequence, dtype=torch.long, device=device
        )
        mask[index, width - len(sequence) :] = 1
    with sdpa_backends(device):
        hidden = backbone(input_ids=ids, attention_mask=mask, use_cache=False).last_hidden_state[
            :, -1
        ]
    return hidden.float() @ readout.T


def _d3_loss(logits, targets):
    import torch

    losses = []
    for index, target in enumerate(targets):
        count = len(target)
        logp = logits[index, :count].log_softmax(0)
        gold = logits.new_tensor(target)
        losses.append(-(gold * logp).sum())
    return torch.stack(losses).mean()


def _export_d3(src: Path, dest: Path, backbone, readout) -> None:
    from safetensors.torch import save_file

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns(
            "*.safetensors",
            "*.mp4",
            "*.png",
            "model.safetensors.index.json",
            "MODEL_MANIFEST.json",
        ),
    )
    state = {key: value.detach().cpu().contiguous() for key, value in backbone.state_dict().items()}
    save_file(state, dest / "model.safetensors")
    save_file({"weight": readout.detach().float().cpu().contiguous()}, dest / "readout.safetensors")


def _predict_clef(payload: dict) -> dict:
    from unsloth import FastDecisionModel
    from unsloth.models.clef import encode_record

    repo = payload["repo"]
    max_context = int(payload["max_context_length"])
    model, processor = _cached(
        ("clef", repo, max_context),
        lambda: _load_clef(repo, max_context),
    )
    # predict() hides its truncated flag and otherwise cuts the prompt at its serve window.
    model.decision_config["max_len"] = max_context
    tokenizer = getattr(processor, "tokenizer", processor)
    encoded = encode_record(
        tokenizer,
        {"state": payload["state"], "questions": payload["questions"]},
        max_length=max_context + 1,
    )
    if len(encoded.input_ids) > max_context:
        return {"error": "max_length_exceeded"}
    answers = FastDecisionModel.predict(model, processor, payload["state"], payload["questions"])
    return {
        "model": repo,
        "answers": answers,
        "usage": {"input_tokens": len(encoded.input_ids), "output_tokens": 0},
    }


def _load_clef(repo: str, max_context: int):
    from unsloth import FastDecisionModel

    model, processor = FastDecisionModel.from_pretrained(
        repo,
        max_seq_length=max_context,
        load_in_4bit=False,
    )
    FastDecisionModel.for_inference(model)
    return model, processor


def _predict_d3(payload: dict) -> dict:
    repo = payload["repo"]
    max_context = int(payload["max_context_length"])
    runtime = _cached(("d3", repo, max_context), lambda: _load_d3(repo, max_context))
    return runtime.system_one(state=payload["state"], questions=payload["questions"])


def _load_d3(repo: str, max_context: int):
    # Before transformers binds Qwen3.5's Gated DeltaNet to the PyTorch reference.
    import unsloth  # noqa: F401
    from huggingface_hub import snapshot_download

    folder = snapshot_download(repo)
    sys.path.insert(0, folder)
    from d3_runtime import D3

    return D3.from_pretrained(
        folder,
        max_length=max_context,
        verify="none",
        device="cuda:0",
        model_name=repo,
    )


def _cached(key, load):
    if _LOADED.get("key") != key or "value" not in _LOADED:
        loaded = load()
        _LOADED.clear()
        _LOADED["key"] = key
        _LOADED["value"] = loaded
    return _LOADED["value"]


def _peak_allocated():
    import torch

    if not torch.cuda.is_available():
        return None
    return round(torch.cuda.max_memory_allocated() / 1024**3, 2)


def _peak_reserved():
    import torch

    if not torch.cuda.is_available():
        return None
    return round(torch.cuda.max_memory_reserved() / 1024**3, 2)
