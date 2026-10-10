"""Native decision optimization on the backbone loaded by the shared engine."""

import hashlib
import io
import json
import math
import os
import random
import socketserver
import tempfile
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
from native_monitor import NativeTrainingMonitor
from training_monitor import preserve_training_state
from transformers import get_cosine_schedule_with_warmup

from modal_shared.decision_artifact import read_artifact, seal_artifact
from modal_shared.decision_batching import microbatches
from modal_shared.decision_checkpoint import restore_resume, save_resume, training_base_identity
from modal_shared.decision_checkpoint_policy import checkpoint_steps, validate_policy
from modal_shared.decision_inference import input_digest
from modal_shared.decisions import (
    TARGET_FIELDS,
    DecisionTokenizer,
    compare_predictions,
    decision_request,
)
from modal_shared.preparation import training_fingerprint
from modal_shared.runtime_profile import representative_order
from modal_shared.serving.artifacts import atomic_json
from modal_shared.training_monitoring import fingerprint, resolve_policy
from modal_shared.training_telemetry import read_telemetry, record_stage

PADDED_TOKEN_BUDGET = int(os.environ.get("PADDED_TOKEN_BUDGET", MAX_LENGTH))


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
def evaluate(model, data, tokenizer, destination, *, stage=None):
    was_training = model.training
    model.eval()
    total_loss = total_weight = correct = count = brier = hard_correct = hard_count = 0.0
    distribution_count = mean_count = mean_error = 0
    if stage:
        record_stage(
            Path(destination).parent, stage, completed=0, total=len(data), unit="decisions"
        )
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
                q = row.get("target_probabilities")
                count += 1
                if q is not None:
                    distribution_count += 1
                    correct += int(
                        max(range(len(p)), key=p.__getitem__)
                        == max(range(len(q)), key=q.__getitem__)
                    )
                    brier += sum((a - b) ** 2 for a, b in zip(p, q, strict=True))
                    if max(q) == 1.0:
                        hard_count += 1
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
        "eval_runtime": time.monotonic() - started,
    }


