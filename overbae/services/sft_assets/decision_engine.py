"""Native decision optimization on the backbone loaded by the shared engine."""

import hashlib
import json
import math
import os
import random
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import torch
from common import (
    CHECKPOINT_DIR,
    GRAD_ACCUM,
    LEARNING_RATE,
    MAX_LENGTH,
    MAX_STEPS,
    N_EPOCHS,
    PER_DEVICE_BATCH,
    RUN_DIR,
    SEED,
    WARMUP_RATIO,
    WEIGHT_DECAY,
    ProgressCallback,
)
from decision_readout import collate, decision_logits, loss_terms, predict, probability_vectors
from transformers import get_cosine_schedule_with_warmup

from modal_shared.decision_artifact import read_artifact
from modal_shared.decision_batching import microbatches
from modal_shared.decision_checkpoint import restore_resume, save_resume, training_base_identity
from modal_shared.decisions import compare_predictions
from modal_shared.preparation import training_fingerprint

PADDED_TOKEN_BUDGET = int(os.environ.get("PADDED_TOKEN_BUDGET", MAX_LENGTH))


class IndexedRows:
    def __init__(self, path):
        self.path = Path(path)
        self.offsets = []
        self.lengths = []
        self.fingerprint = hashlib.sha256()
        with self.path.open("rb") as stream:
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line:
                    break
                self.fingerprint.update(line)
                row = json.loads(line)
                self.offsets.append(offset)
                self.lengths.append(len(row["input_ids"]))
        self.fingerprint = self.fingerprint.hexdigest()

    def __len__(self):
        return len(self.offsets)

    def read(self, indices):
        with self.path.open("rb") as stream:
            result = []
            for index in indices:
                stream.seek(self.offsets[index])
                result.append(json.loads(stream.readline()))
        return result


def epoch_order(data, seed):
    order = list(range(len(data)))
    random.Random(seed).shuffle(order)
    # Bounded sorting reduces padding without turning the epoch into a length curriculum.
    block = max(2048, PER_DEVICE_BATCH * GRAD_ACCUM)
    for start in range(0, len(order), block):
        order[start : start + block] = sorted(
            order[start : start + block], key=lambda i: data.lengths[i]
        )
    return order


@torch.no_grad()
def evaluate(model, data, tokenizer, destination):
    was_training = model.training
    model.eval()
    total_loss = total_weight = correct = count = 0.0
    device = model.get_input_embeddings().weight.device
    started = time.monotonic()
    with Path(destination).open("w") as stream:
        records = (
            row
            for start in range(0, len(data), PER_DEVICE_BATCH)
            for row in data.read(range(start, min(len(data), start + PER_DEVICE_BATCH)))
        )
        for rows in microbatches(
            records, max_rows=PER_DEVICE_BATCH, max_padded_tokens=PADDED_TOKEN_BUDGET
        ):
            batch = collate(rows, tokenizer.pad_token_id, device)
            logits = decision_logits(model, batch)
            loss, weight = loss_terms(logits, batch)
            total_loss += loss.item()
            total_weight += weight.item()
            for row, vectors in zip(rows, probability_vectors(logits, batch), strict=True):
                p = vectors["probabilities"]
                q = row["target_probabilities"]
                correct += int(
                    max(range(len(p)), key=p.__getitem__) == max(range(len(q)), key=q.__getitem__)
                )
                count += 1
                stream.write(
                    json.dumps(
                        {
                            "key": row["key"],
                            **vectors,
                            "target_probabilities": q,
                            "kind": row["kind"],
                        }
                    )
                    + "\n"
                )
    model.train(was_training)
    return {
        "eval_loss": total_loss / total_weight,
        "decision_accuracy": correct / count,
        "decisions": int(count),
        "eval_runtime": time.monotonic() - started,
    }


