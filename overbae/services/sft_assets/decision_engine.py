import hashlib
import io
import json
import math
import os
import socketserver
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from unsloth import DecisionTrainer, FastDecisionModel  # isort: skip

import torch
from common import (
    CHECKPOINT_DIR,
    GRAD_ACCUM,
    LEARNING_RATE,
    LORA_DROPOUT,
    LORA_R,
    MAX_LENGTH,
    MAX_STEPS,
    MODEL_ID,
    N_EPOCHS,
    PER_DEVICE_BATCH,
    RUN_DIR,
    SEED,
    WARMUP_RATIO,
    WEIGHT_DECAY,
    ProgressCallback,
)
from decision_readout import (
    DecisionCollator,
    collate,
    configure_decision_head,
    decision_logits,
    loss_terms,
    predict,
    probability_vectors,
)
from native_monitor import NativeTrainingMonitor
from peft import get_peft_model_state_dict, set_peft_model_state_dict
from safetensors.torch import load_file
from training_monitor import preserve_training_state
from transformers import TrainerCallback, TrainerState, TrainingArguments
from unsloth.models.clef import encode_record

from modal_shared.decision_artifact import read_artifact, seal_artifact
from modal_shared.decision_batching import microbatches
from modal_shared.decision_checkpoint_policy import checkpoint_steps, validate_policy
from modal_shared.decision_encoding import RENDERER, DecisionEncoder
from modal_shared.decision_inference import input_digest
from modal_shared.decisions import TARGET_FIELDS, compare_predictions, decision_request
from modal_shared.preparation import training_fingerprint
from modal_shared.runtime_profile import representative_order
from modal_shared.serving.artifacts import atomic_json, digest_file, read_base_manifest
from modal_shared.training_monitoring import fingerprint, resolve_policy
from modal_shared.training_telemetry import read_telemetry, record_stage

PADDED_TOKEN_BUDGET = int(os.environ.get("PADDED_TOKEN_BUDGET", MAX_LENGTH * PER_DEVICE_BATCH))
ATTENTION_IMPLEMENTATION = "sdpa"