def train(model, tokenizer):
    if torch.cuda.device_count() != 1:
        raise ValueError("Native decision training currently requires one GPU")
    baseline_setting = os.environ.get("DECISION_PRE_TRAINING_BASELINE", "1")
    if baseline_setting not in {"0", "1"}:
        raise ValueError("DECISION_PRE_TRAINING_BASELINE must be 0 or 1")
    baseline_enabled = baseline_setting == "1"
    data = IndexedRows("data.jsonl")
    if not len(data):
        raise ValueError("No decisions to train")
    validation = IndexedRows("val.jsonl") if Path("val.jsonl").exists() else None
    policy = validate_policy(
        json.loads(
            os.environ.get("DECISION_CHECKPOINT_POLICY", '{"fractions":[1],"selection":"last"}')
        ),
        has_development=validation is not None and len(validation) > 0,
    )
    monitoring_value = (
        json.loads(os.environ["TRAINING_MONITORING"])
        if os.environ.get("TRAINING_MONITORING")
        else {
            "mode": "adaptive" if validation else "off",
            "initial": baseline_enabled,
            "selection": policy["selection"],
        }
    )
    monitoring_policy = resolve_policy(
        monitoring_value, has_development=bool(validation), provider="modal"
    )
    if monitoring_policy["initial"] != baseline_enabled:
        raise ValueError("Monitoring initial check conflicts with the native baseline setting")
    if (
        "DECISION_CHECKPOINT_POLICY" in os.environ
        and monitoring_policy["selection"] != policy["selection"]
    ):
        raise ValueError("Monitoring and native checkpoint selection conflict")
    run = Path(RUN_DIR)
    prepared = json.loads(Path("preparation.json").read_text())
    global_batch = PER_DEVICE_BATCH * GRAD_ACCUM
    steps_per_epoch = math.ceil(len(data) / global_batch)
    total_steps = steps_per_epoch * N_EPOCHS
    if MAX_STEPS:
        total_steps = min(total_steps, MAX_STEPS)
    profile_order, profile_measurement = (
        representative_order(
            data.lengths, samples=total_steps * global_batch, batch_size=global_batch, seed=SEED
        )
        if os.environ.get("RUNTIME_PROFILE")
        else (None, None)
    )
    signature = {
        "runtime_profile": profile_measurement,
        "pre_training_baseline": baseline_enabled,
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
        "checkpoint_policy": policy,
        "monitoring": monitoring_policy,
    }
    if profile_measurement is not None:
        record_stage(run, "profile_selection", runtime_profile=profile_measurement)
    record_stage(run, "initializing_trainer")
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
    callback = ProgressCallback(run)
    resume_path = run / "decision-resume.pt"
    if resume_path.exists():
        step, tokens_seen = restore_resume(
            resume_path, model, optimizer, scheduler, signature=signature
        )
        record_stage(run, "training", restored_step=step, restored_tokens=tokens_seen)
        print(f"Resumed native decision training at step {step}", flush=True)
    else:
        signature_path = run / "decision-training.json"
        signature_path.write_text(json.dumps(signature, indent=2))
        if not baseline_enabled or not validation:
            record_stage(
                run,
                "training",
                pre_training_baseline={
                    "status": "not_requested" if not baseline_enabled else "not_applicable",
                    "reason": "disabled" if not baseline_enabled else "no_development_data",
                },
            )
    callback.set_measured_tokens_per_step(round(sum(data.lengths) / len(data) * PER_DEVICE_BATCH))
    state = SimpleNamespace(global_step=step, max_steps=total_steps, epoch=step / steps_per_epoch)
    args = SimpleNamespace(num_train_epochs=N_EPOCHS)
    callback.on_train_begin(args, state, None, model=model, tokens_seen=tokens_seen)
    device = model.get_input_embeddings().weight.device
    model.train()
    retained_steps = checkpoint_steps(policy, total_steps)
    probe_indices = sorted(range(len(data)), key=lambda i: data.lengths[i])
    selected = sorted({probe_indices[round(i * (len(data) - 1) / 63)] for i in range(64)})
    probe = run / "decision-reload-inputs.jsonl"
    with probe.open("w") as stream:
        for row in data.read(selected):
            stream.write(json.dumps(row) + "\n")
    monitoring = NativeTrainingMonitor(
        run,
        monitoring_policy,
        data,
        validation,
        total_steps=total_steps,
        evaluate=lambda rows, destination: evaluate(
            model, rows, tokenizer, destination, stage="validation"
        ),
        retain=lambda step: retain_checkpoint(
            model, tokenizer, run, probe, None, prepared, signature, step
        ),
        preserve=lambda: preserve_training_state(model),
        attempt=read_telemetry(run).get("attempt", 1),
    )
    monitoring_enabled = monitoring_policy["mode"] != "off"
    if monitoring_enabled and monitoring_policy["initial"] and step == 0:
        check_started = time.monotonic()
        initial = monitoring.check(0)
        callback.exclude_monitoring_time(time.monotonic() - check_started)
        atomic_json(run / "decision-before.json", initial["metrics"])
        callback.on_evaluate(args, state, None, metrics=initial["metrics"])
        record_stage(
            run,
            "initial_validation",
            pre_training_baseline={
                "status": initial["state"],
                "decisions": initial["coverage"]["scored"],
            },
        )
    started = time.monotonic()
    saved_at = started
    epoch = -1
    order = []
    while step < total_steps:
        step_started = time.monotonic()
        next_epoch, epoch_step = divmod(step, steps_per_epoch)
        if next_epoch != epoch:
            epoch = next_epoch
            order = profile_order if profile_order is not None else epoch_order(data, SEED + epoch)
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
        monitoring.monitor.observe_metrics(
            {"loss": step_loss, "grad_norm": norm}, clipping_threshold=1.0
        )
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
        if monitoring_enabled:
            monitoring.monitor.observe_step(time.monotonic() - step_started)
            due = monitoring.monitor.schedule.due(step) or step in retained_steps
            if monitoring_policy["mode"] == "epoch" and step % steps_per_epoch == 0:
                due = monitoring.monitor.schedule.checks < monitoring_policy["max_checks"]
            if due and step < total_steps:
                check_started = time.monotonic()
                check = monitoring.check(step)
                callback.exclude_monitoring_time(time.monotonic() - check_started)
                callback.on_evaluate(args, state, None, metrics=check["metrics"])
                if monitoring.monitor.data["stop_reason"]:
                    break
        elif step in retained_steps:
            check_started = time.monotonic()
            monitoring.retain_at_step(step)
            callback.exclude_monitoring_time(time.monotonic() - check_started)
        if now - saved_at >= 300 or step in retained_steps:
            save_resume(
                resume_path,
                model,
                optimizer,
                scheduler,
                signature=signature,
                step=step,
                tokens_seen=tokens_seen,
            )
            record_stage(
                run,
                "training",
                checkpoint_step=step,
                checkpoint_at=time.time(),
                checkpoint_bytes=resume_path.stat().st_size,
            )
            saved_at = now
    if monitoring_enabled:
        monitoring.check(step)
        chosen = next(
            (item for item in monitoring.monitor.data["checkpoints"] if item.get("selected")), None
        )
        if chosen and monitoring_policy["selection"] == "development_loss":
            read_artifact(run / chosen["manifest"]["path"])
            model.load_adapter(
                str(run / chosen["manifest"]["path"]), adapter_name="default", is_trainable=True
            )
            model.set_adapter("default")
        signature["selected_checkpoint_step"] = chosen["step"] if chosen else step
        if monitoring_policy["final"]:
            final = monitoring.check(signature["selected_checkpoint_step"], final=True)
            atomic_json(run / "decision-after.json", final["metrics"])
            callback.on_evaluate(args, state, None, metrics=final["metrics"])
    record_stage(run, "verifying_checkpoint")
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


