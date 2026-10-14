"""Decision-model training. Imported inside the Modal image, not by Django."""

from __future__ import annotations

import json
import random
import shutil
import sys
import time
from pathlib import Path

SEED = 3407


def _error(code: str, message: str, skipped: dict | None = None) -> dict:
    error = {"code": code, "message": message}
    if skipped is not None:
        error["skipped"] = skipped
    return {"error": error}


def train(payload: dict) -> dict:
    family = payload["family"]
    if family == "clef":
        return _train_clef(payload)
    if family == "d3":
        return _train_d3(payload)
    if family == "d1":
        return _train_d1(payload)
    return _error("runtime_error", f"unknown decision family: {family}")


def _train_clef(payload: dict) -> dict:
    import math

    import torch
    from transformers import TrainingArguments
    from unsloth import DecisionTrainer, FastDecisionModel

    repo = payload["repo"]
    max_context = int(payload["max_context_length"])
    out = Path("/artifacts") / payload["job_id"]
    folder, _fetched = _cached(repo)
    model, processor = FastDecisionModel.from_pretrained(
        str(folder),
        max_seq_length=max_context,
        load_in_4bit=False,
        local_files_only=True,
        random_state=SEED,
    )
    items, report = FastDecisionModel.build_dataset(payload["records"], processor, model)
    train_items = [
        item
        for item in items
        if not (isinstance(item, dict) and len(item.get("input_ids") or []) >= max_context)
    ]
    rejected = report.get("skipped")
    skipped = {
        "too_long": len(items) - len(train_items),
        "rejected": rejected if isinstance(rejected, int) else 0,
    }
    if not train_items:
        return _error("no_records_fit", "No records fit the context cap.", skipped)
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
            gradient_checkpointing=True,
            logging_steps=10,
            report_to="none",
            save_strategy="no",
            seed=SEED,
        ),
    )
    trained = trainer.train()
    losses = [row["loss"] for row in trainer.state.log_history if "loss" in row]
    calibration = FastDecisionModel.calibrate(model, processor, holdout) if holdout else {}
    saved = out / "adapter"
    trainer.save_model(str(saved))
    _pin_adapter_base(saved, repo)
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
    from peft import get_peft_model

    repo = payload["repo"]
    max_context = int(payload["max_context_length"])
    folder, _fetched = _cached(repo)
    sys.path.insert(0, str(folder))
    from d3_runtime import D3, sdpa_backends

    runtime = D3.from_pretrained(str(folder), max_length=max_context, verify="none")
    runtime.fast = None
    examples, skipped = _d3_examples(runtime, payload["records"], max_context)
    if not examples:
        return _error("no_records_fit", "No records fit the context cap.", skipped)
    backbone = get_peft_model(runtime.backbone, _d3_lora())
    for name, param in backbone.named_parameters():
        if "visual" in name:
            param.requires_grad_(False)
    # ponytail: 26B batch-2 activations exceed one H100. Recompute them; batch stays 2.
    backbone.enable_input_require_grads()
    backbone.gradient_checkpointing_enable()
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
    saved = Path("/artifacts") / payload["job_id"] / "checkpoint"
    _export_d3(folder, saved, merged, readout)
    return {
        "model": repo,
        "family": "d3",
        "kernels": runtime.kernels,
        "train_questions": len(examples),
        "skipped_questions": skipped["too_long"] + skipped["rejected"],
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


def _train_d1(payload: dict) -> dict:
    import torch
    from peft import get_peft_model
    from transformers import AutoModel

    repo = payload["repo"]
    max_context = int(payload["max_context_length"])
    folder, _fetched = _cached(repo)
    model = AutoModel.from_pretrained(
        folder, trust_remote_code=True, local_files_only=True, dtype=torch.float32
    ).to("cuda")
    omni = type(model).__name__ == "D1OmniModel"
    examples, skipped = (
        _omni_examples(model, payload["records"], max_context)
        if omni
        else _slot_examples(model, payload["records"], max_context)
    )
    if not examples:
        return _error("no_records_fit", "No records fit the context cap.", skipped)
    if omni:
        wrapped = get_peft_model(model, _omni_lora())
        inner = wrapped.base_model.model
        for parameter in inner.head.parameters():
            parameter.requires_grad_(True)
        for parameter in list(inner.vision.parameters()) + list(inner.audio.parameters()):
            parameter.requires_grad_(False)
        batch, accum, serve_dtype = 4, 4, torch.float16
    else:
        wrapped = get_peft_model(model, _slot_lora(model))
        inner = wrapped.base_model.model
        for name, parameter in inner.named_parameters():
            if "vision" in name or "visual" in name:
                parameter.requires_grad_(False)
        batch, accum, serve_dtype = 1, 8, torch.bfloat16
    trainable = [parameter for parameter in wrapped.parameters() if parameter.requires_grad]
    head = (
        [parameter for parameter in trainable if parameter in set(inner.head.parameters())]
        if omni
        else []
    )
    body = [parameter for parameter in trainable if parameter not in set(head)]
    optimizer = torch.optim.AdamW(
        [
            {"params": body, "lr": 1e-4, "weight_decay": 0.01},
            {"params": head, "lr": 3e-4, "weight_decay": 0.0},
        ]
        if head
        else [{"params": body, "lr": 1e-4, "weight_decay": 0.01}]
    )
    rng = random.Random(SEED)
    rng.shuffle(examples)
    steps = max(1, (len(examples) + batch * accum - 1) // (batch * accum))
    wrapped.train()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    losses = []
    cursor = 0
    for _ in range(steps):
        optimizer.zero_grad(set_to_none=True)
        accumulated = 0.0
        for _ in range(accum):
            chunk = [examples[(cursor + offset) % len(examples)] for offset in range(batch)]
            cursor += batch
            loss = _omni_loss(inner, chunk) if omni else _slot_loss(inner, chunk)
            (loss / accum).backward()
            accumulated += float(loss.detach())
        torch.nn.utils.clip_grad_norm_(trainable, 1.0)
        optimizer.step()
        losses.append(accumulated / accum)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    merged = wrapped.merge_and_unload()
    saved = Path("/artifacts") / payload["job_id"] / "checkpoint"
    _export_d1(folder, merged, saved, serve_dtype)
    return {
        "model": repo,
        "family": "d1",
        "variant": "omni" if omni else "causal",
        "train_questions": len(examples),
        "skipped_questions": skipped["too_long"] + skipped["rejected"],
        "steps": steps,
        "rank": 16,
        "batch": batch,
        "grad_accum": accum,
        "max_context_length": max_context,
        "train_runtime_s": round(time.perf_counter() - started, 1),
        "peak_allocated_gb": _peak_allocated(),
        "peak_reserved_gb": _peak_reserved(),
        "loss_first_last": [round(losses[0], 4), round(losses[-1], 4)],
        "saved": str(saved),
    }


def _omni_lora():
    from peft import LoraConfig

    return LoraConfig(
        r=16,
        lora_alpha=16,
        lora_dropout=0.0,
        bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "out_proj", "in_proj", "w1", "w2", "w3"],
    )


def _slot_lora(model):
    import re

    import torch
    from peft import LoraConfig

    names = [
        name
        for name, module in model.named_modules()
        if isinstance(module, torch.nn.Linear)
        and "vision" not in name
        and "visual" not in name
        and not name.endswith("lm_head")
    ]
    if not names:
        raise RuntimeError("d1-3B has no language projections to train")
    return LoraConfig(
        r=16,
        lora_alpha=16,
        lora_dropout=0.0,
        bias="none",
        target_modules="|".join(re.escape(name) for name in names),
    )


def _option_keys(question: dict) -> list[str]:
    if question["type"] == "noul":
        return ["true", "false"]
    if question["type"] == "choice":
        return [str(key) for key in question["criteria"]]
    return [str(index) for index in range(len(question["criteria"]))]


def _gold_vector(question: dict, gold: dict, *, flip_noul: bool) -> list[float] | None:
    probs = gold.get("probabilities") if isinstance(gold, dict) else None
    if not isinstance(probs, dict):
        return None
    values = [float(probs.get(key, 0.0)) for key in _option_keys(question)]
    # The omni trunk scores a noul as [false, true]. Platform gold is [true, false].
    if flip_noul and question["type"] == "noul":
        values = [values[1], values[0]]
    total = sum(values)
    if total <= 0:
        return None
    return [value / total for value in values]


def _omni_examples(model, records, max_context):
    import importlib

    module = importlib.import_module(type(model).__module__)
    tokenizer = model.tokenizer
    examples, skipped = [], {"too_long": 0, "rejected": 0}
    for row in records:
        for key, question in row["questions"].items():
            try:
                parsed = module.as_question(question)
            except ValueError:
                skipped["rejected"] += 1
                continue
            try:
                ids, markers = module.encode(tokenizer, row["state"], parsed, max_context)
            except ValueError:
                skipped["too_long"] += 1
                continue
            if len(ids) > max_context:
                skipped["too_long"] += 1
                continue
            target = _gold_vector(question, (row.get("gold") or {}).get(key) or {}, flip_noul=True)
            if target is None or len(target) != len(markers):
                skipped["rejected"] += 1
                continue
            examples.append((ids, markers, module.QTYPES[parsed.type], target))
    return examples, skipped


def _slot_examples(model, records, max_context):
    import importlib

    engine = model.engine
    prompt = importlib.import_module(type(engine).__module__.rsplit(".", 1)[0] + ".prompt")
    examples, skipped = [], {"too_long": 0, "rejected": 0}
    for row in records:
        for key, question in row["questions"].items():
            parsed = prompt.as_question(question)
            text = engine.render(row["state"], parsed)
            ids = engine.tokenizer.encode(text, add_special_tokens=False)
            if len(ids) > max_context:
                skipped["too_long"] += 1
                continue
            groups = prompt.readout_ids(engine.tokenizer, parsed)
            target = _gold_vector(question, (row.get("gold") or {}).get(key) or {}, flip_noul=False)
            if target is None or len(target) != len(groups):
                skipped["rejected"] += 1
                continue
            examples.append((ids, groups, target))
    return examples, skipped


def _omni_loss(model, batch):
    import torch

    device = model.device
    seqs = [model.encoder.embed_tokens(torch.tensor(ids, device=device)) for ids, *_ in batch]
    hidden = torch.nn.utils.rnn.pad_sequence(seqs, batch_first=True)
    lengths = torch.tensor([seq.shape[0] for seq in seqs], device=device)
    pad = torch.arange(hidden.shape[1], device=device)[None] < lengths[:, None]
    hidden = model.encoder(hidden, pad, torch.zeros(len(batch), dtype=torch.long, device=device))
    width = max(len(markers) for _, markers, *_ in batch)
    marker_pos = torch.zeros(len(batch), width, dtype=torch.long, device=device)
    marker_mask = torch.zeros(len(batch), width, dtype=torch.bool, device=device)
    for index, (_, markers, *_) in enumerate(batch):
        marker_pos[index, : len(markers)] = torch.tensor(markers, device=device)
        marker_mask[index, : len(markers)] = True
    qtype = torch.tensor([item[2] for item in batch], device=device)
    logits = model.head(hidden, pad, marker_pos, marker_mask, qtype)
    return _distribution_loss(logits, [item[3] for item in batch])


def _slot_loss(model, batch):
    import torch

    device = next(model.parameters()).device
    losses = []
    for ids, groups, gold in batch:
        tokens = torch.tensor([ids], device=device)
        try:
            logits = model(input_ids=tokens, use_cache=False, logits_to_keep=1).logits[0, -1]
        except TypeError:
            logits = model(input_ids=tokens, use_cache=False).logits[0, -1]
        scores = torch.stack([logits.float()[group].max() for group in groups])
        losses.append(_distribution_loss(scores[None], [gold]))
    return torch.stack(losses).mean()


def _distribution_loss(logits, targets):
    import torch

    losses = []
    for index, target in enumerate(targets):
        logp = logits[index, : len(target)].log_softmax(0)
        losses.append(-(logp.new_tensor(target) * logp).sum())
    return torch.stack(losses).mean()


def _export_d1(src: Path, model, dest: Path, dtype) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    model.to(dtype=dtype).save_pretrained(dest)
    keep = {
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
        "processor_config.json",
        "preprocessor_config.json",
    }
    for path in src.iterdir():
        if path.suffix == ".py" or path.name in keep:
            shutil.copy(path, dest / path.name)


def _cached(repo: str):
    import decision_cache

    return decision_cache.ensure(repo)


def _pin_adapter_base(saved: Path, repo: str) -> None:
    cfg_path = saved / "adapter_config.json"
    if not cfg_path.is_file():
        raise RuntimeError("Clef training did not write a LoRA adapter")
    cfg = json.loads(cfg_path.read_text())
    if cfg.get("base_model_name_or_path") != repo:
        cfg["base_model_name_or_path"] = repo
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")


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
    skipped = {"too_long": 0, "rejected": 0}
    for row in records:
        prepared = runtime.prepare(row["state"], row["questions"])
        gold = row.get("gold") or {}
        for key, sequence in prepared.sequences.items():
            if len(sequence) > max_context:
                skipped["too_long"] += 1
                continue
            target = _target_vector(prepared.questions[key], gold.get(key))
            if target is None:
                skipped["rejected"] += 1
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