class SupervisedDecisionTrainer(DecisionTrainer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model_accepts_loss_kwargs = True
        retained = {
            value.data_ptr()
            for state in (
                get_peft_model_state_dict(self.model.encoder),
                self.model.head.state_dict(),
            )
            for value in state.values()
        }
        if any(p.requires_grad and p.data_ptr() not in retained for p in self.model.parameters()):
            raise ValueError("Trainable decision weights are absent from the checkpoint")

    def get_batch_samples(self, epoch_iterator, num_batches, device):
        batches, _ = super().get_batch_samples(epoch_iterator, num_batches, device)
        # One denominator across the actual accumulated batch, including the final partial batch.
        return batches, sum(batch["weights"].sum().to(device) for batch in batches)

    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
        logits = decision_logits(model, inputs)
        numerator, weight = loss_terms(logits, inputs)
        loss = numerator / (weight if num_items_in_batch is None else num_items_in_batch)
        return (loss, {"loss": loss, "logits": logits}) if return_outputs else loss

    def reload_model(self, directory):
        directory = Path(directory)
        weights = load_file(str(directory / "adapter_model.safetensors"))
        loaded = set_peft_model_state_dict(self.model.encoder, weights)
        if loaded.unexpected_keys:
            raise ValueError("Decision checkpoint contains unexpected adapter weights")
        head = load_file(str(directory / "joint_head.safetensors"))
        self.model.head.load_state_dict(head, strict=True)
        verify_weights(self.model, directory)


def verify_weights(model, directory):
    directory = Path(directory)
    for filename, restored in (
        ("adapter_model.safetensors", get_peft_model_state_dict(model.encoder)),
        ("joint_head.safetensors", model.head.state_dict()),
    ):
        saved = load_file(str(directory / filename))
        if set(saved) != set(restored) or any(
            not torch.equal(value, restored[key].detach().cpu()) for key, value in saved.items()
        ):
            raise ValueError("Reloaded decision weights differ from the saved checkpoint")


def verification_report(expected, restored):
    return {
        **compare_predictions(expected, restored, tolerance=1e-4),
        "weight_reload": "exact",
        "attention_implementation": ATTENTION_IMPLEMENTATION,
    }


class DecisionProgress(ProgressCallback):
    def on_log(self, args, state, control, logs=None, **kwargs):
        logs = {**(logs or {}), "num_tokens": state.num_input_tokens_seen}
        return super().on_log(args, state, control, logs=logs, **kwargs)


class DecisionCallbacks(TrainerCallback):
    def __init__(self, trainer, monitor, progress, retained_steps):
        self.trainer, self.monitor, self.progress = trainer, monitor, progress
        self.retained_steps = retained_steps
        self.started = self.saved_at = time.monotonic()

    def check(self, state, *, final=False):
        started = time.monotonic()
        check = self.monitor.check(state.global_step, final=final)
        self.progress.exclude_monitoring_time(time.monotonic() - started)
        self.progress.on_evaluate(self.trainer.args, state, None, metrics=check.get("metrics", {}))
        return check

    def on_train_begin(self, args, state, control, **kwargs):
        if state.global_step == 0 and self.monitor.monitor.policy["initial"]:
            if self.monitor.monitor.policy["mode"] != "off":
                check = self.check(state)
                atomic_json(self.monitor.root / "decision-before.json", check.get("metrics", {}))
        else:
            record_stage(
                self.monitor.root,
                "starting_optimizer",
                pre_training_baseline={
                    "status": "not_requested"
                    if not self.monitor.monitor.policy["initial"]
                    else "already_recorded"
                },
            )

    def on_step_begin(self, args, state, control, **kwargs):
        self.started = time.monotonic()
        record_stage(
            self.monitor.root,
            "training",
            completed=state.global_step,
            total=state.max_steps,
            unit="steps",
        )

    def on_step_end(self, args, state, control, **kwargs):
        monitor = self.monitor.monitor
        monitor.observe_step(time.monotonic() - self.started)
        due = monitor.schedule.due(state.global_step) or state.global_step in self.retained_steps
        if (
            monitor.policy["mode"] not in {"off", "epoch"}
            and due
            and state.global_step < state.max_steps
        ):
            self.check(state)
        elif monitor.policy["mode"] == "off" and state.global_step in self.retained_steps:
            self.monitor.retain_at_step(state.global_step)
        if monitor.data["stop_reason"]:
            control.should_training_stop = True
        if time.monotonic() - self.saved_at >= 300 or due or state.global_step == state.max_steps:
            control.should_save = True
            self.saved_at = time.monotonic()
        return control

    def on_epoch_end(self, args, state, control, **kwargs):
        if self.monitor.monitor.policy["mode"] == "epoch" and state.global_step < state.max_steps:
            self.check(state)
            control.should_training_stop = bool(self.monitor.monitor.data["stop_reason"])
        return control

    def on_log(self, args, state, control, logs=None, **kwargs):
        if logs and "loss" in logs:
            self.monitor.monitor.observe_metrics(logs, clipping_threshold=args.max_grad_norm)

    def on_save(self, args, state, control, **kwargs):
        checkpoint = Path(args.output_dir) / f"checkpoint-{state.global_step}"
        atomic_json(
            checkpoint / "overmind-recovery.json",
            {
                "step": state.global_step,
                "signature": json.loads((self.monitor.root / "decision-training.json").read_text()),
                "files": {
                    str(path.relative_to(checkpoint)): {
                        "sha256": digest_file(path),
                        "bytes": path.stat().st_size,
                    }
                    for path in checkpoint.rglob("*")
                    if path.is_file() and path.name != "overmind-recovery.json"
                },
            },
        )
        record_stage(
            self.monitor.root,
            "training",
            checkpoint_step=state.global_step,
            checkpoint_at=time.time(),
        )


class IndexedRows:
    def __init__(self, path):
        self.path = Path(path)
        self.offsets = []
        self.lengths = []
        self.monitoring_rows = []
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
                self.monitoring_rows.append(
                    {
                        "key": row.get("key") or fingerprint(row),
                        "group": row.get("group"),
                        "sha256": fingerprint(row),
                    }
                )
        self.fingerprint = self.fingerprint.hexdigest()

    def __len__(self):
        return len(self.offsets)

    def __getitem__(self, index):
        return self.read([index])[0]

    def read(self, indices):
        with self.path.open("rb") as stream:
            result = []
            for index in indices:
                stream.seek(self.offsets[index])
                result.append(json.loads(stream.readline()))
        return result


@torch.no_grad()
def evaluate(model, data, tokenizer, destination, *, stage=None):
    was_training = model.training
    model.eval()
    total_loss = total_weight = correct = count = brier = hard_correct = hard_count = 0.0
    distribution_count = mean_count = mean_error = cross_entropy = 0
    categorical_families = {}
    if stage:
        record_stage(
            Path(destination).parent, stage, completed=0, total=len(data), unit="decisions"
        )
    device = next(model.parameters()).device
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
                q = row.get("target_probabilities")
                count += 1
                if q is not None:
                    distribution_count += 1
                    cross_entropy -= sum(
                        a * b for a, b in zip(q, vectors["log_probabilities"], strict=True)
                    )
                    correct += int(
                        max(range(len(p)), key=p.__getitem__)
                        == max(range(len(q)), key=q.__getitem__)
                    )
                    brier += sum((a - b) ** 2 for a, b in zip(p, q, strict=True))
                    if max(q) == 1.0:
                        hard_count += 1
                        if row.get("target_semantics") == "categorical_gold":
                            family = {k: row[k] for k in ("question", "kind", "options")}
                            key = fingerprint(family)
                            size = len(p)
                            fact = categorical_families.setdefault(
                                key,
                                {
                                    **family,
                                    "expected": 0,
                                    "scored": 0,
                                    "support": [0] * size,
                                    "predicted": [0] * size,
                                    "confusion_matrix": [[0] * size for _ in range(size)],
                                },
                            )
                            actual, predicted = q.index(1), max(range(size), key=p.__getitem__)
                            fact["expected"] += 1
                            fact["scored"] += 1
                            fact["support"][actual] += 1
                            fact["predicted"][predicted] += 1
                            fact["confusion_matrix"][actual][predicted] += 1
                        hard_correct += int(q[max(range(len(p)), key=p.__getitem__)] == 1)
                else:
                    mean_count += 1
                    estimate = math.fsum(
                        a * b for a, b in zip(p, row["option_values"], strict=True)
                    )
                    mean_error += abs(estimate - row["target_mean"])
                stream.write(
                    json.dumps(
                        {
                            "key": row["key"],
                            **vectors,
                            **{key: row[key] for key in TARGET_FIELDS if key in row},
                            "weight": row.get("weight", 1.0),
                            "kind": row["kind"],
                            "question": row["question"],
                            "options": row["options"],
                        }
                    )
                    + "\n"
                )
            if stage:
                record_stage(
                    Path(destination).parent,
                    stage,
                    completed=int(count),
                    total=len(data),
                    unit="decisions",
                )
    model.train(was_training)
    return {
        "eval_loss": total_loss / total_weight,
        "argmax_target_agreement": correct / distribution_count if distribution_count else None,
        "hard_label_accuracy": hard_correct / hard_count if hard_count else None,
        "hard_label_decisions": int(hard_count),
        "brier": brier / distribution_count if distribution_count else None,
        "distribution_decisions": distribution_count,
        "mean_decisions": mean_count,
        "expected_score_mae": mean_error / mean_count if mean_count else None,
        "decisions": int(count),
        "cross_entropy": cross_entropy / distribution_count if distribution_count else None,
        "categorical_families": categorical_families,
        "eval_runtime": time.monotonic() - started,
    }


def train(model, tokenizer):
    if torch.cuda.device_count() != 1:
        raise ValueError("Decision training requires one GPU")
    run = Path(RUN_DIR)
    run.mkdir(parents=True, exist_ok=True)
    data = IndexedRows("data.jsonl")
    validation = IndexedRows("val.jsonl") if Path("val.jsonl").exists() else None
    if not len(data):
        raise ValueError("No decisions to train")
    baseline = os.environ.get("DECISION_PRE_TRAINING_BASELINE", "1")
    if baseline not in {"0", "1"}:
        raise ValueError("DECISION_PRE_TRAINING_BASELINE must be 0 or 1")
    checkpoint_policy = validate_policy(
        json.loads(
            os.environ.get("DECISION_CHECKPOINT_POLICY", '{"fractions":[1],"selection":"last"}')
        ),
        has_development=bool(validation),
    )
    policy = resolve_policy(
        json.loads(
            os.environ.get(
                "TRAINING_MONITORING",
                json.dumps(
                    {
                        "mode": "adaptive" if validation else "off",
                        "initial": baseline == "1",
                        "selection": checkpoint_policy["selection"],
                    }
                ),
            )
        ),
        has_development=bool(validation),
        provider="modal",
    )
    if policy["initial"] != (baseline == "1"):
        raise ValueError("Monitoring initial check conflicts with baseline setting")
    if (
        "DECISION_CHECKPOINT_POLICY" in os.environ
        and policy["selection"] != checkpoint_policy["selection"]
    ):
        raise ValueError("Monitoring and checkpoint selection conflict")
    prepared = json.loads(Path("preparation.json").read_text())
    global_batch = PER_DEVICE_BATCH * GRAD_ACCUM
    total_steps = math.ceil(len(data) / global_batch) * N_EPOCHS
    total_steps = min(total_steps, MAX_STEPS) if MAX_STEPS else total_steps
    profile_order, profile_measurement = (
        representative_order(
            data.lengths, samples=total_steps * global_batch, batch_size=global_batch, seed=SEED
        )
        if os.environ.get("RUNTIME_PROFILE")
        else (None, None)
    )
    base = read_base_manifest(Path(os.environ["BASE_MODEL_PATH"]))
    if base["repo"] != MODEL_ID:
        raise ValueError("Training base repository differs from its immutable identity")
    signature = {
        "runtime_profile": profile_measurement,
        "runtime_fingerprint": training_fingerprint(Path(__file__).parent),
        "base_identity": base["identity"],
        "train_sha256": data.fingerprint,
        "validation_sha256": validation.fingerprint if validation else None,
        "preparation_sha256": hashlib.sha256(Path("preparation.json").read_bytes()).hexdigest(),
        "global_batch": global_batch,
        "microbatch": PER_DEVICE_BATCH,
        "padded_token_budget": PADDED_TOKEN_BUDGET,
        "epochs": N_EPOCHS,
        "total_steps": total_steps,
        "seed": SEED,
        "pre_training_baseline": baseline == "1",
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
                "DECISION_HEAD_WIDTH",
            )
        },
        "trainer": "unsloth.DecisionTrainer",
        "encoder": RENDERER,
        "attention_implementation": ATTENTION_IMPLEMENTATION,
        "head_config": model.head.config,
        "head_initialization_seed": SEED,
        "learning_rate": LEARNING_RATE,
        "head_learning_rate": float(os.environ.get("DECISION_HEAD_LEARNING_RATE", "0.0001")),
        "weight_decay": WEIGHT_DECAY,
        "warmup_ratio": WARMUP_RATIO,
        "checkpoint_policy": checkpoint_policy,
        "monitoring": policy,
    }
    signature_path = run / "decision-training.json"
    if signature_path.exists() and json.loads(signature_path.read_text()) != signature:
        raise ValueError("Recovery belongs to a different base/data/recipe identity")
    atomic_json(signature_path, signature)
    progress = DecisionProgress(run)
    progress.set_measured_tokens_per_step(round(sum(data.lengths) / len(data) * PER_DEVICE_BATCH))
    record_stage(run, "initializing_trainer")
    trainer = SupervisedDecisionTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=torch.utils.data.Subset(data, profile_order)
        if profile_order is not None
        else data,
        data_collator=DecisionCollator(tokenizer.pad_token_id),
        callbacks=[progress],
        head_learning_rate=signature["head_learning_rate"],
        label_smoothing=0.0,
        brier_weight=0.0,
        ordinal_weight=0.0,
        kl_weight=0.0,
        permute_fields=False,
        args=TrainingArguments(
            output_dir=str(run / "recovery"),
            max_steps=total_steps,
            num_train_epochs=N_EPOCHS,
            per_device_train_batch_size=PER_DEVICE_BATCH,
            per_device_eval_batch_size=PER_DEVICE_BATCH,
            gradient_accumulation_steps=GRAD_ACCUM,
            learning_rate=LEARNING_RATE,
            weight_decay=WEIGHT_DECAY,
            warmup_steps=math.ceil(total_steps * WARMUP_RATIO),
            lr_scheduler_type="cosine",
            seed=SEED,
            data_seed=SEED,
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=1,
            logging_nan_inf_filter=False,
            report_to=[],
            save_strategy="no",
            eval_strategy="no",
            save_total_limit=2,
            remove_unused_columns=False,
            include_num_input_tokens_seen="non_padding",
            dataloader_num_workers=0,
        ),
    )
    probe = run / "decision-reload-inputs.jsonl"
    order = sorted(range(len(data)), key=data.lengths.__getitem__)
    indices = sorted({order[round(i * (len(data) - 1) / 31)] for i in range(32)})
    probe.write_text("".join(json.dumps(r) + "\n" for r in data.read(indices)))
    monitor = NativeTrainingMonitor(
        run,
        policy,
        data,
        validation,
        total_steps=total_steps,
        evaluate=lambda rows, destination: evaluate(
            model, rows, tokenizer, destination, stage="validation"
        ),
        retain=lambda step: retain_checkpoint(
            trainer, tokenizer, run, probe, prepared, signature, step
        ),
        preserve=lambda: preserve_training_state(model),
        attempt=read_telemetry(run).get("attempt", 1),
    )
    callback = DecisionCallbacks(
        trainer, monitor, progress, checkpoint_steps(checkpoint_policy, total_steps)
    )
    trainer.add_callback(callback)
    completed = sorted(
        (run / "recovery").glob("checkpoint-*/overmind-recovery.json"),
        key=lambda path: int(path.parent.name.split("-")[-1]),
    )
    recovery = str(completed[-1].parent) if completed else None
    if completed:
        receipt = json.loads(completed[-1].read_text())
        if receipt["signature"] != signature:
            raise ValueError("Recovery signature differs")
        for name, expected in receipt["files"].items():
            path = Path(recovery) / name
            if not path.resolve().is_relative_to(Path(recovery).resolve()) or path.is_symlink():
                raise ValueError("Recovery file lies outside its checkpoint")
            if path.stat().st_size != expected["bytes"] or digest_file(path) != expected["sha256"]:
                raise ValueError("Recovery checkpoint bytes changed")
    FastDecisionModel.for_training(model)
    if recovery and receipt["step"] >= total_steps:
        trainer.reload_model(recovery)
        trainer.state = TrainerState.load_from_json(str(Path(recovery) / "trainer_state.json"))
    else:
        trainer.train(resume_from_checkpoint=recovery)
    if policy["mode"] != "off":
        callback.check(trainer.state)
        selected = next(
            (item for item in monitor.monitor.data["checkpoints"] if item.get("selected")), None
        )
        if selected and policy["selection"] == "development_loss":
            path = run / selected["manifest"]["path"]
            read_artifact(path)
            trainer.reload_model(path)
        selected_step = (
            selected["step"]
            if selected and policy["selection"] == "development_loss"
            else trainer.state.global_step
        )
        if policy["final"]:
            final = callback.check(replace(trainer.state, global_step=selected_step), final=True)
            atomic_json(run / "decision-after.json", final.get("metrics", {}))
        signature = {**signature, "selected_checkpoint_step": selected_step}
    record_stage(run, "verifying_checkpoint")
    with preserve_training_state(model):
        evaluate(model, IndexedRows(probe), tokenizer, run / "decision-reload-reference.jsonl")
    trainer.save_model(CHECKPOINT_DIR)
    write_contract(Path(CHECKPOINT_DIR), prepared, signature)
    progress.emit_final_checkpoint(trainer.state, path="checkpoint-final")