def retain_checkpoint(model, tokenizer, run, probe, validation, prepared, signature, step):
    final = run / "checkpoints" / str(step)
    expected_training = {**signature, "selected_checkpoint_step": step}
    if final.exists():
        artifact = read_artifact(final)
        if artifact["training"] != expected_training:
            raise ValueError("A retained checkpoint belongs to another training state")
        record = json.loads((final / "checkpoint-metrics.json").read_text())
        return {
            **record,
            "path": str(final.relative_to(run)),
            "artifact_identity": artifact["identity"],
        }
    final.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=final.parent, prefix="retaining-") as temporary:
        checkpoint = Path(temporary) / "artifact"
        checkpoint.mkdir()
        metrics = (
            evaluate(model, validation, tokenizer, run / f"development-{step}.jsonl")
            if validation and len(validation)
            else {}
        )
        before, after = run / f"reload-{step}-before.jsonl", run / f"reload-{step}-after.jsonl"
        evaluate(model, IndexedRows(probe), tokenizer, before)
        model.save_pretrained(checkpoint)
        tokenizer.save_pretrained(checkpoint)
        atomic_json(
            checkpoint / "decision.json",
            {
                **{
                    key: prepared[key]
                    for key in ("objective", "renderer", "codebook", "vocab_fingerprint")
                },
                "training": {**signature, "selected_checkpoint_step": step},
            },
        )
        try:
            model.load_adapter(str(checkpoint), adapter_name="verification", is_trainable=False)
            model.set_adapter("verification")
            evaluate(model, IndexedRows(probe), tokenizer, after)
            with before.open() as expected, after.open() as restored:
                verification = compare_predictions(
                    (json.loads(line) for line in expected), (json.loads(line) for line in restored)
                )
        finally:
            model.set_adapter("default")
            if "verification" in model.peft_config:
                model.delete_adapter("verification")
        record = {
            "step": step,
            "development_loss": metrics.get("eval_loss"),
            "development_metrics": metrics,
            "reload_verification": verification,
        }
        atomic_json(checkpoint / "checkpoint-metrics.json", record)
        artifact = seal_artifact(checkpoint, verification)
        checkpoint.replace(final)
    return {
        **record,
        "path": str(final.relative_to(run)),
        "artifact_identity": artifact["identity"],
    }


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


def prediction_identity(model):
    base_identity = training_base_identity(os.environ["BASE_MODEL_PATH"], os.environ["MODEL_ID"])
    foundation_path = os.environ.get("DECISION_FOUNDATION_PATH")
    if foundation_path:
        foundation = json.loads(Path(foundation_path).read_text())
        expected = foundation["foundation"]["base_identity"]
        artifact = None
    else:
        artifact = read_artifact(CHECKPOINT_DIR)
        expected = artifact["training"]["base_identity"]
    if base_identity != expected:
        raise ValueError("Prediction base differs from the trained artifact")
    base_only = bool(foundation_path) or os.environ.get("DECISION_BASE_ONLY") == "1"
    if not base_only:
        model.load_adapter(CHECKPOINT_DIR, adapter_name="default", is_trainable=False)
        model.set_adapter("default")
    identity = "base:" + base_identity if base_only else artifact["identity"]
    return identity, base_only


def serve_decisions(model, tokenizer):
    identity, base_only = prediction_identity(model)
    preparation = json.loads(Path("preparation.json").read_text())
    vocab = hashlib.sha256(json.dumps(tokenizer.get_vocab(), sort_keys=True).encode()).hexdigest()
    if vocab != preparation["vocab_fingerprint"]:
        raise ValueError("Performance tokenizer identity changed")
    encoder = DecisionTokenizer(tokenizer, preparation["codebook"])

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
                    if len(row["input_ids"]) > preparation["context_length"]:
                        raise ValueError("Performance input exceeds the qualified context")
                    prepared.append(
                        {**row, "key": str(index), "input_sha256": input_digest(request)}
                    )
                output = io.StringIO()
                with model.disable_adapter() if base_only else nullcontext():
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


def predict_checkpoint(model, tokenizer):
    identity, base_only = prediction_identity(model)
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