def train(model, tokenizer):
    if torch.cuda.device_count() != 1:
        raise ValueError("Native decision training currently requires one GPU")
    data = IndexedRows("data.jsonl")
    if not len(data):
        raise ValueError("No decisions to train")
    validation = IndexedRows("val.jsonl") if Path("val.jsonl").exists() else None
    run = Path(RUN_DIR)
    prepared = json.loads(Path("preparation.json").read_text())
    global_batch = PER_DEVICE_BATCH * GRAD_ACCUM
    steps_per_epoch = math.ceil(len(data) / global_batch)
    total_steps = steps_per_epoch * N_EPOCHS
    if MAX_STEPS:
        total_steps = min(total_steps, MAX_STEPS)
    signature = {
        "runtime_fingerprint": training_fingerprint(Path(__file__).parent),
        "torch_version": str(torch.__version__),
        "cuda_version": torch.version.cuda,
        "base_identity": training_base_identity(
            os.environ["BASE_MODEL_PATH"], os.environ["MODEL_ID"]
        ),
        "train_sha256": data.fingerprint,
        "validation_sha256": validation.fingerprint if validation else None,
        "preparation_sha256": hashlib.sha256(Path("preparation.json").read_bytes()).hexdigest(),
        "global_batch": global_batch,
        "microbatch": PER_DEVICE_BATCH,
        "padded_token_budget": PADDED_TOKEN_BUDGET,
        "epochs": N_EPOCHS,
        "total_steps": total_steps,
        "seed": SEED,
        "model_config": {
            key: os.environ.get(key)
            for key in (
                "MODEL_ID",
                "BASE_MODEL_PATH",
                "TRAINING_TYPE",
                "LORA_R",
                "LORA_ALPHA",
                "LORA_DROPOUT",
                "LORA_TARGET_MODULES",
                "UNSLOTH_IMAGE",
                "LOAD_IN_4BIT",
            )
        },
        "learning_rate": LEARNING_RATE,
        "weight_decay": WEIGHT_DECAY,
        "warmup_ratio": WARMUP_RATIO,
    }
    random.seed(SEED)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    parameters = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(parameters, lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
    scheduler = get_cosine_schedule_with_warmup(
        optimizer,
        num_warmup_steps=math.ceil(total_steps * WARMUP_RATIO),
        num_training_steps=total_steps,
    )
    step = tokens_seen = 0
    resume_path = run / "decision-resume.pt"
    if resume_path.exists():
        step, tokens_seen = restore_resume(
            resume_path, model, optimizer, scheduler, signature=signature
        )
        print(f"Resumed native decision training at step {step}", flush=True)
    else:
        signature_path = run / "decision-training.json"
        signature_path.write_text(json.dumps(signature, indent=2))
        if validation and len(validation):
            (run / "decision-before.json").write_text(
                json.dumps(evaluate(model, validation, tokenizer, run / "decision-before.jsonl"))
            )
    callback = ProgressCallback(run)
    callback.set_measured_tokens_per_step(round(sum(data.lengths) / len(data) * PER_DEVICE_BATCH))
    state = SimpleNamespace(global_step=step, max_steps=total_steps, epoch=step / steps_per_epoch)
    args = SimpleNamespace(num_train_epochs=N_EPOCHS)
    callback.on_train_begin(args, state, None, model=model, tokens_seen=tokens_seen)
    device = model.get_input_embeddings().weight.device
    model.train()
    started = time.monotonic()
    saved_at = started
    epoch = -1
    order = []
    while step < total_steps:
        next_epoch, epoch_step = divmod(step, steps_per_epoch)
        if next_epoch != epoch:
            epoch = next_epoch
            order = epoch_order(data, SEED + epoch)
        indices = order[epoch_step * global_batch : (epoch_step + 1) * global_batch]
        rows = data.read(indices)
        normalizer = sum(row["weight"] for row in rows)
        optimizer.zero_grad(set_to_none=True)
        step_loss = 0.0
        for micro in microbatches(
            rows, max_rows=PER_DEVICE_BATCH, max_padded_tokens=PADDED_TOKEN_BUDGET
        ):
            batch = collate(micro, tokenizer.pad_token_id, device)
            loss, _ = loss_terms(decision_logits(model, batch), batch)
            (loss / normalizer).backward()
            step_loss += loss.detach().item() / normalizer
            tokens_seen += sum(len(row["input_ids"]) for row in micro)
        norm = torch.nn.utils.clip_grad_norm_(parameters, 1.0, error_if_nonfinite=True)
        optimizer.step()
        scheduler.step()
        step += 1
        state.global_step, state.epoch = step, step / steps_per_epoch
        callback.on_log(
            args,
            state,
            None,
            logs={
                "loss": step_loss,
                "grad_norm": norm,
                "learning_rate": scheduler.get_last_lr()[0],
                "num_tokens": tokens_seen,
            },
        )
        now = time.monotonic()
        if now - saved_at >= 300 or step == total_steps:
            save_resume(
                resume_path,
                model,
                optimizer,
                scheduler,
                signature=signature,
                step=step,
                tokens_seen=tokens_seen,
            )
            saved_at = now
    if validation and len(validation):
        metrics = evaluate(model, validation, tokenizer, run / "decision-after.jsonl")
        (run / "decision-after.json").write_text(json.dumps(metrics))
        callback.on_evaluate(args, state, None, metrics=metrics)
    probe_indices = sorted(range(len(data)), key=lambda i: data.lengths[i])
    selected = sorted({probe_indices[round(i * (len(data) - 1) / 63)] for i in range(64)})
    probe = run / "decision-reload-inputs.jsonl"
    with probe.open("w") as stream:
        for row in data.read(selected):
            stream.write(json.dumps(row) + "\n")
    evaluate(model, IndexedRows(probe), tokenizer, run / "decision-reload-reference.jsonl")
    model.save_pretrained(CHECKPOINT_DIR)
    tokenizer.save_pretrained(CHECKPOINT_DIR)
    (Path(CHECKPOINT_DIR) / "decision.json").write_text(
        json.dumps(
            {
                "objective": prepared["objective"],
                "renderer": prepared["renderer"],
                "codebook": prepared["codebook"],
                "vocab_fingerprint": prepared["vocab_fingerprint"],
                "training": signature,
            },
            indent=2,
        )
    )
    callback.on_train_end(args, state, None, model=model)
    callback.emit_final_checkpoint(state, path="checkpoint-final")


def verify_checkpoint(model, tokenizer):
    run = Path(RUN_DIR)
    model.load_adapter(CHECKPOINT_DIR, adapter_name="default", is_trainable=False)
    model.set_adapter("default")
    result = run / "decision-reloaded.jsonl"
    evaluate(model, IndexedRows(run / "decision-reload-inputs.jsonl"), tokenizer, result)
    with (run / "decision-reload-reference.jsonl").open() as before, result.open() as after:
        report = compare_predictions(
            (json.loads(line) for line in before), (json.loads(line) for line in after)
        )
    (run / "decision-reload-verification.json").write_text(json.dumps(report, indent=2))
    print("Native checkpoint reload verified: " + json.dumps(report), flush=True)


def predict_checkpoint(model, tokenizer):
    artifact = read_artifact(CHECKPOINT_DIR)
    base_identity = training_base_identity(os.environ["BASE_MODEL_PATH"], os.environ["MODEL_ID"])
    if base_identity != artifact["training"]["base_identity"]:
        raise ValueError("Prediction base differs from the trained artifact")
    base_only = os.environ.get("DECISION_BASE_ONLY") == "1"
    if not base_only:
        model.load_adapter(CHECKPOINT_DIR, adapter_name="default", is_trainable=False)
        model.set_adapter("default")
    identity = "base:" + base_identity if base_only else artifact["identity"]
    with (
        model.disable_adapter() if base_only else nullcontext(),
        Path(os.environ["DECISION_INPUTS_PATH"]).open() as source,
        Path(os.environ["DECISION_OUTPUT_PATH"]).open("w") as output,
    ):
        count = predict(
            model,
            tokenizer.pad_token_id,
            (json.loads(line) for line in source),
            output,
            identity,
            max_rows=PER_DEVICE_BATCH,
            max_tokens=PADDED_TOKEN_BUDGET,
        )
    print(json.dumps({"predicted_decisions": count, "model_identity": identity}), flush=True)