def write_contract(directory, prepared, signature):
    atomic_json(
        directory / "decision.json",
        {
            "objective": prepared["objective"],
            "renderer": RENDERER,
            "vocab_fingerprint": prepared["vocab_fingerprint"],
            "training": signature,
        },
    )


def retain_checkpoint(trainer, tokenizer, run, probe, prepared, signature, step):
    final = run / "checkpoints" / str(step)
    signature = {**signature, "selected_checkpoint_step": step}
    if final.exists():
        artifact = read_artifact(final)
        if artifact["training"] != signature:
            raise ValueError("Retained checkpoint belongs to another state")
        return {
            "path": str(final.relative_to(run)),
            "artifact_identity": artifact["identity"],
            "reload_verification": artifact["verification"],
        }
    final.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=final.parent, prefix="retaining-") as temp:
        checkpoint = Path(temp) / "artifact"
        before, after = Path(temp) / "before.jsonl", Path(temp) / "after.jsonl"
        evaluate(
            trainer.model, IndexedRows(probe), tokenizer, before, stage="checkpoint_validation"
        )
        trainer.save_model(checkpoint)
        write_contract(checkpoint, prepared, signature)
        trainer.reload_model(checkpoint)
        evaluate(trainer.model, IndexedRows(probe), tokenizer, after, stage="checkpoint_reload")
        verification = verification_report(
            map(json.loads, before.read_text().splitlines()),
            map(json.loads, after.read_text().splitlines()),
        )
        artifact = seal_artifact(checkpoint, verification)
        checkpoint.replace(final)
    return {
        "path": str(final.relative_to(run)),
        "artifact_identity": artifact["identity"],
        "reload_verification": verification,
    }


def verify_checkpoint(model, tokenizer):
    run = Path(RUN_DIR)
    verify_weights(model, CHECKPOINT_DIR)
    result = run / "decision-reloaded.jsonl"
    evaluate(
        model,
        IndexedRows(run / "decision-reload-inputs.jsonl"),
        tokenizer,
        result,
        stage="checkpoint_reload",
    )
    report = verification_report(
        map(json.loads, (run / "decision-reload-reference.jsonl").read_text().splitlines()),
        map(json.loads, result.read_text().splitlines()),
    )
    atomic_json(run / "decision-reload-verification.json", report)


def prediction_identity():
    base = read_base_manifest(Path(os.environ["BASE_MODEL_PATH"]))
    if os.environ.get("DECISION_FOUNDATION_PATH") or os.environ.get("DECISION_BASE_ONLY") == "1":
        return "base:" + base["identity"]
    return read_artifact(CHECKPOINT_DIR)["identity"]


def serve_decisions(model, tokenizer):
    identity = prediction_identity()
    encoder = DecisionEncoder(tokenizer, encode_record)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                requests = json.loads(self.rfile.readline(32 * 1024 * 1024))
                if not isinstance(requests, list) or not 1 <= len(requests) <= 16:
                    raise ValueError("A performance request contains one to sixteen decisions")
                prepared = []
                for index, request in enumerate(requests):
                    decision_request(request)
                    row = encoder.request(request)
                    if len(row["input_ids"]) > MAX_LENGTH:
                        raise ValueError("Performance input exceeds the qualified context")
                    prepared.append(
                        {**row, "key": str(index), "input_sha256": input_digest(request)}
                    )
                output = io.StringIO()
                predict(
                    model,
                    tokenizer.pad_token_id,
                    prepared,
                    output,
                    identity,
                    max_rows=PER_DEVICE_BATCH,
                    max_tokens=PADDED_TOKEN_BUDGET,
                )
                result = {
                    "predictions": [json.loads(line) for line in output.getvalue().splitlines()],
                    "input_tokens": [len(row["input_ids"]) for row in prepared],
                    "model_identity": identity,
                }
            except Exception as exc:
                result = {"error": type(exc).__name__}
            self.wfile.write(json.dumps(result).encode() + b"\n")

    with socketserver.UnixStreamServer(os.environ["DECISION_SERVICE_SOCKET"], Handler) as server:
        server.serve_forever()


def main():
    run = Path(RUN_DIR)
    run.mkdir(parents=True, exist_ok=True)
    inference = bool(
        os.environ.get("DECISION_INPUTS_PATH") or os.environ.get("DECISION_SERVICE_SOCKET")
    )
    verify = os.environ.get("DECISION_VERIFY_CHECKPOINT") == "1"
    base_only = (
        bool(os.environ.get("DECISION_FOUNDATION_PATH"))
        or os.environ.get("DECISION_BASE_ONLY") == "1"
    )
    source = (
        CHECKPOINT_DIR if verify or (inference and not base_only) else os.environ["BASE_MODEL_PATH"]
    )
    record_stage(run, "loading_model")
    model, processor = FastDecisionModel.from_pretrained(
        source,
        max_seq_length=MAX_LENGTH,
        random_state=SEED,
        load_in_4bit=os.environ.get("LOAD_IN_4BIT") == "1",
        local_files_only=True,
        # Trainer cleanup propagates the parent attention setting into nested text configs.
        attn_implementation=ATTENTION_IMPLEMENTATION,
        use_gradient_checkpointing="unsloth" if not inference and not verify else False,
        **(
            {"head_width": int(os.environ["DECISION_HEAD_WIDTH"])}
            if os.environ.get("DECISION_HEAD_WIDTH") and source != CHECKPOINT_DIR
            else {}
        ),
    )
    if any(
        config._attn_implementation != ATTENTION_IMPLEMENTATION
        for config in (model.encoder.config, model.encoder.config.get_text_config())
    ):
        raise ValueError("Decision attention backend differs from the pinned runtime")
    configure_decision_head(model)
    tokenizer = getattr(processor, "tokenizer", processor)
    prepared = json.loads(Path("preparation.json").read_text())
    record_stage(run, "verifying_training_tokenizer")
    vocab = hashlib.sha256(json.dumps(tokenizer.get_vocab(), sort_keys=True).encode()).hexdigest()
    if vocab != prepared["vocab_fingerprint"] or prepared["context_length"] != MAX_LENGTH:
        raise ValueError("Decision tokenizer/context differs from the prepared artifact")
    if prepared["renderer"] != RENDERER:
        raise ValueError("Decision encoder differs from the prepared artifact")
    if verify:
        verify_checkpoint(model, tokenizer)
    elif inference:
        if not base_only:
            verify_weights(model, CHECKPOINT_DIR)
        FastDecisionModel.for_inference(model)
        if os.environ.get("DECISION_SERVICE_SOCKET"):
            serve_decisions(model, tokenizer)
        else:
            with (
                Path(os.environ["DECISION_INPUTS_PATH"]).open() as src,
                Path(os.environ["DECISION_OUTPUT_PATH"]).open("w") as out,
            ):
                predict(
                    model,
                    tokenizer.pad_token_id,
                    map(json.loads, src),
                    out,
                    prediction_identity(),
                    max_rows=PER_DEVICE_BATCH,
                    max_tokens=PADDED_TOKEN_BUDGET,
                )
    else:
        record_stage(run, "configuring_adapters")
        targets = os.environ.get(
            "LORA_TARGET_MODULES", "q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj"
        )
        model = FastDecisionModel.get_peft_model(
            model,
            r=LORA_R,
            lora_alpha=int(os.environ.get("LORA_ALPHA", 2 * LORA_R)),
            lora_dropout=LORA_DROPOUT,
            random_state=SEED,
            target_modules=targets if targets == "all-linear" else targets.split(","),
        )
        record_stage(run, "loading_training_dataset")
        train(model, tokenizer)
